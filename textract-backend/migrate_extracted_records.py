"""Copy legacy extraction rows into the renamed document-records table."""

import argparse

import boto3


def scan_all(table):
    items, request = [], {}
    while True:
        response = table.scan(**request)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        request["ExclusiveStartKey"] = last_key


def migrate(region, source_name, target_name):
    dynamodb = boto3.resource("dynamodb", region_name=region)
    source, target = dynamodb.Table(source_name), dynamodb.Table(target_name)
    source_items = scan_all(source)
    with target.batch_writer(overwrite_by_pkeys=["id", "theme_entity_id"]) as writer:
        for item in source_items:
            # Empty values cannot be stored on an indexed DynamoDB key.
            if not item.get("district"):
                item.pop("district", None)
            writer.put_item(Item=item)

    target_keys = {
        (item["id"], item["theme_entity_id"])
        for item in scan_all(target)
    }
    source_keys = {
        (item["id"], item["theme_entity_id"])
        for item in source_items
    }
    missing = source_keys - target_keys
    if missing:
        raise RuntimeError(f"Migration verification failed: {len(missing)} records are missing")
    if len(target_keys) != len(source_keys):
        raise RuntimeError(
            f"Target contains {len(target_keys)} records; source contained {len(source_keys)}. "
            "Check for pre-existing target data before removing the source table."
        )
    print(f"Migrated and verified {len(source_keys)} records from {source_name} to {target_name}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--source", default="jrf-task-data-Extraction-entities")
    parser.add_argument("--target", default="jrf-task-ocr-ExtractedRecords")
    args = parser.parse_args()
    migrate(args.region, args.source, args.target)


if __name__ == "__main__":
    main()
