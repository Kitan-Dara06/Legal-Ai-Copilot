# app/services/object_storage.py
#
# Cloudflare R2 (S3-compatible) object storage.
# Replaces Google Cloud Storage. Uses boto3 with a custom R2 endpoint.
#
# Required env: CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_R2_ACCESS_KEY_ID,
#               CLOUDFLARE_R2_SECRET_ACCESS_KEY, CLOUDFLARE_R2_BUCKET_NAME

import logging
import os

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

R2_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("CLOUDFLARE_R2_BUCKET_NAME")

_client = None


def _get_client():
    """Returns a boto3 S3 client configured for Cloudflare R2."""
    global _client
    if _client is None:
        if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY]):
            raise ValueError(
                "R2 storage requires: CLOUDFLARE_ACCOUNT_ID, "
                "CLOUDFLARE_R2_ACCESS_KEY_ID, CLOUDFLARE_R2_SECRET_ACCESS_KEY"
            )
        endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        _client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
    return _client


def _bucket():
    if not R2_BUCKET_NAME:
        raise ValueError("CLOUDFLARE_R2_BUCKET_NAME environment variable is missing.")
    return R2_BUCKET_NAME


def upload_file(
    local_file_path: str,
    destination_blob_name: str,
    content_type: str = "application/pdf",
) -> str:
    """
    Uploads a file from a local path to R2 (object key = destination_blob_name).
    Returns an r2://-style identifier.
    """
    client = _get_client()
    bucket = _bucket()
    with open(local_file_path, "rb") as f:
        client.upload_fileobj(
            f,
            bucket,
            destination_blob_name,
            ExtraArgs={"ContentType": content_type},
        )
    return f"r2://{bucket}/{destination_blob_name}"


def upload_bytes(
    data: bytes, destination_blob_name: str, content_type: str = "application/pdf"
) -> str:
    """
    Uploads bytes directly to R2 (object key = destination_blob_name).
    Returns an r2://-style identifier.
    """
    client = _get_client()
    bucket = _bucket()
    from io import BytesIO

    client.upload_fileobj(
        BytesIO(data),
        bucket,
        destination_blob_name,
        ExtraArgs={"ContentType": content_type},
    )
    return f"r2://{bucket}/{destination_blob_name}"


def generate_presigned_upload(blob_name: str, max_size_bytes: int = 104857600) -> dict:
    """
    Generates a pre-signed POST policy for direct client-to-R2 uploads.
    Enforces a strict maximum file size via AWS S3 Conditions.
    """
    client = _get_client()
    bucket = _bucket()

    # Conditions: enforce bucket, key, and content-length-range (default 0 to 100MB)
    conditions = [
        {"bucket": bucket},
        {"key": blob_name},
        ["content-length-range", 0, max_size_bytes],
    ]

    try:
        response = client.generate_presigned_post(
            Bucket=bucket,
            Key=blob_name,
            Fields={"key": blob_name},
            Conditions=conditions,
            ExpiresIn=3600,  # 1 hour expiry
        )
        return response
    except ClientError as e:
        logger.error("[r2] Failed to generate presigned url: %s", e)
        raise


# Backward-compatible alias
upload_local_file_to_gcs = upload_file


def download_file(blob_name: str, destination_file_name: str) -> None:
    """Downloads an object from R2 to a local file path."""
    client = _get_client()
    client.download_file(_bucket(), blob_name, destination_file_name)


# Backward-compatible alias
download_file_from_gcs = download_file


def delete_file(blob_name: str) -> None:
    """Deletes an object from R2. Logs and ignores if object is missing."""
    try:
        client = _get_client()
        client.delete_object(Bucket=_bucket(), Key=blob_name)
        logger.info("[r2] Deleted object: %s", blob_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            logger.debug("[r2] Object already missing: %s", blob_name)
        else:
            logger.warning("[r2] Could not delete object %s: %s", blob_name, e)
    except Exception as e:
        logger.warning("[r2] Could not delete object %s: %s", blob_name, e)


# Backward-compatible alias
delete_file_from_gcs = delete_file


def object_exists(blob_name: str) -> bool:
    """Returns True if the object exists in R2."""
    try:
        _get_client().head_object(Bucket=_bucket(), Key=blob_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise


def check_storage_ready(strict: bool = False) -> bool:
    """
    Validates that R2 credentials are present and bucket is reachable.
    If strict=True, re-raises on failures so callers can fail fast at startup.
    """
    try:
        client = _get_client()
        bucket = _bucket()
        client.head_bucket(Bucket=bucket)
        logger.info("[r2] Storage readiness check passed for bucket '%s'", bucket)
        return True
    except Exception as e:
        logger.error("[r2] Storage readiness check failed: %s", e)
        if strict:
            raise
        return False
