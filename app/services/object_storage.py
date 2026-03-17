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


def upload_local_file_to_gcs(local_file_path: str, destination_blob_name: str) -> str:
    """
    Uploads a file from a local path to R2 (object key = destination_blob_name).
    Kept name for drop-in replacement of GCS; returns an r2://-style identifier.
    """
    client = _get_client()
    bucket = _bucket()
    with open(local_file_path, "rb") as f:
        client.upload_fileobj(f, bucket, destination_blob_name, ExtraArgs={"ContentType": "application/pdf"})
    return f"r2://{bucket}/{destination_blob_name}"


def download_file_from_gcs(blob_name: str, destination_file_name: str) -> None:
    """Downloads an object from R2 to a local file path."""
    client = _get_client()
    client.download_file(_bucket(), blob_name, destination_file_name)


def delete_file_from_gcs(blob_name: str) -> None:
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


def object_exists(blob_name: str) -> bool:
    """Returns True if the object exists in R2."""
    try:
        _get_client().head_object(Bucket=_bucket(), Key=blob_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise
