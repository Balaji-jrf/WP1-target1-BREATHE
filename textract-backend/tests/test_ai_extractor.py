import io
import json
import unittest

from ai_extractor import extract_chunk, page_chunks, validate_entities
from dynamo_ingestion import build_entities


TAXONOMY = [{"entity_key": "CLIMATE_METRICS", "display_name": "Climate", "is_active": True}]


class FakeBedrock:
    def __init__(self, text):
        self.text = text
        self.request = None

    def invoke_model(self, **kwargs):
        self.request = kwargs
        body = {"output": {"message": {"content": [{"text": self.text}]}}}
        return {"body": io.BytesIO(json.dumps(body).encode())}


class ExtractorTests(unittest.TestCase):
    def test_page_chunks_group_text_and_bound_chunk_size(self):
        chunks = list(page_chunks([
            {"page": 2, "text": "rainfall " + "x" * 80, "bbox": [0.1, 0.2, 0.4, 0.3]},
            {"page": 2, "text": "drought observation", "bbox": [0.1, 0.4, 0.4, 0.5]},
        ], max_chars=50))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk["text"]) <= 50 for chunk in chunks))
        self.assertTrue(all(chunk["page"] == 2 for chunk in chunks))

    def test_rejects_trivial_and_unknown_taxonomy_facts(self):
        facts = [
            {"theme": "CLIMATE_METRICS", "extracted_fact": "Harvesting", "confidence_score": 0.99},
            {"theme": "UNKNOWN", "extracted_fact": "Rainfall records indicate a severe seasonal drought in Bidar district.", "confidence_score": 0.99},
            {"theme": "CLIMATE_METRICS", "extracted_fact": "Rainfall records indicate a severe seasonal drought in Bidar district.", "confidence_score": 0.9, "spatial_units": {"district": "Bidar"}},
        ]
        accepted = validate_entities(facts, TAXONOMY)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["spatial_units"]["district"], "Bidar")

    def test_calls_nova_lite_invoke_model_and_parses_json(self):
        client = FakeBedrock('[{"theme":"CLIMATE_METRICS","extracted_fact":"Rainfall records indicate a severe seasonal drought in Bidar district.","confidence_score":0.9}]')
        result = extract_chunk({"page": 4, "text": "Source excerpt"}, TAXONOMY, client)
        self.assertEqual(len(result), 1)
        self.assertEqual(client.request["modelId"], "amazon.nova-lite-v1:0")
        self.assertEqual(client.request["contentType"], "application/json")

    def test_ingestion_builder_stages_validated_fact_and_table(self):
        client = FakeBedrock('[{"theme":"CLIMATE_METRICS","extracted_fact":"Rainfall records indicate a severe seasonal drought in Bidar district.","confidence_score":0.9,"spatial_units":{"district":"Bidar"},"temporal_period":"1934"}]')
        ocr = {
            "elements": [{"page": 1, "text": "Rainfall records indicate a severe seasonal drought in Bidar district during 1934.", "bbox": [0.1, 0.2, 0.9, 0.3]}],
            "tables": [{"page": 1, "bbox": [0.1, 0.4, 0.8, 0.8], "rows": [["Year", "Rainfall"], ["1934", "41 inches"]]}],
            "figures": [],
        }
        entities = list(build_entities("doc-1", ocr, TAXONOMY, client))
        self.assertEqual(len(entities), 2)
        table_entity = next(record for record in entities if record[0] == "TABLES")
        fact_entity = next(record for record in entities if record[0] == "CLIMATE_METRICS")
        self.assertEqual(table_entity[2]["review_status"], "pending_review")
        self.assertEqual(fact_entity[2]["spatial_units"]["district"], "Bidar")
        self.assertEqual(fact_entity[2]["bbox"][:3], [0.1, 0.2, 0.8])
        self.assertAlmostEqual(fact_entity[2]["bbox"][3], 0.1)


if __name__ == "__main__":
    unittest.main()
