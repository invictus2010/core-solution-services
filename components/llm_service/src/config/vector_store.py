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
"""
Vector Store Config
"""
# pylint: disable=broad-exception-caught

from common.utils.http_exceptions import InternalServerError
from google.cloud import secretmanager
from config import PROJECT_ID

# vector store types
VECTOR_STORE_MATCHING_ENGINE = "matching_engine"
VECTOR_STORE_LANGCHAIN_PGVECTOR = "langchain_pgvector"

# default vector store used for query engines
DEFAULT_VECTOR_STORE = VECTOR_STORE_LANGCHAIN_PGVECTOR

# postgres
PG_HOST = "10.133.0.2"
PG_PORT = "5432"
PG_DBNAME = "genie"
PG_USER = "postgres"
PG_PASSWD = None

# load secrets
secrets = secretmanager.SecretManagerServiceClient()
try:
  PG_PASSWD = secrets.access_secret_version(
      request={
          "name": "projects/" + PROJECT_ID +
                  "/secrets/postgres-user-passwd/versions/latest"
      }).payload.data.decode("utf-8")
  PG_PASSWD = PG_PASSWD.strip()
except Exception as e:
  raise InternalServerError(
      "Can't access postgres user password secret: {e}") from e
