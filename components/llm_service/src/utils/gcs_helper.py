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
from common.utils.logging_handler import Logger
from google.cloud import storage

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
                  force: bool = True) -> None:
  # Check if the bucket exists
  bucket = storage_client.bucket(bucket_name)
  if not bucket.exists():
    # Create new bucket
    _ = storage_client.create_bucket(bucket_name, location=location)
    print(f"Bucket {bucket_name} created.")
  else:
    print(f"Bucket {bucket_name} already exists.")
    if force:
      clear_bucket(storage_client, bucket_name)

def upload_to_gcs(storage_client: storage.Client, bucket_name: str,
                  file_name: str, content: str,
                  content_type="text/plain") -> None:
  """Upload content to GCS bucket"""
  Logger.info(f"Uploading {file_name} to GCS bucket {bucket_name}")
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(file_name)
  blob.upload_from_string(
    data=content,
    content_type=content_type
  )
  Logger.info(f"Uploaded {len(content)} bytes")