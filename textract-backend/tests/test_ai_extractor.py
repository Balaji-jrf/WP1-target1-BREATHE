import io
import json
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError

from ai_extractor import (
    build_system_prompt, clear_generated_records, extract_chunk,
    extract_chunks_sequential, get_model_config, load_active_taxonomy,
    page_chunks, validate_entities,
)
from dynamo_ingestion import build_entities


TAXONOMY = [{"entity_key": "CLIMATE_METRICS", "display_name": "Climate", "is_active": True}]
FACT = "Rainfall records indicate a severe seasonal drought in Bidar district."
SOURCE_TEXT = (
    "Rainfall records indicate a severe seasonal drought in Bidar district "
    "during 1934, reducing harvests and affecting local communities."
)
SOURCE_EXCERPT = (
    "Rainfall records indicate a severe seasonal drought in Bidar district "
    "during 1934"
)


def bedrock_response(fact=FACT, source_excerpt=SOURCE_EXCERPT, extra=""):
    return (
        '[{"theme":"CLIMATE_METRICS","extracted_fact":' + json.dumps(fact)
        + ',"source_excerpt":' + json.dumps(source_excerpt)
        + ',"confidence_score":0.9' + extra + '}]'
    )


class FakeBedrock:
    def __init__(self, text):
        self.text = text
        self.request = None

    def converse(self, **kwargs):
        self.request = kwargs
        return {"output": {"message": {"content": [{"text": self.text}]}}}


class ThrottlingBedrock(FakeBedrock):
    def __init__(self, text):
        super().__init__(text)
        self.calls = 0

    def converse(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
        return super().converse(**kwargs)


class PagedTaxonomyTable:
    def __init__(self):
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs:
            return {
                "Items": [
                    {
                        "entity_key": "LOCAL_CUSTOM",
                        "display_name": "Inactive custom entry",
                        "is_active": False,
                    },
                    {
                        "entity_key": "PUBLIC_HEALTH",
                        "display_name": "Health & Sanitation",
                        "description": "Health impacts",
                        "keywords": ["cholera"],
                        "extraction_guidance": "Name affected places.",
                        "is_active": True,
                    },
                ],
                "LastEvaluatedKey": {"entity_key": "PUBLIC_HEALTH"},
            }
        return {
            "Items": [
                {
                    "entity_key": "CLIMATE_METRICS",
                    "display_name": "Climate & Heat",
                    "description": "Climate trends",
                    "keywords": ["rainfall"],
                    "extraction_guidance": "Preserve measurements.",
                    "is_active": True,
                },
                {"entity_key": "LEGACY", "display_name": "Legacy without active flag"},
            ]
        }


class GeneratedRecordsTable:
    def __init__(self):
        self.queries = []
        self.deleted = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if "ExclusiveStartKey" not in kwargs:
            return {
                "Items": [
                    {"id": "DOC#doc-1", "theme_entity_id": "CLIMATE_METRICS#fact", "entity_type": "thematic_fact"},
                    {"id": "DOC#doc-1", "theme_entity_id": "CUSTOM#manual", "entity_type": "manual"},
                ],
                "LastEvaluatedKey": {"id": "DOC#doc-1", "theme_entity_id": "next"},
            }
        return {
            "Items": [
                {"id": "DOC#doc-1", "theme_entity_id": "TABLES#P01_T001", "entity_type": "table"},
                {"id": "DOC#doc-1", "theme_entity_id": "FIGURES#P01_F001", "entity_type": "figure"},
            ]
        }

    class BatchWriter:
        def __init__(self, table):
            self.table = table

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def delete_item(self, **kwargs):
            self.table.deleted.append(kwargs["Key"])

    def batch_writer(self):
        return self.BatchWriter(self)


class ExtractorTests(unittest.TestCase):
    def test_prompt_uses_only_active_rows_loaded_from_dynamodb(self):
        table = PagedTaxonomyTable()
        taxonomy = load_active_taxonomy(table)
        prompt = build_system_prompt(taxonomy)

        self.assertEqual(len(table.calls), 2)
        self.assertEqual(
            [item["entity_key"] for item in taxonomy],
            ["CLIMATE_METRICS", "PUBLIC_HEALTH"],
        )
        self.assertIn("PUBLIC_HEALTH", prompt)
        self.assertIn("CLIMATE_METRICS", prompt)
        self.assertIn("Health impacts", prompt)
        self.assertNotIn("LOCAL_CUSTOM", prompt)
        self.assertNotIn("LEGACY", prompt)

    def test_loading_taxonomy_requires_active_database_rows(self):
        table = type("EmptyTaxonomyTable", (), {"scan": lambda self, **kwargs: {"Items": []}})()
        with self.assertRaisesRegex(ValueError, "No active entity taxonomy"):
            load_active_taxonomy(table)

    def test_page_chunks_group_text_by_page_range(self):
        chunks = list(page_chunks([
            {"page": 2, "text": "rainfall " + "x" * 130, "bbox": [0.1, 0.2, 0.4, 0.3]},
            {"page": 15, "text": "drought " + "y" * 130, "bbox": [0.1, 0.4, 0.4, 0.5]},
        ], page_group_size=10))
        self.assertEqual(len(chunks), 2)
        self.assertEqual([chunk["page"] for chunk in chunks], [1, 11])

    def test_page_chunks_honor_max_chars_without_dropping_text(self):
        source = " ".join(f"word{index}" for index in range(120))
        chunks = list(page_chunks(
            [{"page": 1, "text": source, "bbox": [0.1, 0.2, 0.4, 0.3]}],
            max_chars=90,
        ))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk["text"]) <= 90 for chunk in chunks))
        self.assertEqual(" ".join(chunk["text"] for chunk in chunks), source)

    def test_every_chunk_is_submitted_sequentially_in_order(self):
        chunks = [
            {"page": 1, "page_end": 1, "text": "first chunk"},
            {"page": 2, "page_end": 2, "text": "second chunk"},
        ]
        submitted = []

        def fake_extract(chunk, taxonomy, client, model_id):
            submitted.append(chunk["text"])
            return []

        with patch("ai_extractor.extract_chunk", side_effect=fake_extract):
            result = extract_chunks_sequential(chunks, TAXONOMY, object(), "fake-model")
        self.assertEqual(submitted, ["first chunk", "second chunk"])
        self.assertEqual(len(result), 2)

    def test_model_regions_are_allow_listed(self):
        self.assertEqual(
            get_model_config("meta.llama3-1-8b-instruct-v1:0")["region"],
            "us-west-2",
        )
        self.assertEqual(
            get_model_config("mistral.ministral-3-8b-instruct")["region"],
            "ap-southeast-2",
        )
        with self.assertRaisesRegex(ValueError, "Unsupported extraction model"):
            get_model_config("arbitrary.model")

    def test_rejects_trivial_and_unknown_taxonomy_facts(self):
        facts = [
            {"theme": "CLIMATE_METRICS", "extracted_fact": "Harvesting", "confidence_score": 0.99},
            {"theme": "UNKNOWN", "extracted_fact": FACT, "source_excerpt": SOURCE_EXCERPT, "confidence_score": 0.99},
            {"theme": "CLIMATE_METRICS", "extracted_fact": FACT, "source_excerpt": SOURCE_EXCERPT, "confidence_score": 0.9, "spatial_units": {"district": "Bidar"}},
        ]
        accepted = validate_entities(facts, TAXONOMY, SOURCE_TEXT)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["spatial_units"]["district"], "Bidar")
        self.assertEqual(accepted[0]["source_excerpt"], SOURCE_EXCERPT)

    def test_calls_selected_model_with_converse_and_parses_json(self):
        client = FakeBedrock(bedrock_response())
        result = extract_chunk(
            {"page": 4, "text": SOURCE_TEXT}, TAXONOMY, client,
            "meta.llama3-1-8b-instruct-v1:0",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(client.request["modelId"], "meta.llama3-1-8b-instruct-v1:0")
        self.assertIn("CLIMATE_METRICS", client.request["system"][0]["text"])
        self.assertEqual(client.request["inferenceConfig"]["temperature"], 0.1)

    def test_retries_transient_bedrock_throttling(self):
        client = ThrottlingBedrock(bedrock_response())
        with patch("ai_extractor.time.sleep") as sleep:
            result = extract_chunk(
                {"page": 4, "text": SOURCE_TEXT}, TAXONOMY, client,
                "meta.llama3-1-8b-instruct-v1:0",
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertGreaterEqual(sleep.call_args.args[0], 2)
        self.assertLess(sleep.call_args.args[0], 4)

    def test_ingestion_builder_stages_only_taxonomy_fact_and_uses_short_source(self):
        client = FakeBedrock(bedrock_response(extra=',"spatial_units":{"district":"Bidar"},"temporal_period":"1934"'))
        ocr = {
            "elements": [{"page": 1, "text": SOURCE_TEXT, "bbox": [0.1, 0.2, 0.9, 0.3]}],
            "tables": [{"page": 1, "bbox": [0.1, 0.4, 0.8, 0.8], "rows": [["Year", "Rainfall"], ["1934", "41 inches"]]}],
            "figures": [{"page": 1, "bbox": [0.1, 0.4, 0.8, 0.8], "image_s3_key": "figure.png"}],
        }
        entities = list(build_entities("doc-1", ocr, TAXONOMY, client))
        self.assertEqual(len(entities), 1)
        fact_entity = next(record for record in entities if record[0] == "CLIMATE_METRICS")
        self.assertEqual(fact_entity[2]["spatial_units"]["district"], "Bidar")
        self.assertEqual(fact_entity[2]["bbox"][:3], [0.1, 0.2, 0.8])
        self.assertAlmostEqual(fact_entity[2]["bbox"][3], 0.1)
        self.assertEqual(fact_entity[2]["source_text_chunk"], SOURCE_EXCERPT)

    def test_rejects_source_excerpt_outside_source_or_wrong_length(self):
        facts = [
            {"theme": "CLIMATE_METRICS", "extracted_fact": FACT, "source_excerpt": "Made up supporting quote from an unrelated historical source.", "confidence_score": 0.9},
            {"theme": "CLIMATE_METRICS", "extracted_fact": FACT, "source_excerpt": "Rainfall records indicate", "confidence_score": 0.9},
        ]
        self.assertEqual(validate_entities(facts, TAXONOMY, SOURCE_TEXT), [])

    def test_clears_only_generated_and_legacy_asset_records(self):
        table = GeneratedRecordsTable()
        self.assertEqual(clear_generated_records(table, "doc-1"), 3)
        self.assertEqual(len(table.queries), 2)
        self.assertEqual(
            table.deleted,
            [
                {"id": "DOC#doc-1", "theme_entity_id": "CLIMATE_METRICS#fact"},
                {"id": "DOC#doc-1", "theme_entity_id": "TABLES#P01_T001"},
                {"id": "DOC#doc-1", "theme_entity_id": "FIGURES#P01_F001"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
