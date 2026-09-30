import sys

from loguru import logger

# An MCP server over stdio owns stdout: anything written there that is not a protocol
# message corrupts the stream. Logs therefore go to stderr, which the client shows as
# server output.
LOG_FORMAT = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {name}:{line} - {message}"


def configure_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, format=LOG_FORMAT)
