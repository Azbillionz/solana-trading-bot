"""
Logging setup using loguru with console + rotating file output.
"""
import sys
from loguru import logger
import config


def setup_logger() -> None:
    logger.remove()

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    logger.add(sys.stdout, format=fmt, level=config.LOG_LEVEL, colorize=True)

    logger.add(
        "logs/bot_{time:YYYY-MM-DD}.log",
        format=fmt,
        level=config.LOG_LEVEL,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,
    )

    logger.info("Logger initialised — level={}", config.LOG_LEVEL)


setup_logger()

__all__ = ["logger"]
