from __future__ import annotations

from typing import Any


class RequestError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str | None = None,
        phase: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.phase = phase
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": False,
            "error": self.message,
            "status": self.status,
        }
        if self.code:
            payload["code"] = self.code
        if self.phase:
            payload["phase"] = self.phase
        if self.details:
            payload["details"] = self.details
        return payload
