import os
from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.constants import ENV_PREFIX, MOLT_VERSION


class Environment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


def locate_env_file() -> Path:
    active_env = Environment(os.environ.get(f"{ENV_PREFIX}ENVIRONMENT", Environment.LOCAL))
    env_file = Path(__file__).parents[2].joinpath(f"{active_env.value}.env").absolute()
    if not env_file.exists():
        raise FileNotFoundError(f"Environment file {env_file} not found")
    return env_file


class AppConfig(BaseSettings):
    """Server identity, and where the two SQLite files live.

    Defaults are deliberate: the server must start with no env file at all, because
    the first thing anyone does with an MCP server is point a client at it before
    configuring anything.
    """

    # One env file serves every settings class in the server, so each of them must
    # ignore the keys that belong to the others rather than refusing to load.
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "molt"
    version: str = MOLT_VERSION
    cache_path: str = "~/.molt/molt.db"
    memory_path: str = "~/.molt/molt.db"

    # http keeps the server running in its own terminal, printing every tool call
    # live, instead of being spawned invisibly per-client under stdio.
    transport: str = "http"
    host: str = "127.0.0.1"
    port: int = 8000
