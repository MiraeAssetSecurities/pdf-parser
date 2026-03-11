"""S3 operations for reading PDFs and writing markdown files."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("pdf_parser.s3_handler")


class S3Handler:
    """Handle S3 read/write operations for PDFs and markdown files."""

    def __init__(self, region_name: str | None = None):
        """Initialize S3 client.

        Args:
            region_name: AWS region. If None, uses boto3 default region.
        """
        kwargs = {}
        if region_name:
            kwargs["region_name"] = region_name
        self._client = boto3.client("s3", **kwargs)

    @staticmethod
    def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
        """Parse s3://bucket/key into (bucket, key).

        Args:
            s3_uri: S3 URI like s3://my-bucket/path/to/file.pdf

        Returns:
            (bucket_name, object_key)

        Raises:
            ValueError: If URI format is invalid
        """
        if not s3_uri.startswith("s3://"):
            raise ValueError(f"Invalid S3 URI: {s3_uri}. Must start with s3://")

        parts = s3_uri[5:].split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid S3 URI: {s3_uri}. Format: s3://bucket/key")

        return parts[0], parts[1]

    def download_pdf(self, s3_uri: str, local_path: Path) -> None:
        """Download PDF from S3 to local file.

        Args:
            s3_uri: S3 URI like s3://bucket/path/file.pdf
            local_path: Local filesystem path to save the PDF

        Raises:
            ClientError: If S3 download fails
        """
        bucket, key = self.parse_s3_uri(s3_uri)
        logger.info("Downloading s3://%s/%s → %s", bucket, key, local_path)

        try:
            self._client.download_file(bucket, key, str(local_path))
            logger.info("Downloaded successfully")
        except ClientError as e:
            logger.error("Failed to download from S3: %s", e)
            raise

    def upload_file(self, local_path: Path, s3_uri: str) -> None:
        """Upload local file to S3.

        Args:
            local_path: Local file path
            s3_uri: Destination S3 URI like s3://bucket/path/file.md

        Raises:
            ClientError: If S3 upload fails
        """
        bucket, key = self.parse_s3_uri(s3_uri)
        logger.info("Uploading %s → s3://%s/%s", local_path, bucket, key)

        try:
            self._client.upload_file(str(local_path), bucket, key)
            logger.info("Uploaded successfully")
        except ClientError as e:
            logger.error("Failed to upload to S3: %s", e)
            raise

    def upload_directory(self, local_dir: Path, s3_base_uri: str) -> list[str]:
        """Upload entire directory to S3, preserving structure.

        Args:
            local_dir: Local directory to upload
            s3_base_uri: Base S3 URI like s3://bucket/output/doc_name/

        Returns:
            List of uploaded S3 URIs

        Raises:
            ClientError: If any S3 upload fails
        """
        bucket, base_key = self.parse_s3_uri(s3_base_uri)
        if not base_key.endswith("/"):
            base_key += "/"

        uploaded = []
        for local_file in local_dir.rglob("*"):
            if local_file.is_file():
                rel_path = local_file.relative_to(local_dir)
                s3_key = base_key + str(rel_path).replace("\\", "/")
                s3_uri = f"s3://{bucket}/{s3_key}"

                logger.debug("Uploading %s → %s", rel_path, s3_uri)
                self._client.upload_file(str(local_file), bucket, s3_key)
                uploaded.append(s3_uri)

        logger.info("Uploaded %d files to s3://%s/%s", len(uploaded), bucket, base_key)
        return uploaded

    def list_pdfs(self, s3_prefix: str) -> list[str]:
        """List all PDF files in S3 prefix.

        Args:
            s3_prefix: S3 URI like s3://bucket/pdfs/

        Returns:
            List of S3 URIs for PDF files

        Raises:
            ClientError: If S3 list operation fails
        """
        bucket, prefix = self.parse_s3_uri(s3_prefix)
        if not prefix.endswith("/"):
            prefix += "/"

        logger.info("Listing PDFs in s3://%s/%s", bucket, prefix)

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

            pdfs = []
            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.lower().endswith(".pdf"):
                        pdfs.append(f"s3://{bucket}/{key}")

            logger.info("Found %d PDF files", len(pdfs))
            return pdfs
        except ClientError as e:
            logger.error("Failed to list S3 objects: %s", e)
            raise

    def read_markdown(self, s3_uri: str) -> str:
        """Read markdown file content from S3.

        Args:
            s3_uri: S3 URI of markdown file

        Returns:
            Markdown content as string

        Raises:
            ClientError: If S3 read fails
        """
        bucket, key = self.parse_s3_uri(s3_uri)

        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            return content
        except ClientError as e:
            logger.error("Failed to read from S3: %s", e)
            raise

    def list_folders(self, s3_prefix: str) -> list[str]:
        """List all folders (common prefixes) in S3 prefix.

        Args:
            s3_prefix: S3 URI like s3://bucket/output/

        Returns:
            List of folder names (without full path)

        Raises:
            ClientError: If S3 list operation fails
        """
        bucket, prefix = self.parse_s3_uri(s3_prefix)
        if not prefix.endswith("/"):
            prefix += "/"

        logger.info("Listing folders in s3://%s/%s", bucket, prefix)

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")

            folders = []
            for page in pages:
                for common_prefix in page.get("CommonPrefixes", []):
                    folder_key = common_prefix["Prefix"]
                    # Extract folder name (remove prefix and trailing slash)
                    folder_name = folder_key[len(prefix):].rstrip("/")
                    if folder_name:
                        folders.append(folder_name)

            logger.info("Found %d folders", len(folders))
            return sorted(folders)
        except ClientError as e:
            logger.error("Failed to list S3 folders: %s", e)
            raise

    def browse_path(self, s3_prefix: str) -> dict:
        """Browse S3 path and return folders and PDF files.

        Args:
            s3_prefix: S3 URI like s3://bucket/path/

        Returns:
            Dictionary with 'folders' and 'pdfs' lists
            - folders: List of folder names (without full path)
            - pdfs: List of PDF file names (without full path)

        Raises:
            ClientError: If S3 list operation fails
        """
        bucket, prefix = self.parse_s3_uri(s3_prefix)
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        logger.info("Browsing s3://%s/%s", bucket, prefix)

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")

            folders = []
            pdfs = []

            for page in pages:
                # Collect folders (common prefixes)
                for common_prefix in page.get("CommonPrefixes", []):
                    folder_key = common_prefix["Prefix"]
                    folder_name = folder_key[len(prefix):].rstrip("/")
                    if folder_name:
                        folders.append(folder_name)

                # Collect PDF files in current directory
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Skip if it's a folder marker or not a PDF
                    if key == prefix or key.endswith("/"):
                        continue
                    file_name = key[len(prefix):]
                    # Only include files in current directory (no subdirectories)
                    if "/" not in file_name and file_name.lower().endswith(".pdf"):
                        pdfs.append(file_name)

            logger.info("Found %d folders, %d PDFs", len(folders), len(pdfs))
            return {"folders": sorted(folders), "pdfs": sorted(pdfs)}
        except ClientError as e:
            logger.error("Failed to browse S3 path: %s", e)
            raise

    def download_directory(self, s3_prefix: str, local_dir: Path) -> int:
        """Download entire S3 directory to local filesystem.

        Args:
            s3_prefix: S3 URI like s3://bucket/output/sample/
            local_dir: Local directory to download files to

        Returns:
            Number of files downloaded

        Raises:
            ClientError: If S3 download fails
        """
        bucket, prefix = self.parse_s3_uri(s3_prefix)
        if not prefix.endswith("/"):
            prefix += "/"

        logger.info("Downloading s3://%s/%s → %s", bucket, prefix, local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

            count = 0
            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Skip if it's just a folder marker
                    if key.endswith("/"):
                        continue

                    # Calculate relative path and local file path
                    rel_path = key[len(prefix):]
                    local_file = local_dir / rel_path

                    # Create parent directories
                    local_file.parent.mkdir(parents=True, exist_ok=True)

                    # Download file
                    logger.debug("Downloading %s → %s", key, local_file)
                    self._client.download_file(bucket, key, str(local_file))
                    count += 1

            logger.info("Downloaded %d files", count)
            return count
        except ClientError as e:
            logger.error("Failed to download from S3: %s", e)
            raise
