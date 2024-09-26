# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Pydantic Models for ValidateToken API's
"""
from pydantic import ConfigDict, BaseModel
from typing import List, Optional
from schemas.schema_examples import BASIC_VALIDATE_TOKEN_RESPONSE_EXAMPLE


# pylint: disable=line-too-long
class IdentityModel(BaseModel):
  email: List[str]


class FirebaseModel(BaseModel):
  identities: IdentityModel
  sign_in_provider: str


class ResponseModel(BaseModel):
  """data Pydantic Model"""
  name: Optional[str] = None
  picture: Optional[str] = None
  iss: str
  aud: str
  auth_time: int
  user_id: str
  sub: str
  iat: int
  exp: int
  email: str
  email_verified: bool
  firebase: FirebaseModel
  uid: str
  access_api_docs: Optional[bool] = None
  user_id: Optional[str] = None
  user_type: str


class ValidateTokenResponseModel(BaseModel):
  """Validate Token Response Pydantic Model"""
  message: str = "Token validated successfully"
  success: bool = True
  data: ResponseModel
  model_config = ConfigDict(from_attributes=True, json_schema_extra={
      "example": {
          "success": True,
          "message": "Token validated successfully",
          "data": BASIC_VALIDATE_TOKEN_RESPONSE_EXAMPLE
      }
  })
