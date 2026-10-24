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


if __name__ == "__main__":
    main()
