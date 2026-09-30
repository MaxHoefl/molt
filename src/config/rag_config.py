from enum import StrEnum
from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config.app_config import locate_env_file
from src.constants import ENV_PREFIX


class RagBackend(StrEnum):
    CHROMA = "chroma"
    LEXICAL = "lexical"
    OFF = "off"


class RagConfig(BaseSettings):
    """Which retriever the license agent gets, and how much it may bring back.

    `chroma` is the real thing — embeddings, a persistent local collection, semantic
    matching. `lexical` is a dependency-free scorer over the same passages, which is
    what tests use and what the server falls back to when Chroma cannot start. `off`
    removes the tool entirely, which is the honest way to measure whether retrieval
    is buying anything: run the license evals with it on and off and compare the two
    scorecards.
    """

    model_config = SettingsConfigDict(extra="ignore")

    backend: RagBackend = RagBackend.CHROMA
    collection: str = "molt-license-clauses"
    persist_directory: str = "~/.molt/chroma"
    top_k: int = 3


@cache
def load_rag_config() -> RagConfig:
    try:
        env_file = locate_env_file()
    except FileNotFoundError:
        env_file = None
    return RagConfig(_env_file=env_file, _env_prefix=f"{ENV_PREFIX}RAG_", _env_file_encoding="utf-8")
