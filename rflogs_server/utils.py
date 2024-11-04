import uuid
import base64
import re
import boto3
import boto3.exceptions
from botocore.client import Config
import os


def map_base64_char_to_alphabet(char: str) -> str:
    """Map the first character to an alphabet using modulo math on char code."""
    char_code = ord(char)
    # Map to A-Z (65-90) or a-z (97-122)
    return chr(
        ((char_code % 26) + 97) if char_code % 52 >= 26 else ((char_code % 26) + 65)
    )


def generate_urlsafe_id() -> str:
    """
    Generate a URL-safe, unique identifier.

    This function creates a 22-character string using a UUID4, which is then
    base64 encoded and made URL-safe. The first character is always mapped to
    an alphabet (a-z, A-Z) to ensure safety in various environments (e.g., CLI,
    filenames, URLs).

    Returns:
        str: A 22-character URL-safe identifier string.
    """
    random_uuid = uuid.uuid4()
    uuid_bytes = random_uuid.bytes
    urlsafe_id = base64.urlsafe_b64encode(uuid_bytes).rstrip(b"=").decode("ascii")
    return map_base64_char_to_alphabet(urlsafe_id[0]) + urlsafe_id[1:]


def get_s3_client(backend="s3"):
    region_name = os.getenv("AWS_REGION", "us-east-1")
    if backend == "minio":
        s3_client = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://localhost:9000"),
            aws_access_key_id=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            aws_secret_access_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            config=Config(signature_version="s3v4"),
        )
    else:
        session = boto3.session.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name,
        )
        s3_client = session.client("s3", config=Config(signature_version="s3v4"))
    return s3_client


TAG_KEY_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,49}$")
TAG_VALUE_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-/\s]{1,100}$")
