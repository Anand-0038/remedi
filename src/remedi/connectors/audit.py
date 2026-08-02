"""Shared tool-call audit log for judge-visible DataHub operations."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from remedi.models.incident import ToolCall


class ToolAudit:
    def __init__(self) -> None:
        self._calls: list[ToolCall] = []

    def clear(self) -> None:
        self._calls.clear()

    def record(
        self,
        tool: str,
        *,
        args: dict[str, Any] | None = None,
        status: str = "ok",
        detail: str = "",
        duration_ms: int = 0,
    ) -> ToolCall:
        call = ToolCall(
            tool=tool,
            args=args or {},
            status=status,  # type: ignore[arg-type]
            detail=detail,
            duration_ms=duration_ms,
        )
        self._calls.append(call)
        return call

    @contextmanager
    def track(self, tool: str, **args: Any) -> Iterator[dict[str, Any]]:
        meta: dict[str, Any] = {"status": "ok", "detail": ""}
        t0 = time.perf_counter()
        try:
            yield meta
        except Exception as exc:  # noqa: BLE001
            meta["status"] = "error"
            meta["detail"] = str(exc)
            raise
        finally:
            self.record(
                tool,
                args={k: v for k, v in args.items() if not str(k).startswith("_")},
                status=meta.get("status", "ok"),
                detail=meta.get("detail", ""),
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

    @property
    def calls(self) -> list[ToolCall]:
        return list(self._calls)
