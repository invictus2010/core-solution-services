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
Query Data Sources
"""
import json
import traceback
import os
import re
import tempfile
from time import time
from random import randint
from urllib.parse import unquote
from copy import copy
from base64 import b64encode
from typing import List, Tuple
from pathlib import Path
from common.utils.logging_handler import Logger
from common.utils.gcs_adapter import get_blob_from_gcs_path
from common.models import QueryEngine
from config import PROJECT_ID, get_default_manifest
from pypdf import PdfReader, PdfWriter, PageObject
from pdf2image import convert_from_path
from langchain_community.document_loaders import CSVLoader
from utils.errors import NoDocumentsIndexedException
from utils import text_helper, gcs_helper
from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import SentenceWindowNodeParser
from llama_index.core import Document

# pylint: disable=broad-exception-caught

# text chunk size for embedding data
Logger = Logger.get_logger(__file__)
# number of sentences included before and after the current
# sentence when creating chunks (chunks have overlapping text)
CHUNK_SENTENCE_PADDING = 1

class DataSourceFile():
  """ object storing meta data about a data source file """
  def __init__(self,
               doc_name:str=None,
               src_url:str=None,
               local_path:str=None,
               gcs_path:str=None,
               doc_id:str=None,
               mime_type:str=None):
    self.doc_name = doc_name
    self.src_url = src_url
    self.local_path = local_path
    self.gcs_path = gcs_path
    self.doc_id = doc_id
    self.mime_type = mime_type

  def __repr__(self) -> str:
    """
    Log-friendly string representation of a DataSourceFile
    """
    return (
      f"DataSourceFile(doc_name={self.doc_name}, "
      f"src_url={self.src_url}, "
      f"local_path={self.local_path}, "
      f"gcs_path={self.gcs_path}, "
      f"doc_id={self.doc_id}, "
      f"mime_type={self.mime_type})"
    )


class DataSource:
  """
  Super class for query data sources. Also implements GCS DataSource.
  """

  def __init__(self, storage_client):
    self.storage_client = storage_client
    self.docs_not_processed = []
    # use llama index sentence window parser
    self.doc_parser = SentenceWindowNodeParser.from_defaults(
      window_size=CHUNK_SENTENCE_PADDING,
      include_metadata=True,
      window_metadata_key="window_text",
      original_text_metadata_key="text",
    )

  @classmethod
  def downloads_bucket_name(cls, q_engine_name: str) -> str:
    """
    Generate a unique downloads bucket name, that obeys the rules of
    GCS bucket names.

    Args:
        q_engine_name: name of QueryEngine to generate the bucket name for.

    Returns:
        bucket name (str)
    """
    qe_name = q_engine_name.replace(" ", "-")
    qe_name = qe_name.replace("_", "-").lower()
    bucket_name = f"{PROJECT_ID}-downloads-{qe_name}"
    if not re.fullmatch("^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$", bucket_name):
      raise RuntimeError(f"Invalid downloads bucket name {bucket_name}")
    return bucket_name

  def download_documents(self, doc_url: str, temp_dir: str) -> \
        List[DataSourceFile]:
    """
    Download files from doc_url source to a local tmp directory

    Args:
        doc_url: url pointing to container of documents to be indexed
        temp_dir: Path to temporary directory to download files to

    Returns:
        list of DataSourceFile
    """
    doc_filepaths = []
    bucket_name = doc_url.split("gs://")[1].split("/")[0]
    Logger.info(f"downloading {doc_url} from bucket {bucket_name}")

    for blob in self.storage_client.list_blobs(bucket_name):
      # Download the file to the tmp folder flattening all directories
      file_name = Path(blob.name).name
      file_path = os.path.join(temp_dir, file_name)
      blob.download_to_filename(file_path)
      gcs_path = blob.path.replace("/b/","")
      gcs_url = f"gs://{gcs_path}"
      doc_filepaths.append(DataSourceFile(doc_name=blob.name,
                                          src_url=blob.public_url,
                                          local_path=file_path,
                                          gcs_path=gcs_url))

    if len(doc_filepaths) == 0:
      raise NoDocumentsIndexedException(
          f"No documents can be indexed at url {doc_url}")

    return doc_filepaths

  def init_metadata(self, q_engine: QueryEngine) -> dict:
    """ 
    Load metadata from manifest, if it is defined in the query engine
    
    Manifest spec looks like:
      {"doc1_url": {
          "title": "blah",
          "attr": "value"
        }
       "doc2_url": {
          ...
        }
      }
    Args:
        q_engine: query engine
    Returns:
      A dict representation of the manifest. If there is no manifest available
      returns empty dict.
    """
    manifest_url = q_engine.manifest_url
    manifest_spec = {}
    if manifest_url:
      if not manifest_url.startswith("gs://"):
        raise RuntimeError("unsupported manifest URL {manifest_url}")
      # download manifest from gs:// bucket
      blob = get_blob_from_gcs_path(manifest_url)
      manifest_spec = json.loads(blob.download_as_string())
      Logger.info(f"loaded document manifest from {manifest_url}")
    else:
      default_manifest = get_default_manifest()
      if default_manifest:
        manifest_spec = default_manifest
        Logger.info("using default manifest")
    return manifest_spec

  def chunk_document(self, doc_name: str, doc_url: str,
                     doc_filepath: str) -> Tuple[list[str], list[str]]:
    """
    Process doc into chunks for embeddings

    Args:
       doc_name: file name of document
       doc_url: remote url of document
       doc_filepath: local file path of document
    Returns:
       tuple of 
          list of text chunks or None if the document could not be processed
          list of embedding chunks or None
    """

    embed_chunks = None
    text_chunks = None

    Logger.info(f"generating index data for {doc_name}")

    # read doc data and split into text chunks
    # skip any file that can't be read or generates an error
    doc_text_list = None
    try:
      doc_text_list = self.read_doc(doc_name, doc_filepath)
      if doc_text_list is None:
        Logger.error(f"no content read from {doc_name}")
        self.docs_not_processed.append(doc_url)
    except Exception as e:
      Logger.error(f"error reading doc {doc_name}: {e}")
      self.docs_not_processed.append(doc_url)

    if doc_text_list is not None:
      # clean text of escape and other unprintable chars
      doc_text_list = [self.clean_text(x) for x in doc_text_list]
      # combine text from all pages to try to avoid small chunks
      # when there is just title text on a page, for example
      doc_text = "\n".join(doc_text_list)
      # llama-index base class that is used by all parsers
      doc = Document(text=doc_text)
      # a node = a chunk of a page
      chunks = self.doc_parser.get_nodes_from_documents([doc])
      # remove any empty chunks
      chunks = [c for c in chunks if c.metadata["text"].strip() != ""]
      # this is a sentence parser with overlap --
      # each text chunk will include the specified
      # number of sentences before and after the current sentence
      embed_chunks = [c.metadata["text"] for c in chunks]
      text_chunks = [c.metadata["window_text"] for c in chunks]

      if all(element == "" for element in text_chunks):
        Logger.warning(f"All extracted pages from {doc_name} are empty.")
        self.docs_not_processed.append(doc_url)
      else:
        Logger.info(f"generated {len(text_chunks)} text chunks for {doc_name}")

    return text_chunks, embed_chunks

  def chunk_document_multimodal(self,
                           doc_name: str,
                           doc_url: str,
                           doc_filepath: str) -> \
                            List[object]:
    """
    Process a file document into multimodal chunks (b64 and text) for embeddings

    Args:
       doc_name: file name of document
       doc_url: remote url of document
       doc_filepath: local file path of document
    Returns:
       array where each item is an object representing a page of the document
       contains two properties for image b64 data & text chunks 
       or None if the document could not be processed
    """
    Logger.info(f"generating index data for {doc_name}")

    # Confirm that this is a valid file type
    allowed_image_types = ["png", "jpeg", "jpg", "bmp", "gif"]
    try:
      doc_extension = doc_name.split(".")[-1]
      doc_extension = doc_extension.lower()
      if doc_extension != "pdf" and doc_extension not in allowed_image_types:
        raise ValueError(f"{doc_name} must be a PDF, PNG, JPG, BMP, or GIF")
      # TODO: Insert elif statements to check for additional types of
      # videos (AVI, MP4, MOV, etc), and audio (MP3, WAV, etc)
    except Exception as e:
      Logger.error(f"error reading doc {doc_name}: {e}")

    doc_chunks = []
    try:

      # Get bucket name & the doc file path within bucket
      if doc_url.startswith("https://storage.googleapis.com/"):
        bucket_parts = unquote(\
          doc_url.split("https://storage.googleapis.com/")[1]).split("/")
      elif doc_url.startswith("gs://"):
        bucket_parts = unquote(doc_url.split("gs://")[1]).split("/")
      else:
        raise ValueError(f"Invalid Doc URL: {doc_url}")

      bucket_name = bucket_parts[0]
      bucket_folder = "/".join(bucket_parts[1:-1]) \
        if len(bucket_parts) > 2 else None

      # Determine bucket folder to store all chunk docs created
      # Add time-in-ms_randint to ensure that that folders are unique
      chunk_ext_i = bucket_parts[-1].rfind(".")
      chunk_bucket_folder = bucket_parts[-1][:chunk_ext_i]+"_"+\
        str(round(time() * 1000))+"_"+str(randint(1000,9999))
      if bucket_folder:
        chunk_bucket_folder = f"{bucket_folder}/{chunk_bucket_folder}"

      # If doc is a PDF, convert it to an array of PNGs for each page
      allowed_image_types = ["png", "jpg", "jpeg", "bmp", "gif"]
      if doc_extension == "pdf":

        with tempfile.TemporaryDirectory() as path:
          png_array = convert_from_path(doc_filepath, output_folder=path)

        # Open PDF and iterate over pages
        with open(doc_filepath, "rb") as f:
          reader = PdfReader(f)
          num_pages = len(reader.pages)
          Logger.info(f"Reading pdf doc {doc_name} with {num_pages} pages")
          for i in range(num_pages):
            # Create a pdf file for the page and chunk into contextual_text
            pdf_doc = self.create_pdf_page(reader.pages[i], doc_filepath, i)
            contextual_text = self.extract_contextual_text(pdf_doc["filename"],
                                                  pdf_doc["filepath"], doc_url)

            # Take PNG version of page and convert to b64
            png_doc_filepath = \
              ".png".join(pdf_doc["filepath"].rsplit(".pdf", 1))
            png_array[i].save(png_doc_filepath, format="png")
            png_b64 = self.extract_b64(png_doc_filepath)

            # Upload to Google Cloud Bucket and return gs URL
            png_url = gcs_helper.upload_to_gcs(self.storage_client,
                                               bucket_name,
                                               png_doc_filepath,
                                               chunk_bucket_folder)

            # Clean up temp files
            os.remove(pdf_doc["filepath"])
            os.remove(png_doc_filepath)

            # Push chunk object into chunk array
            chunk_obj = {
              "image": png_b64,
              "image_url": png_url,
              "text": contextual_text
            }
            doc_chunks.append(chunk_obj)
      elif doc_extension in allowed_image_types:
        # TODO: Convert image file into something text readable (pdf, html, ext)
        # So that we can extract text chunks

        # Get text associated with the document
        contextual_text = self.extract_contextual_text(doc_name,
                                          doc_filepath, doc_url)

        # Get b64 for the document
        image_b64 = self.extract_b64(doc_filepath)

        # Push chunk object into chunk array
        chunk_obj = {
          "image": image_b64,
          "image_url": doc_url,
          "text": contextual_text
        }
        doc_chunks.append(chunk_obj)

      # TODO: Insert elif statements to chunk additional types of
      # videos (AVI, MP4, MOV, etc), and audio (MP3, WAV, etc)
      # - For images, set "image" and "text" fields of chunk_obj
      # - For video and audio, set "timestamp_start" and "timestamp_stop"
      # fields of chunk_obj

    except Exception as e:
      Logger.error(f"error processing doc {doc_name}: {e}")
      Logger.error(traceback.format_exc())

    # Return array of page data
    return doc_chunks

  def extract_contextual_text(self, doc_name: str, doc_filepath: str, \
        doc_url: str) -> str:
    """
    Extract the contextual text for a multimodal document

    Args:

      doc_name: The name of the doc we are reading the data from
      doc_filepath: string filepath of the doc we are reading the data from
      doc_url: The url of the doc we are reading the data from
    Returns:
      str containing the contextual_text of a multimodal doc
    """
    #chunk_document returns 2 outputs, text_chunks and contextual_text.
    #Each element of text_chunks has the same info as its corresponding
    #element in contextual_text, but is padded with adjacent sentences
    #before and after. Use the 2nd output here (contextual_text).
    _, contextual_text = self.chunk_document(doc_name,
                                          doc_url, doc_filepath)

    # Format text if not None
    if contextual_text is not None:
      contextual_text = [string.strip() for string in contextual_text]
      contextual_text = " ".join(contextual_text)

      #TODO: Consider all characters in my_contextual_text,
      #not just the first 1024
      contextual_text = contextual_text[0:1023]

    return contextual_text

  def extract_b64(self, doc_filepath: str) -> str:
    """
    Extract the b64 a multimodal document

    Args:
      doc_filepath: string filepath of the doc we are reading the data from
    Returns:
      str containing b64 of the doc
    """
    # Take the doc and convert it to b64
    with open(doc_filepath, "rb") as f:
      doc_bytes = f.read()
    doc_b64 = b64encode(doc_bytes).decode("utf-8")
    return doc_b64

  @classmethod
  def text_to_sentence_list(cls, text: str) -> List[str]:
    """
    Split text into sentences.
    In this class we assume generic text.
    Subclasses may do additional transformation (e.g. html to text).
    """
    return text_helper.text_to_sentence_list(text)

  @classmethod
  def clean_text(cls, text: str) -> List[str]:
    """
    Produce clean text from text extracted from source document.
    In this class we assume generic text.
    Subclasses may do additional transformation (e.g. html to text).
    """
    return text_helper.clean_text(text)

  @staticmethod
  def read_doc(doc_name: str, doc_filepath: str) -> List[str]:
    """
    Read document and return content as a list of strings

    Args:
      doc_name: name of document
      doc_filepath: local file path
    Returns:
      doc content as a list of strings
    """
    doc_extension = doc_name.split(".")[-1]
    doc_extension = doc_extension.lower()
    doc_text_list = None
    loader = None

    text_doc_extensions = ["txt", "html", "htm"]

    if doc_extension in text_doc_extensions:
      with open(doc_filepath, "r", encoding="utf-8") as f:
        doc_text = f.read()
      doc_text_list = [doc_text]
    elif doc_extension == "csv":
      loader = CSVLoader(file_path=doc_filepath)
    elif doc_extension == "pdf":
      # read PDF into array of pages
      doc_text_list = []
      with open(doc_filepath, "rb") as f:
        reader = PdfReader(f)
        num_pages = len(reader.pages)
        Logger.info(f"Reading pdf file {doc_name} with {num_pages} pages")
        for page in range(num_pages):
          doc_text_list.append(reader.pages[page].extract_text())
        Logger.info(f"Finished reading pdf file {doc_name}")
    elif doc_extension in ["docx", "pptx", "ppt", "pptm"]:
      doc_text_list = []
      docs = SimpleDirectoryReader(
          input_files=[doc_filepath]
      ).load_data()
      # each document is read as one chunk, but do this for clarity
      for d in docs:
        doc_text_list.append(d.text)
      Logger.info(f"Finished reading file {doc_name}")
    else:
      # return None if doc type not supported
      Logger.error(
          f"Cannot read {doc_name}: unsupported extension {doc_extension}")
      pass

    if loader is not None:
      langchain_document = loader.load()
      doc_text_list = [section.page_content for section in langchain_document]

    return doc_text_list

  @staticmethod
  def create_pdf_page(page: PageObject, doc_filepath: str,
                       page_index: int) -> List[str]:
    """
    Read pypdf PageObject and create a new pdf file for that PageObject

    Args:
      page: PdfReader page object representing a page from a pdf file
      doc_filepath: string filepath of the pdf we are reading the page from
      page_index: int index for the page of doc_filepath file we are on
    Returns:
      obj containing strings of the filename and filepath of new pdf
    """

    # Get file name and temp folder path
    doc_folder_i = doc_filepath.rfind("/")
    if doc_folder_i == -1:
      doc_folder = ""
    else:
      doc_folder = doc_filepath[:doc_folder_i+1]
    doc_file = doc_filepath[doc_folder_i+1:]

    # create a new PDF file and add the cropped page to the new PDF file
    page_copy = copy(page)
    page_pdf = PdfWriter()
    page_pdf.add_page(page_copy)

    # write the new PDF file to a file
    page_pdf_filename = str(page_index) + doc_file
    page_pdf_filepath = doc_folder + page_pdf_filename
    with open(page_pdf_filepath, "wb") as f:
      page_pdf.write(f)

    return {
      "filename": page_pdf_filename,
      "filepath": page_pdf_filepath
    }
