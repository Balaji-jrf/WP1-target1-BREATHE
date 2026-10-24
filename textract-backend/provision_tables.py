"""Provision the OCR and extracted-entity DynamoDB tables safely.

The existing DocumentOCR table uses document_id as its sole key. DynamoDB does
not support changing a table's key schema in place, so this script preserves
that deployed schema and stores record_type=METADATA as an ordinary attribute.
"""

import argparse
import boto3
from botocore.exceptions import ClientError


def ensure_table(client, name, key_schema, attribute_definitions, gsis=None):
    try:
        description = client.describe_table(TableName=name)["Table"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
        params = {
            "TableName": name,
            "KeySchema": key_schema,
            "AttributeDefinitions": attribute_definitions,
            "BillingMode": "PAY_PER_REQUEST",
        }
        if gsis:
            params["GlobalSecondaryIndexes"] = gsis
        client.create_table(**params)
        client.get_waiter("table_exists").wait(TableName=name)
        print(f"Created {name}")
        return

    actual = [(entry["AttributeName"], entry["KeyType"]) for entry in description["KeySchema"]]
    expected = [(entry["AttributeName"], entry["KeyType"]) for entry in key_schema]
    if actual != expected:
        raise RuntimeError(
            f"{name} exists with key schema {actual}; requested {expected}. "
            "DynamoDB key schemas cannot be changed in place. Create a replacement "
            "table and migrate data before switching the application."
        )
    actual_indexes = {
        index["IndexName"]: index
        for index in description.get("GlobalSecondaryIndexes", [])
    }
    expected_indexes = {index["IndexName"]: index for index in (gsis or [])}
    missing_indexes = sorted(set(expected_indexes) - set(actual_indexes))
    if missing_indexes:
        raise RuntimeError(
            f"{name} is missing required global secondary indexes: {', '.join(missing_indexes)}. "
            "Add these indexes with UpdateTable before deploying the database API."
        )
    for index_name, expected_index in expected_indexes.items():
        expected_key_schema = [
            (entry["AttributeName"], entry["KeyType"])
            for entry in expected_index["KeySchema"]
        ]
        actual_key_schema = [
            (entry["AttributeName"], entry["KeyType"])
            for entry in actual_indexes[index_name]["KeySchema"]
        ]
        if actual_key_schema != expected_key_schema:
            raise RuntimeError(
                f"{name}.{index_name} has key schema {actual_key_schema}; "
                f"requested {expected_key_schema}."
            )
    print(f"Kept existing compatible table {name}")


DEFAULT_TAXONOMY = [
    {
        "entity_key": "LOCATION",
        "display_name": "Location & Administrative Units",
        "description": "Districts, taluks, villages, towns, and boundary changes.",
        "keywords": ["district", "taluk", "village", "town", "boundary"],
        "extraction_guidance": "Capture named places and their administrative level.",
        "is_active": True,
    },
    {
        "entity_key": "CLIMATE_METRICS",
        "display_name": "Climate & Heat",
        "description": "Temperature, rainfall, monsoon trends, drought, humidity, and heat waves.",
        "keywords": ["temperature", "rainfall", "monsoon", "drought", "humidity", "heat wave"],
        "extraction_guidance": "Preserve numeric measurements, units, and stated periods.",
        "is_active": True,
    },
    {
        "entity_key": "PUBLIC_HEALTH",
        "display_name": "Health & Sanitation",
        "description": "Disease, sanitation, water contamination, and public-health impacts.",
        "keywords": ["cholera", "malaria", "plague", "sanitation", "contamination", "sewage"],
        "extraction_guidance": "Record the documented disease or condition and the affected population/place.",
        "is_active": True,
    },
    {
        "entity_key": "AGRICULTURE_CROPS",
        "display_name": "Agriculture & Land Use",
        "description": "Crops, yields, cultivation, land tenure, and forest land.",
        "keywords": ["jowar", "cotton", "rice", "crop", "cultivation", "tenure", "forest"],
        "extraction_guidance": "Capture crop, yield or practice and any stated location or period.",
        "is_active": True,
    },
    {
        "entity_key": "IRRIGATION_WATER",
        "display_name": "Irrigation & Water",
        "description": "Tanks, wells, canals, reservoirs, groundwater, and irrigation projects.",
        "keywords": ["tank", "well", "canal", "reservoir", "groundwater", "irrigation"],
        "extraction_guidance": "Describe water infrastructure, use, condition, and location.",
        "is_active": True,
    },
    {
        "entity_key": "INEQUALITY_LABOUR",
        "display_name": "Socio-Economic Inequality",
        "description": "Caste, gendered labour, wages, migration, housing, and ventilation.",
        "keywords": ["caste", "labour", "wage", "migration", "housing", "ventilation"],
        "extraction_guidance": "Keep claims tied to the source wording and affected groups.",
        "is_active": True,
    },
    {
        "entity_key": "POLICIES_SCHEMES",
        "display_name": "Government & Policies",
        "description": "Acts, relief schemes, committees, and administrative programmes.",
        "keywords": ["act", "scheme", "committee", "relief", "government", "policy"],
        "extraction_guidance": "Capture the named policy or measure, its action, and date if stated.",
        "is_active": True,
    },
    {
        "entity_key": "TABLES",
        "display_name": "Tables",
        "description": "Structured tables detected by Textract.",
        "keywords": [],
        "is_active": False,
    },
    {
        "entity_key": "FIGURES",
        "display_name": "Figures & Maps",
        "description": "Detected figure regions and saved figure crops.",
        "keywords": [],
        "is_active": False,
    },
]


def seed_taxonomy(resource, table_name):
    table = resource.Table(table_name)
    for definition in DEFAULT_TAXONOMY:
        existing = table.get_item(Key={"entity_key": definition["entity_key"]}).get("Item")
        if not existing:
            table.put_item(Item=definition)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="ap-southeast-2")
    args = parser.parse_args()
    client = boto3.client("dynamodb", region_name=args.region)
    ensure_table(
        client,
        "jrf-task-ocr-DocumentOCR",
        [{"AttributeName": "document_id", "KeyType": "HASH"}],
        [{"AttributeName": "document_id", "AttributeType": "S"}],
    )
    ensure_table(
        client,
        "jrf-task-data-Extraction-entities",
        [
            {"AttributeName": "id", "KeyType": "HASH"},
            {"AttributeName": "theme_entity_id", "KeyType": "RANGE"},
        ],
        [
            {"AttributeName": "id", "AttributeType": "S"},
            {"AttributeName": "theme_entity_id", "AttributeType": "S"},
            {"AttributeName": "theme", "AttributeType": "S"},
            {"AttributeName": "district", "AttributeType": "S"},
            {"AttributeName": "document_id", "AttributeType": "S"},
        ],
        [
            {
                "IndexName": "ThemeIndex",
                "KeySchema": [
                    {"AttributeName": "theme", "KeyType": "HASH"},
                    {"AttributeName": "document_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "LocationIndex",
                "KeySchema": [
                    {"AttributeName": "district", "KeyType": "HASH"},
                    {"AttributeName": "document_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
    )
    taxonomy_name = "jrf-task-data-EntityTaxonomy"
    ensure_table(
        client,
        taxonomy_name,
        [{"AttributeName": "entity_key", "KeyType": "HASH"}],
        [{"AttributeName": "entity_key", "AttributeType": "S"}],
    )
    seed_taxonomy(boto3.resource("dynamodb", region_name=args.region), taxonomy_name)


if __name__ == "__main__":
    main()
