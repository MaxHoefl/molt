"""The server: eleven tools, two prompts, three resources, and one file that writes.

Nothing is decided here. `create_mcp_server` wires the three routers onto a FastMCP
instance and returns it; everything the server actually does lives behind the tool
functions, which is what lets the whole audit be exercised in tests without a client
or a transport.

    uv run python -m src.server             # http, default: 127.0.0.1:8000/mcp
    MOLT_SERVER_TRANSPORT=stdio uv run python -m src.server   # stdio, one subprocess per client
    uv run fastmcp dev src/server.py        # the inspector
"""

from fastmcp import FastMCP

from src.config.app_config import AppConfig
from src.config.log_config import configure_logging, logger
from src.di import acquire_app_config
from src.routers.prompts import register_mcp_prompts
from src.routers.resources import register_mcp_resources
from src.routers.tools import register_mcp_tools


def create_mcp_server(config: AppConfig | None = None) -> FastMCP:
    config = config or acquire_app_config()
    app = FastMCP(name=config.app_name, version=config.version)
    register_mcp_tools(app)
    register_mcp_resources(app)
    register_mcp_prompts(app)
    return app


def main() -> None:
    configure_logging()
    config = acquire_app_config()
    logger.info(f"Starting {config.app_name} {config.version}")
    server = create_mcp_server(config)
    if config.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=config.transport, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
