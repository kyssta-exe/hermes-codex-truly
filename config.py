"""Typed settings for codex-truly.

``CONFIG_SCHEMA`` mirrors ``plugin.yaml``'s ``config_schema`` key for key — the
desktop Settings form and the manifest suite both read it, and the two copies
are asserted equal in the tests. Every value is read through ``ctx.get_config``,
coerced defensively, and clamped, so a typo in ``config.yaml`` degrades to the
default instead of breaking the plugin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The window Hermes can safely hand a Codex model. 918_000 is the highest value
# backed by measurement: gpt-6.1-sol accepted 919_956 input tokens and refused
# 930_151 (live, 2026-10-14), so 900_000 keeps ~25K of margin and 918_000 stays
# inside the observed-accepted zone with a much thinner one.
MIN_CONTEXT_WINDOW = 272_000
MAX_CONTEXT_WINDOW = 918_000
DEFAULT_CONTEXT_WINDOW = 900_000

VALID_ASPECT_RATIOS = ("landscape", "square", "portrait")

# Mirror of plugin.yaml config_schema. Keep the key sets identical.
CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "enabled": {"type": "bool", "default": True},
    "target_context_window": {"type": "int", "default": DEFAULT_CONTEXT_WINDOW},
    "apply_to_base_slugs": {"type": "bool", "default": False},
    "provider_keys": {"type": "str", "default": "openai-codex"},
    "ensure_image_provider": {"type": "bool", "default": True},
    "models": {"type": "str", "default": ""},
}


_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", ""})


def _as_bool(value: Any, default: bool) -> bool:
    """Recognised words and numbers coerce; anything else degrades to ``default``.

    A typo must not flip a setting — `enabled: ture` silently disabling the
    plugin is worse than ignoring the typo.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed


def _as_list(value: Any) -> tuple[str, ...]:
    """Comma / whitespace separated string or list -> tuple of non-blank items."""
    if isinstance(value, str):
        raw = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        raw = []
    return tuple(item.strip() for item in raw if item and item.strip())


@dataclass
class CodexTrulyConfig:
    """Validated settings. Clamping lives in ``__post_init__`` so a hand-built
    instance behaves exactly like one read from ``config.yaml``."""

    enabled: bool = True
    target_context_window: int = DEFAULT_CONTEXT_WINDOW
    apply_to_base_slugs: bool = False
    provider_keys: str = "openai-codex"
    ensure_image_provider: bool = True
    models: str = ""

    def __post_init__(self) -> None:
        self.enabled = _as_bool(self.enabled, True)
        self.apply_to_base_slugs = _as_bool(self.apply_to_base_slugs, False)
        self.ensure_image_provider = _as_bool(self.ensure_image_provider, True)
        value = _as_int(self.target_context_window, DEFAULT_CONTEXT_WINDOW)
        # Out-of-range is clamped, never rejected: the user asked for "more" or
        # "less", and a broken config must not stop the plugin from working.
        self.target_context_window = max(MIN_CONTEXT_WINDOW, min(MAX_CONTEXT_WINDOW, value))
        keys = _as_list(self.provider_keys) or ("openai-codex",)
        self.provider_keys = ",".join(keys)
        self.models = ",".join(_as_list(self.models))

    @property
    def provider_key_list(self) -> tuple[str, ...]:
        return _as_list(self.provider_keys)

    @property
    def model_list(self) -> tuple[str, ...]:
        return _as_list(self.models)

    @classmethod
    def from_ctx(cls, ctx: Any) -> CodexTrulyConfig:
        """Read every key through ``ctx.get_config`` with the schema default."""
        kwargs: dict[str, Any] = {}
        for key, spec in CONFIG_SCHEMA.items():
            kwargs[key] = ctx.get_config(key, spec["default"])
        return cls(**kwargs)


def describe(config: CodexTrulyConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "target_context_window": config.target_context_window,
        "apply_to_base_slugs": config.apply_to_base_slugs,
        "provider_keys": list(config.provider_key_list),
        "ensure_image_provider": config.ensure_image_provider,
        "models": list(config.model_list),
    }
