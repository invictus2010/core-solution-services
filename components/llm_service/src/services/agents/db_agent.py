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

""" SQL Agent module """
# pylint: disable=unused-argument

import datetime
import json
from typing import Tuple, List
from langchain.agents import create_sql_agent
from langchain.agents.agent_toolkits import SQLDatabaseToolkit
from langchain.sql_database import SQLDatabase
from langchain.tools import BaseTool
from langchain.tools.sql_database.tool import QuerySQLDataBaseTool
from common.utils.logging_handler import Logger
from config import (LANGCHAIN_LLM, PROJECT_ID,
                    OPENAI_LLM_TYPE_GPT4)
from config.utils import get_dataset_config
from services.agents.agent_prompts import (SQL_QUERY_FORMAT_INSTRUCTIONS,
                                           SQL_STATEMENT_FORMAT_INSTRUCTIONS,
                                           SQL_STATEMENT_PREFIX)
from services.agents.utils import (
    strip_punctuation_from_end, agent_executor_run_with_logs)
from services.agents.agent_tools import google_sheets_tool
import sqlparse
from sqlparse.sql import IdentifierList, Identifier
from sqlparse.tokens import Keyword, DML

Logger = Logger.get_logger(__file__)


def run_db_agent(prompt: str, llm_type: str = None, dataset = None,
                 user_email:str = None) -> Tuple[dict, str]:
  """
  Run the DB agent and return the resulting data.

  Return:
    a dict of "columns: column names, "data": row data
  """
  if dataset is None:
    dataset, db_type = map_prompt_to_dataset(prompt, llm_type)
  else:
    ds_config = get_dataset_config().get(dataset, None)
    if ds_config is None:
      raise RuntimeError(f"Dataset not found {dataset}")
    db_type = ds_config.get("type")

  Logger.info(f"querying db dataset {dataset} db type {db_type}")

  if db_type == "SQL":
    # generate SQL statement
    statement, agent_logs = generate_sql_statement(
        prompt, dataset, llm_type, user_email)

    # run SQL
    output = execute_sql_statement(statement, dataset, user_email)

  else:
    raise RuntimeError(f"Unsupported agent db type {db_type}")
  return output, agent_logs


def map_prompt_to_dataset(prompt: str, llm_type: str) -> str:
  """
  Determine the dataset based on the prompt
  """
  datasets = get_dataset_config()

  # TODO: use LLM to map datatype
  dataset = "fqhc_medical_transactions"

  db_type = datasets.get(dataset).get("type")
  return dataset, db_type


def generate_sql_statement(prompt: str,
                           dataset: str,
                           llm_type: str=None,
                           user_email: str=None) -> Tuple[dict, str]:

  llm = get_langchain_llm(llm_type)

  # get langchain SQL db object
  db, db_url = get_langchain_db(dataset)

  Logger.info(f"generating sql statement for dataset [{dataset}] "
              f"prompt [{prompt}] llm_type [{llm_type}] "
              f"db url [{db_url}]")

  # create langchain SQL agent to generate SQL statement
  toolkit = SQLStatementDBToolKit(db=db, llm=llm)

  agent_executor = create_sql_agent(
      llm=llm,
      toolkit=toolkit,
      verbose=True,
      top_k=100,
      prefix=SQL_STATEMENT_PREFIX
  )

  # get query prompt for agent
  input_prompt = format_prompt(prompt, SQL_STATEMENT_FORMAT_INSTRUCTIONS)

  # return_val = agent_executor.run(input_prompt)
  return_val, agent_logs = agent_executor_run_with_logs(
      agent_executor, input_prompt)

  Logger.info(f"generated SQL statement [{return_val}]")

  # do some basic validation on output
  return_val = return_val.strip()
  if not return_val.startswith("SELECT") \
     or "FROM" not in return_val:
    raise RuntimeError("invalid SQL statement: {return_val}")

  return return_val, agent_logs

def execute_sql_statement(statement: str,
                          dataset: str,
                          user_email: str=None) -> Tuple[dict, str]:
  """
  Execute a SQL database statement on the dataset
  """
  # create langchain SQL db object
  db_url = f"bigquery://{PROJECT_ID}/{dataset}"
  db = SQLDatabase.from_uri(db_url)

  # instantiate the langchain db tool to run the query.
  # we don't need a description since the tool is not being
  # executed by an agent.
  query_sql_database_tool = QuerySQLDataBaseTool(
      db=db, description=""
  )
  dbdata = query_sql_database_tool.run(statement)

  # get columns from the sql statement
  columns = extract_columns(statement)
  
  # generate spreadsheet
  sheet_data = {
    "columns": columns,
    "data": dbdata
  }
  sheet_url = generate_spreadsheet(dataset, sheet_data, user_email)

  # format output
  output = {
    "data": sheet_data,
    "resources": {
      "Spreadsheet": sheet_url
    }
  }

  return output


def execute_sql_query(prompt: str,
                      dataset: str,
                      llm_type: str=None,
                      user_email: str=None) -> Tuple[dict, str]:
  """
  Execute a SQL database query based on a human prompt.
  Currently hardcoded to target bigquery.

  Args:
    prompt: human query
    dataset: dataset id (from agent dataset config)
    llm_type: model id of llm to use to execute the query

  Return:
    Tuple:
      sheet URL,
      a dict of "columns: column names, "data": row data
  """
  llm = get_langchain_llm(llm_type)

  # get langchain SQL db object
  db, db_url = get_langchain_db(dataset)

  Logger.info(f"querying db dataset [{dataset}] "
              f"prompt [{prompt}] llm_type [{llm_type}] "
              f"db url [{db_url}]")

  # create langchain SQL agent to perform db query
  toolkit = SQLDatabaseToolkit(db=db, llm=llm)
  agent_executor = create_sql_agent(
      llm=llm,
      toolkit=toolkit,
      verbose=True,
      top_k=100
  )

  # Format query prompt for agent
  input_prompt = format_prompt(prompt, SQL_QUERY_FORMAT_INSTRUCTIONS)

  # return_val = agent_executor.run(input_prompt)
  return_val, agent_logs = agent_executor_run_with_logs(
      agent_executor, input_prompt)

  # process result
  try:
    output_dict = json.loads(return_val)
  except Exception as e:
    msg = f"DB Query returned non-json data format. " \
          f"llm_type: {llm_type} prompt {prompt} return {return_val}"
    Logger.error(msg)
    raise RuntimeError(msg) from e

  # validate return value
  if not "columns" in output_dict or not "data" in output_dict:
    msg = f"DB Query return data missing columns/data. " \
          f"llm_type: {llm_type} prompt {prompt} return {return_val}"
    Logger.error(msg)
    raise RuntimeError(msg)

  # generate spreadsheet
  sheet_url = generate_spreadsheet(dataset, output_dict, user_email)

  output = {
    "data": output_dict,
    "resources": {
      "Spreadsheet": sheet_url
    }
  }
  return output, agent_logs


def generate_spreadsheet(
    dataset: str, return_dict: dict, user_email:list) -> str:
  """
  Generate Workspace Sheet containing return data
  """
  now = datetime.datetime.utcnow()
  sheet_name = f"Dataset {dataset} Query {now}"
  sheet_output = google_sheets_tool(sheet_name,
                                    return_dict["columns"],
                                    return_dict["data"],
                                    user_email=user_email)
  sheet_url = sheet_output["sheet_url"]
  return sheet_url

def extract_columns(sql_query: str) -> List[str]:
  """ Use sqlparse to extract columns from a SQL statement """
  # Parse the SQL query
  parsed = sqlparse.parse(sql_query)[0]

  # Flag to indicate if we are in the SELECT part
  select_part = False
  columns = []

  for token in parsed.tokens:
    if select_part:
      if isinstance(token, IdentifierList):
        for identifier in token.get_identifiers():
          columns.append(str(identifier))
      elif isinstance(token, Identifier):
        columns.append(str(token))
      elif token.ttype is Keyword:
        break

    if token.ttype is DML and token.value.upper() == 'SELECT':
      select_part = True

  return columns

class SQLStatementDBToolKit(SQLDatabaseToolkit):
  """ override SQLDatabaseToolkit to remove the SQL query tool """
  def get_tools(self) -> List[BaseTool]:
    tools = super().get_tools()
    my_tools = [tool for tool in tools
                if not isinstance(tool, QuerySQLDataBaseTool)]
    return my_tools

def get_langchain_llm(llm_type: str):
  """
  Get langchain llm.  Since we are using langchain SQL query
  toolkit only Langchain LLM objects are supported.
  """
  if llm_type is None:
    llm_type = OPENAI_LLM_TYPE_GPT4
  llm = LANGCHAIN_LLM[llm_type]
  if llm is None:
    raise RuntimeError(f"Unsupported llm type {llm_type}")
  return llm


def get_langchain_db(dataset: str):
  # create langchain SQL db object
  db_url = f"bigquery://{PROJECT_ID}/{dataset}"
  db = SQLDatabase.from_uri(db_url)
  return db, db_url


def format_prompt(prompt: str, format_instructions: str) -> str:
  """ Format query prompt for agent.  We strip punctuation and add a question
  mark to make sure the format instructions are cleanly separated. """
  clean_prompt = strip_punctuation_from_end(prompt)
  input_prompt = f"{clean_prompt}? {format_instructions}"
  return input_prompt

