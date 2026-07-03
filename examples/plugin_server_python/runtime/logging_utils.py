from __future__ import annotations

import logging
import os
import sys


def setup_logging() -> None:
    level_name = os.getenv("ARTISAN_PLUGIN_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )


logger = logging.getLogger("artisan.plugin_server_python")
