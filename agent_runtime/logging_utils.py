from __future__ import annotations

import logging
import os
import sys

from loguru import logger as _logger


class LoguruCompatLogger:
    def __init__(self, name: str) -> None:
        self.name = name
        self._logger = _logger.bind(component=name)

    def info(self, message: str, *args: object, **kwargs: object) -> None:
        self._logger.info(self._format(message, args), **kwargs)

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self._logger.warning(self._format(message, args), **kwargs)

    def error(self, message: str, *args: object, **kwargs: object) -> None:
        self._logger.error(self._format(message, args), **kwargs)

    def exception(self, message: str, *args: object, **kwargs: object) -> None:
        self._logger.exception(self._format(message, args), **kwargs)

    @staticmethod
    def _format(message: str, args: tuple[object, ...]) -> str:
        if not args:
            return message
        try:
            return message % args
        except Exception:
            return " ".join([message, *[str(arg) for arg in args]])


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        _logger.bind(component=record.name).opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    level_name = os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper()
    _logger.remove()
    _logger.add(
        sys.stdout,
        level=level_name,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{extra[component]}</cyan> "
            "<level>{message}</level>"
        ),
    )
    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=getattr(logging, level_name, logging.INFO),
        force=False,
    )


logger = LoguruCompatLogger("agent_runtime")
