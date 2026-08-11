from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from document_intelligence.documents.uploads import UploadIntent, reserve_upload
from document_intelligence.storage.multipart import MultipartPart, S3CompatibleObjectStore

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000003")


async def run_probe(endpoint_url: str) -> dict[str, object]:
    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name="us-east-1",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket = f"document-intelligence-phase1-{uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    payload = b"phase-1 verified upload\n" * 1024
    reservation = reserve_upload(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        actor_id=ACTOR_ID,
        intent=UploadIntent(
            display_name="Phase 1 storage probe",
            original_filename="phase1-probe.pdf",
            declared_size_bytes=len(payload),
        ),
    )
    store = S3CompatibleObjectStore(client=client, bucket=bucket)
    plan = await store.begin_multipart_upload(reservation)
    part = client.upload_part(
        Bucket=bucket,
        Key=plan.reservation.multipart_object_key,
        UploadId=plan.reservation.multipart_upload_id,
        PartNumber=1,
        Body=payload,
    )
    stored = await store.finalize_multipart_upload(
        plan.reservation,
        [MultipartPart(part_number=1, etag=part["ETag"])],
    )
    final = client.get_object(
        Bucket=bucket,
        Key=stored.reservation.final_object_key,
        VersionId=stored.object_version_id,
    )["Body"].read()
    pending_exists = True
    try:
        client.head_object(Bucket=bucket, Key=plan.reservation.multipart_object_key)
    except ClientError as error:
        pending_exists = error.response.get("Error", {}).get("Code") not in {
            "404",
            "NoSuchKey",
            "NotFound",
        }
    client.delete_object(
        Bucket=bucket,
        Key=stored.reservation.final_object_key,
        VersionId=stored.object_version_id,
    )
    client.delete_bucket(Bucket=bucket)
    return {
        "bucket": bucket,
        "part_url_count": len(plan.part_upload_urls),
        "object_version_id_present": bool(stored.object_version_id),
        "sha256": stored.sha256,
        "round_trip_verified": final == payload,
        "temporary_object_removed": not pending_exists,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 1 multipart storage adapter probe.")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("S3_ENDPOINT_URL", "http://127.0.0.1:4566"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase-1/storage/multipart.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(run_probe(args.endpoint))
    report["storage_adapter_verified"] = all(
        (
            report["part_url_count"] == 1,
            report["object_version_id_present"],
            report["round_trip_verified"],
            report["temporary_object_removed"],
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["storage_adapter_verified"]:
        raise SystemExit("Phase 1 storage adapter probe failed")


if __name__ == "__main__":
    main()
