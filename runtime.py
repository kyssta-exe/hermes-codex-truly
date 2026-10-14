"""Process-wide handles the modules share without importing each other.

``register(ctx)`` stores the PluginContext here so modules reached from a CLI
handler, a slash command and a tool handler all resolve the same context. Kept
deliberately tiny so every other module can import it without cycles.
"""

from __future__ import annotations

from typing import Any

_ctx: Any | None = None


def set_ctx(ctx: Any) -> None:
    global _ctx
    _ctx = ctx


def get_ctx() -> Any | None:
    return _ctx
