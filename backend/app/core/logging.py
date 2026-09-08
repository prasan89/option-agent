import logging
import sys

from app.core.config import settings


def configure_logging() -> None:
    """Configure application logging so exceptions are visible in CF/uvicorn logs."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    # Uvicorn may already install handlers, so basicConfig() can become a no-op.
    # Ensure application logs still have an explicit stderr handler.
    if not any(getattr(handler, "_option_agent_handler", False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler._option_agent_handler = True
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)

    logging.getLogger("app").setLevel(level)
