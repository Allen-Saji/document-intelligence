from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from document_intelligence.storage.keys import original_pdf_key

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000002")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000003")
PART_SIZE = 5 * 1024 * 1024


def _not_found(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}


def run_probe(endpoint_url: str, output: Path) -> dict[str, object]:
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name="us-east-1",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket = f"document-intelligence-{uuid4().hex}"
    version_id = uuid4()
    payload = (b"phase-0-part-one\n" * (PART_SIZE // 17 + 1))[:PART_SIZE]
    payload += (b"phase-0-part-two\n" * (PART_SIZE // 17 + 1))[:PART_SIZE]
    checksum = hashlib.sha256(payload).hexdigest()
    key = original_pdf_key(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_ID,
        version_id=version_id,
        sha256=checksum,
    )

    s3.create_bucket(Bucket=bucket)
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    upload = s3.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        Metadata={"document-version-id": str(version_id), "sha256": checksum},
        ContentType="application/pdf",
    )
    upload_id = upload["UploadId"]
    parts = []
    for part_number in (1, 2):
        start = (part_number - 1) * PART_SIZE
        response = s3.upload_part(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=payload[start : start + PART_SIZE],
        )
        parts.append({"PartNumber": part_number, "ETag": response["ETag"]})
    completed = s3.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload={"Parts": parts},
    )
    stored_version_id = completed.get("VersionId")
    head = s3.head_object(Bucket=bucket, Key=key, VersionId=stored_version_id)
    body = s3.get_object(Bucket=bucket, Key=key, VersionId=stored_version_id)["Body"].read()
    signed_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key, "VersionId": stored_version_id},
        ExpiresIn=300,
    )
    with urllib.request.urlopen(signed_url, timeout=30) as response:
        signed_body = response.read()

    abandoned_key = f"{key}.abandoned"
    abandoned = s3.create_multipart_upload(Bucket=bucket, Key=abandoned_key)
    s3.upload_part(
        Bucket=bucket,
        Key=abandoned_key,
        UploadId=abandoned["UploadId"],
        PartNumber=1,
        Body=b"abandoned",
    )
    s3.abort_multipart_upload(
        Bucket=bucket,
        Key=abandoned_key,
        UploadId=abandoned["UploadId"],
    )
    active_uploads = s3.list_multipart_uploads(Bucket=bucket).get("Uploads", [])

    s3.delete_object(Bucket=bucket, Key=key, VersionId=stored_version_id)
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        deletion_verified = _not_found(error)
    else:
        deletion_verified = False
    s3.delete_bucket(Bucket=bucket)

    report: dict[str, object] = {
        "bucket": bucket,
        "key": key,
        "byte_count": len(payload),
        "sha256": checksum,
        "object_version_id_present": bool(stored_version_id),
        "metadata_version_id": head["Metadata"].get("document-version-id"),
        "round_trip_verified": body == payload,
        "signed_read_verified": signed_body == payload,
        "multipart_abort_verified": not active_uploads,
        "deletion_verified": deletion_verified,
    }
    report["storage_probe_verified"] = all(
        (
            report["object_version_id_present"],
            report["metadata_version_id"] == str(version_id),
            report["round_trip_verified"],
            report["signed_read_verified"],
            report["multipart_abort_verified"],
            report["deletion_verified"],
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 0 S3-compatible storage probe.")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("S3_ENDPOINT_URL", "http://127.0.0.1:4566"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/phase-0/storage/multipart.json"),
    )
    args = parser.parse_args()
    report = run_probe(args.endpoint, args.output)
    print(json.dumps(report, indent=2))
    if not report["storage_probe_verified"]:
        raise SystemExit("storage probe failed")


if __name__ == "__main__":
    main()
