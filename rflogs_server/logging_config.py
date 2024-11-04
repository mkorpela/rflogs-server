import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import structlog

LOG_FILE = os.environ.get("LOG_FILE_PATH", "/var/log/rflogs/rflogs.log")
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 7  # Keep 7 backup files


def configure_logging():
    # Ensure the log directory exists
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    # Create a RotatingFileHandler
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(logging.StreamHandler(sys.stdout))
    root_logger.info("Logging system initialized")


# Call configure_logging at module level
configure_logging()


def get_logger(name: str):
    """
    Get a logger instance for the given name.

    :param name: The name of the logger, typically __name__ of the module
    :return: A structured logger instance
    """
    return structlog.get_logger(name)
