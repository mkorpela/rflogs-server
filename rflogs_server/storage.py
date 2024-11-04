import io
import os
from typing import Tuple

from botocore.exceptions import ClientError

from .logging_config import get_logger
from .models import Workspace
from .utils import get_s3_client

logger = get_logger(__name__)


class StorageManager:
    def __init__(self, workspace: Workspace, backend="s3"):
        self.backend = backend
        self.workspace = workspace
        self.bucket_name = workspace.bucket_name  # Use bucket_name from workspace
        self.region_name = os.getenv("AWS_REGION", "us-east-1")

        # Initialize S3 client using the helper function
        self.s3_client = get_s3_client(backend=self.backend)

        # Ensure the bucket exists
        self.create_bucket_if_not_exists()

    def create_bucket_if_not_exists(self):
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = int(e.response["Error"]["Code"])
            if error_code == 404:
                try:
                    if self.backend == "minio":
                        self.s3_client.create_bucket(Bucket=self.bucket_name)
                    else:
                        self.s3_client.create_bucket(
                            Bucket=self.bucket_name,
                            CreateBucketConfiguration={
                                "LocationConstraint": self.region_name
                            },
                        )
                    logger.info(f"Created bucket: {self.bucket_name}")
                except ClientError as e:
                    logger.error(f"Failed to create bucket: {e}")
            else:
                logger.error(f"Failed to access bucket: {e}")

    def upload_file(self, file_obj, object_name) -> Tuple[bool, int]:
        if (
            not object_name
            or len(object_name) > 1024
            or ".." in object_name
            or "\x00" in object_name
        ):
            logger.error("Invalid object name")
            return False, 0
        try:
            self.s3_client.upload_fileobj(file_obj, self.bucket_name, object_name)
            # After successful upload, use head_object to get the file size
            response = self.s3_client.head_object(
                Bucket=self.bucket_name, Key=object_name
            )
            file_size = response["ContentLength"]

            logger.info(
                f"Successfully uploaded file to {self.bucket_name}/{object_name}, size: {file_size} bytes"
            )
            return True, file_size
        except ClientError as e:
            logger.error(
                f"Failed to upload file or retrieve size for {object_name}: {e}"
            )
            return False, 0

    def download_file(self, object_name):
        try:
            file_obj = io.BytesIO()
            self.s3_client.download_fileobj(self.bucket_name, object_name, file_obj)
            file_obj.seek(0)  # Reset file pointer to the beginning
            logger.info(
                f"Successfully downloaded file {self.bucket_name}/{object_name}"
            )
            return file_obj
        except ClientError as e:
            logger.error(f"Failed to download file {object_name}: {e}")
            return None

    def delete_file(self, object_name):
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=object_name)
            logger.info(f"Deleted file {self.bucket_name}/{object_name}")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete file {object_name}: {e}")
            return False
