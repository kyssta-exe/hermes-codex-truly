"""``codex_truly`` — lets the agent inspect and fix its own Codex context window.

The agent is the one that hits the wall, so it should be able to see the numbers
and widen the window itself. Rules from the plugin guide applied literally:
``handler(args, **kwargs) -> str``, always a JSON string, never raises.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import images, overrides, probe, report
from .config import CodexTrulyConfig

logger = logging.getLogger(__name__)

SCHEMA = {
    "name": "codex_truly",
    "description": (
        "Inspect and fix the context window of the Codex/ChatGPT-subscription models this "
        "session runs on. The Codex OAuth catalog under-reports the window (272K or 500K "
        "advertised, 872K published ceiling) while the backend accepts ~920K of input, so a "
        "Codex subscriber silently loses most of their context. "
        "action='status' reports, per model, what the catalog advertises, what Hermes "
        "currently resolves, and what the backend was measured to accept. "
        "action='apply' writes the scoped model_overrides entry that grants the real window "
        "(dry_run=true previews). action='revert' removes it. action='probe' re-measures the "
        "true ceiling live against the API — this spends subscription quota, so only run it "
        "when the user asks. action='image' generates an image through the Codex "
        "subscription (no OpenAI API key). Use this whenever the user asks why their Codex "
        "context is small, or wants it bigger, or wants images generated without an API key."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "apply", "revert", "probe", "image"],
                "description": "What to do. 'status' is read-only and free.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "For action='apply': report the change without writing config.yaml.",
            },
            "target_context_window": {
                "type": "integer",
                "description": "For action='apply': window to grant each model (272000-918000). "
                               "Defaults to the plugin setting (900000).",
            },
            "model": {
                "type": "string",
                "description": "For action='probe': the Codex model id to measure "
                               "(default gpt-6.1-sol).",
            },
            "prompt": {
                "type": "string",
                "description": "For action='image': what to draw.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["landscape", "square", "portrait"],
                "description": "For action='image': canvas shape (default landscape).",
            },
        },
        "required": ["action"],
    },
}


def _config(target: Any = None) -> CodexTrulyConfig:
    from . import runtime

    ctx = runtime.get_ctx()
    config = CodexTrulyConfig.from_ctx(ctx) if ctx is not None else CodexTrulyConfig()
    if target is not None:
        config.target_context_window = target
        config.__post_init__()
    return config


def codex_truly(args: dict, **kwargs: Any) -> str:
    """Dispatch one action; every path returns JSON and none raises."""
    action = str((args or {}).get("action") or "status").strip().lower()
    target = (args or {}).get("target_context_window")
    try:
        if action == "status":
            status = report.collect(_config())
            return json.dumps({
                "ok": True,
                "summary": report.summary(status),
                "signed_in": status["signed_in"],
                "auth_error": status["auth_error"],
                "target_context_window": status["config"]["target_context_window"],
                "safety_ceiling": status["safety_ceiling"],
                "measured_on": status["measured_on"],
                "models": status["models"],
                "model_overrides": status["model_overrides"],
                "image_gen": status["image_gen"],
                "reality_check": (
                    "Full 1M of input is not available on the Codex OAuth route: the models "
                    "advertise ~1.05M total context with a 128K output reserve, leaving ~920K "
                    "of input. ~900K is the safe ceiling."
                ),
            })
        if action == "apply":
            payload = overrides.apply_overrides(
                _config(target), dry_run=bool((args or {}).get("dry_run")))
            return json.dumps(payload, default=str)
        if action == "revert":
            return json.dumps(overrides.revert_overrides(_config()), default=str)
        if action == "probe":
            model = str((args or {}).get("model") or "gpt-6.1-sol").strip()
            return json.dumps(probe.probe_model(model), default=str)
        if action == "image":
            prompt = str((args or {}).get("prompt") or "").strip()
            aspect = str((args or {}).get("aspect_ratio") or "landscape").strip().lower()
            return json.dumps(images.generate(prompt, aspect), default=str)
        return json.dumps({"ok": False, "error": f"unknown action {action!r}"})
    except Exception as exc:  # never break the agent loop
        logger.debug("codex_truly failed", exc_info=True)
        return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
