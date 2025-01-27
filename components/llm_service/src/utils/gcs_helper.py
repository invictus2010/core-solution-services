# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# pylint: disable=unused-argument,broad-exception-raised
"""
Google Storage helper functions.
"""
import io
import re
from pathlib import Path
from typing import List
from common.utils.logging_handler import Logger
from google.cloud import storage
import google.cloud.exceptions
from config import PROJECT_ID

Logger = Logger.get_logger(__file__)

def clear_bucket(storage_client: storage.Client, bucket_name: str) -> None:
  """
  Delete all the contents of the specified GCS bucket
  """
  Logger.info(f"Deleting all objects from GCS bucket {bucket_name}")
  bucket = storage_client.bucket(bucket_name)
  blobs = bucket.list_blobs()
  index = 0
  for blob in blobs:
    blob.delete()
    index += 1
  Logger.info(f"{index} files deleted")

def create_bucket(storage_client: storage.Client,
                  bucket_name: str, location: str = None,
                  clear: bool = True,
                  make_public: bool = False) -> None:
  # Check if the bucket exists
  bucket = storage_client.bucket(bucket_name)
  if not bucket.exists():
    # Create new bucket
    if make_public:
      _ = storage_client.create_bucket(bucket_name, location=location)
      set_bucket_viewer_iam(storage_client, bucket_name, ["allUsers"])
    else:
      _ = storage_client.create_bucket(bucket_name, location=location)
    Logger.info(f"Bucket {bucket_name} created.")
  else:
    Logger.info(f"Bucket {bucket_name} already exists.")
    if clear:
      clear_bucket(storage_client, bucket_name)

def set_bucket_viewer_iam(
    storage_client: storage.Client,
    bucket_name: str,
    members: List[str] = None,
):
  """Set viewer IAM Policy on bucket"""
  if members is None:
    members = ["allUsers"]
  bucket = storage_client.bucket(bucket_name)
  policy = bucket.get_iam_policy(requested_policy_version=3)
  policy.bindings.append(
      {"role": "roles/storage.objectViewer", "members": members}
  )
  bucket.set_iam_policy(policy)

def upload_to_gcs(storage_client: storage.Client, bucket_name: str,
                  file_path: str, bucket_folder: str = None) -> str:
  """ Upload file to GCS bucket. Returns URL to file. """
  Logger.info(f"""Uploading {file_path} to GCS bucket {bucket_name} \
              in folder {str(bucket_folder)}""")
  bucket = storage_client.bucket(bucket_name)
  file_name = Path(file_path).name
  if bucket_folder:
    file_name = f"{bucket_folder}/{file_name}"
  blob = bucket.blob(file_name)
  blob.upload_from_filename(file_path)
  gcs_url = f"gs://{bucket_name}/{file_name}"
  Logger.info(f"Uploaded {file_path} to {gcs_url}")
  return gcs_url

def upload_file_to_gcs(bucket: storage.Bucket,
                       file_name: str, file_obj: io.BytesIO) -> str:
  """ Upload file to GCS bucket. Returns URL to file. """
  bucket_name = bucket.name
  Logger.info(f"Uploading {file_name} to GCS bucket {bucket_name}")
  blob = bucket.blob(file_name)
  blob.upload_from_file(file_obj)
  gcs_url = f"gs://{bucket_name}/{file_name}"
  Logger.info(f"Uploaded {file_name} to {gcs_url}")
  return gcs_url

def create_bucket_for_file(filename: str) -> storage.Bucket:
  storage_client = storage.Client()

  # base name is projectid_filename
  base_name = PROJECT_ID + "_" + Path(filename).name

  # Convert to lowercase, replace invalid characters with hyphens,
  # and ensure length is within limits
  bucket_name = re.sub(r"[^a-z0-9\-]", "-", base_name.lower())[:63]

  # Add a suffix if needed to ensure uniqueness
  suffix = 0
  bucket = None
  while True:
    try:
      bucket = storage_client.bucket(bucket_name)
      bucket.location = "US"
      bucket.storage_class = "STANDARD"
      bucket.create()
      break  # Bucket created successfully, exit the loop
    except google.cloud.exceptions.Conflict:
      suffix += 1
      if suffix == 1:
        bucket_name = f"{bucket_name}-{suffix}"
      else:
        bucket_name = \
            f"{bucket_name[:len(bucket_name)-len(str(suffix-1))-1]}-{suffix}"

  Logger.info(f"Bucket {bucket.name} created")
  return bucket
