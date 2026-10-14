"""Image generation through the ChatGPT/Codex subscription.

Hermes core already ships the backend for this — ``plugins/image_gen/openai-codex``
routes ``image_generate`` at the Codex backend's native ``images/generations`` and
``images/edits`` endpoints on the same OAuth token the models use, so no
``OPENAI_API_KEY`` and no per-image API credit is involved. What a Codex
subscriber usually lacks is the *selection*: ``image_gen.provider`` has to name
that plugin.

So this module checks and drives the existing path rather than reimplementing it:
``provider_state`` reports which provider is wired up and whether it is
Codex-backed, and ``generate`` runs a real generation so the plugin can prove the
route works instead of asserting it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CODEX_IMAGE_PROVIDER = "openai-codex"
CODEX_IMAGE_PLUGIN = "image_gen/openai-codex"
DEFAULT_ASPECT = "landscape"


def _read_config_key(key: str) -> Any:
    try:
        from hermes_cli.config import load_config

        section = load_config().get("image_gen")
        return section.get(key) if isinstance(section, dict) else None
    except Exception:
        logger.debug("could not read image_gen config", exc_info=True)
        return None


def codex_image_plugin_available() -> bool:
    """True when core's Codex image provider plugin is on disk for this install."""
    try:
        from pathlib import Path

        from hermes_constants import get_hermes_home

        bundled = Path(__file__).resolve().parents[3] / "plugins" / CODEX_IMAGE_PLUGIN
        if bundled.is_dir():
            return True
        return (Path(get_hermes_home()) / "plugins" / CODEX_IMAGE_PLUGIN).is_dir()
    except Exception:
        return False


def provider_state() -> dict[str, Any]:
    """What image_generate currently resolves to, and whether it is Codex-backed."""
    provider = str(_read_config_key("provider") or "").strip()
    model = str(_read_config_key("model") or "").strip()
    return {
        "provider": provider or "(unset)",
        "model": model or "(default)",
        "codex_backed": provider == CODEX_IMAGE_PROVIDER,
        "codex_plugin_available": codex_image_plugin_available(),
        "note": (
            "Codex-backed: image_generate bills your ChatGPT subscription."
            if provider == CODEX_IMAGE_PROVIDER
            else "Set image_gen.provider to 'openai-codex' (hermes codex-truly apply does it) "
                 "to generate with your Codex subscription."
        ),
    }


def generate(
    prompt: str, aspect_ratio: str = DEFAULT_ASPECT, *, timeout_hint: str = "~40s",
) -> dict[str, Any]:
    """Run one real image generation through the configured provider.

    Preferred path is the live tool registry, so the agent-facing and CLI paths
    exercise identical provider selection. Outside a session the ``image_generate``
    tool is not registered (it is toolset-gated), so fall back to the same
    resolver the tool itself uses — ``agent.image_gen_registry.get_active_provider``
    — rather than to a second implementation.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "A prompt is required."}

    from . import runtime

    ctx = runtime.get_ctx()
    payload: Any = None
    if ctx is not None:
        try:
            raw = ctx.dispatch_tool(
                "image_generate", {"prompt": prompt, "aspect_ratio": aspect_ratio})
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            logger.debug("registry dispatch failed, falling back to the provider", exc_info=True)
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(payload, dict) and "Unknown tool" in str(payload.get("error") or ""):
            payload = None
    if payload is None:
        payload = _generate_via_provider(prompt, aspect_ratio)

    if not isinstance(payload, dict):
        return {"ok": False, "error": f"Unexpected result: {str(payload)[:300]}"}
    if payload.get("success") is False or payload.get("error"):
        return {"ok": False, "error": payload.get("error") or "generation failed",
                "provider": payload.get("provider"), "raw": payload}
    return {
        "ok": True,
        "image": payload.get("image"),
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "prompt": payload.get("prompt"),
        "aspect_ratio": payload.get("aspect_ratio"),
        "reported_size": payload.get("reported_size") or payload.get("size"),
        "note": f"Generation typically takes {timeout_hint} on gpt-image-2.",
    }


def _generate_via_provider(prompt: str, aspect_ratio: str) -> dict[str, Any]:
    """Call the active provider directly — the CLI runs without a session toolset."""
    try:
        import model_tools  # noqa: F401  (importing it is what discovers plugins)
        from agent.image_gen_registry import get_active_provider

        provider = get_active_provider()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if provider is None:
        return {"error": "No image provider is active. Run `hermes codex-truly apply` "
                         "to select the Codex one, or pick one in `hermes tools`."}
    try:
        return provider.generate(prompt, aspect_ratio)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "provider": provider.name}
