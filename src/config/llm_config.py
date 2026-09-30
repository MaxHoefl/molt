from functools import cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config.app_config import locate_env_file
from src.constants import DEFAULT_LLM_MODEL, ENV_PREFIX
from src.exceptions import ToolException


class LlmConfig(BaseSettings):
    """Which model an agentic tool talks to, and how.

    `model` is a langchain model string — "<provider>:<model-id>", e.g.
    "anthropic:claude-opus-5", "openai:gpt-5.5", "ollama:llama3.1". Swapping
    providers is therefore a one-line change in the env file.
    """

    model_config = SettingsConfigDict(extra="ignore")

    model: str = DEFAULT_LLM_MODEL
    # Unset, not zero. Reasoning models — anthropic:claude-opus-5 among them — reject
    # `temperature` outright with a 400, so a sampling default that looks harmless
    # makes every agentic tool fail on the default model. Anything left as None is
    # dropped in build_chat_model rather than forwarded, which keeps the provider's
    # own default in charge.
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = 120.0
    max_retries: int = 2

    @property
    def provider(self) -> str:
        return self.model.split(":", 1)[0] if ":" in self.model else "unknown"


def load_llm_config(tool_name: str) -> LlmConfig:
    """Resolves one tool's LLM settings from the environment.

    Three layers, most specific first: MOLT_SERVER_<TOOL>_LLM_* overrides
    MOLT_SERVER_DEFAULT_LLM_*, which overrides the field defaults above. This is
    what makes per-tool experimentation cheap — point every tool at a new model
    with one variable, or override a single tool without touching the rest.
    """
    shared = read_settings(f"{ENV_PREFIX}DEFAULT_LLM_")
    specific = read_settings(f"{ENV_PREFIX}{tool_name.upper()}_LLM_")
    merged = shared.model_dump() | {
        field: getattr(specific, field) for field in specific.model_fields_set
    }
    return LlmConfig(**merged)


def read_settings(env_prefix: str) -> LlmConfig:
    try:
        env_file = locate_env_file()
    except FileNotFoundError:
        env_file = None
    return LlmConfig(_env_file=env_file, _env_prefix=env_prefix, _env_file_encoding="utf-8")


def build_chat_model(config: LlmConfig) -> BaseChatModel:
    """Instantiates the configured chat model, whatever provider it names.

    Only settings that were actually configured are forwarded. Providers disagree
    about which sampling knobs they still accept — Anthropic's reasoning models
    return a 400 for `temperature` — so passing an unset value is not a no-op, it
    is a request that can fail. Omitting it leaves the provider's default in place.
    """
    optional = {
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "timeout": config.timeout_seconds,
    }
    try:
        return init_chat_model(
            config.model,
            max_retries=config.max_retries,
            **{name: value for name, value in optional.items() if value is not None},
        )
    except ImportError as e:
        raise ToolException(
            f"Provider package for '{config.model}' is not installed: {e}. "
            f"Install the matching langchain integration (e.g. langchain-{config.provider})."
        ) from e
    except ValueError as e:
        raise ToolException(f"Could not initialize chat model '{config.model}': {e}") from e


@cache
def chat_model_for(tool_name: str) -> BaseChatModel:
    config = load_llm_config(tool_name)
    return build_chat_model(config)
