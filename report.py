"""The status picture: what the catalog advertises, what this plugin has applied,
and what the backend was measured to accept.

Deliberately reports only what can be known without a credential: the catalog is
fetched through Hermes's credentialed request route (see :mod:`codex_api`), the
applied overrides come from ``config.yaml``, and the measured numbers come from
``hermes codex-truly probe``. The effective window for a *session* is resolved by
Hermes at startup — reported here as the override when one is set, and otherwise
bracketed by the catalog's own advertised/ceiling pair rather than predicted.
"""

from __future__ import annotations

import logging
from typing import Any

from . import codex_api, images, overrides, targets
from .config import MAX_CONTEXT_WINDOW, CodexTrulyConfig, describe

logger = logging.getLogger(__name__)


def _fmt(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else "—"


def _window_of(entry: Any) -> Any:
    """The window inside a `model_overrides` entry.

    Entries are mappings (`{context_window: N}`); a bare scalar is accepted too so
    a config written by an older or hand-edited file still renders.
    """
    if isinstance(entry, dict):
        return entry.get("context_window")
    return entry


def _applied_overrides(config: CodexTrulyConfig) -> dict[str, Any]:
    """``{model_id: window}`` for the first configured provider key."""
    raw = overrides.current_overrides()
    if not raw:
        return {}
    bucket = raw.get(config.provider_key_list[0])
    if not isinstance(bucket, dict):
        return {}
    return {mid: _window_of(entry) for mid, entry in bucket.items()}


def collect(config: CodexTrulyConfig) -> dict[str, Any]:
    """Everything the CLI, slash command and tool report, in one dict."""
    entries, catalog_error = codex_api.fetch_catalog()
    applied = _applied_overrides(config)

    managed = targets.managed_ids(
        [str(e.get("slug") or "") for e in entries] if entries else _fallback_slugs(),
        apply_to_base_slugs=config.apply_to_base_slugs,
        only=config.model_list,
    )

    rows: list[dict[str, Any]] = []
    for base, model_id in managed:
        entry = codex_api.catalog_row(entries, base) or {}
        measurement = targets.measured_ceiling(base) or {}
        window = applied.get(model_id)
        rows.append({
            "model": model_id,
            "base": base,
            "advertised": entry.get("context_window"),
            "catalog_ceiling": entry.get("max_context_window"),
            "catalog_promise": codex_api.promised_window(entry),
            "override": window,
            "measured_accepted": measurement.get("accepted"),
            "measured_rejected": measurement.get("rejected"),
            "target": config.target_context_window,
            "widenable": (
                (window or 0) < config.target_context_window
                and codex_api.promised_window(entry) < config.target_context_window
            ),
        })

    return {
        "ok": True,
        "signed_in": bool(entries),
        "auth_error": catalog_error,
        "catalog_error": catalog_error,
        "config": describe(config),
        "safety_ceiling": MAX_CONTEXT_WINDOW,
        "measured_on": targets.MEASUREMENT_DATE,
        "core_verified_default": targets.CORE_VERIFIED,
        "models": rows,
        "model_overrides": applied,
        "plugin_written": overrides.remembered(),
        "image_gen": images.provider_state(),
    }


def _fallback_slugs() -> list[str]:
    """Curated Codex model ids, used only when live discovery is unavailable."""
    try:
        from hermes_cli.codex_models import DEFAULT_CODEX_MODELS

        return list(DEFAULT_CODEX_MODELS)
    except Exception:  # pragma: no cover
        return ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna"]


def text_report(status: dict[str, Any]) -> str:
    """A compact human report — what ``hermes codex-truly status`` prints."""
    config = status.get("config", {})
    lines: list[str] = [
        "Codex Truly — real context windows for ChatGPT/Codex subscriptions",
        "",
    ]
    if not status.get("signed_in"):
        lines.append(f"  NOT SIGNED IN — {status.get('auth_error')}")
    lines.append(f"  target window       : {_fmt(config.get('target_context_window'))} "
                 f"(safety ceiling {_fmt(status.get('safety_ceiling'))})")
    lines.append(f"  base slugs widened  : {config.get('apply_to_base_slugs')}")
    lines.append("")
    lines.append(f"  {'model':<22}{'advertised':>12}{'catalog max':>13}{'override':>12}"
                 f"{'measured ok':>13}{'target':>10}")
    for row in status.get("models", []):
        lines.append(
            f"  {row['model']:<22}{_fmt(row['advertised']):>12}{_fmt(row['catalog_ceiling']):>13}"
            f"{_fmt(row['override']):>12}{_fmt(row['measured_accepted']):>13}"
            f"{_fmt(row['target']):>10}"
        )
    if not status.get("models"):
        lines.append("  (no widenable Codex models found — sign in, or set `models` explicitly)")
    lines.append("")
    if status.get("model_overrides"):
        lines.append(f"  {len(status['model_overrides'])} override(s) in config.yaml "
                     "— a new session gets these:")
        for model_id, window in sorted(status["model_overrides"].items()):
            lines.append(f"    {model_id} -> {_fmt(window)}")
    else:
        lines.append("  no overrides in config.yaml — run `hermes codex-truly apply`")
    image = status.get("image_gen", {})
    lines.append(f"  image_gen provider  : {image.get('provider')} "
                 f"(Codex-backed: {image.get('codex_backed')})")
    lines.append("")
    lines.append("  'advertised' is the catalog's own context_window; 'catalog max' is the")
    lines.append("  ceiling Hermes clamps to. 'override' is what this plugin wrote — an override")
    lines.append("  wins, so a new session gets it. 'measured ok' is the largest input the live")
    lines.append("  backend accepted in a probe; the true limit sits between it and the smallest")
    lines.append("  rejection. Full 1M of input is not available on this route: ~1.05M total")
    lines.append("  context is shared with a 128K output reserve, leaving ~920K of input.")
    return "\n".join(lines)


def summary(status: dict[str, Any]) -> str:
    """One-line summary for the slash command."""
    if not status.get("signed_in"):
        first = str(status.get("auth_error") or "").splitlines()[0]
        return f"Codex Truly: not signed in — {first}"
    rows = status.get("models", [])
    if not rows:
        return "Codex Truly: signed in, no widenable Codex models found"
    gaps = [r for r in rows if r.get("widenable")]
    target = status.get("config", {}).get("target_context_window")
    if gaps:
        return (
            f"Codex Truly: {len(rows)} Codex models managed, {len(gaps)} below the "
            f"{_fmt(target)} target (catalog caps them at "
            f"{_fmt(max(r.get('catalog_ceiling') or 0 for r in gaps))}). "
            "Run `hermes codex-truly apply`."
        )
    return (
        f"Codex Truly: {len(rows)} Codex models at the {_fmt(target)} target. "
        "Image generation runs on the Codex subscription."
    )
