"""Write and remove the scoped ``model_overrides`` entries that widen the window.

Hermes resolves a model's context length in a fixed order, and
``model_overrides.<provider>.<model_id>.context_window`` is consulted at step 0b
— *before* the Codex OAuth catalog probe that clamps everything to
``max_context_window``. So an override is the supported way to hand a Codex
subscriber their real window, and it needs no core patch.

Every write goes through ``hermes_cli.config``'s atomic writers on the raw user
config (never the defaults-merged view, which would write the whole default tree
back into config.yaml). Nothing here reads or holds a credential: the catalog
behind a decision comes from :mod:`codex_api`, which asks Hermes to attach the
sign-in.
"""

from __future__ import annotations

import logging
from typing import Any

from . import codex_api, targets
from .config import CodexTrulyConfig

logger = logging.getLogger(__name__)

_STATE_KEY = "applied_overrides"


def config_path():
    from hermes_cli.config import get_config_path

    return get_config_path()


def read_raw() -> dict[str, Any]:
    """The user's own config.yaml, without defaults merged in."""
    from hermes_cli.config import read_user_config_raw

    raw = read_user_config_raw(config_path())
    return raw if isinstance(raw, dict) else {}


def write_raw(data: dict[str, Any], *, replace: bool = False) -> None:
    """Persist the raw user config.

    ``atomic_config_write`` refuses to drop an existing path (so an incomplete
    dict can never delete settings); removing the last override — and with it the
    ``model_overrides`` key — is a deliberate deletion, which is exactly what
    ``atomic_config_replace`` is for.
    """
    from hermes_cli.config import atomic_config_replace, atomic_config_write

    if replace:
        atomic_config_replace(config_path(), data)
    else:
        atomic_config_write(config_path(), data)


def current_overrides(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = read_raw() if raw is None else raw
    section = raw.get("model_overrides")
    return section if isinstance(section, dict) else {}


def plan(config: CodexTrulyConfig) -> dict[str, Any]:
    """Compute the override map without writing anything.

    Returns ``{"overrides": {provider: {model_id: window}}, "rows": [...],
    "skipped": [...], "catalog_error": str|None, "signed_in": bool}``.

    "Already big enough" is decided from the catalog the account is actually
    entitled to — the same numbers Hermes reads — so the plugin never overwrites
    an override the user already has with a smaller one, and never depends on a
    credential of its own to work that out.
    """
    entries, catalog_error = codex_api.fetch_catalog()
    slugs = [str(e.get("slug") or "").strip() for e in entries]
    if not slugs:
        # No catalog (not signed in, offline, or a transient API failure): fall
        # back to Hermes' curated list so the plan still names something.
        slugs = _fallback_slugs()

    managed = targets.managed_ids(
        slugs, apply_to_base_slugs=config.apply_to_base_slugs, only=config.model_list)

    overrides: dict[str, dict[str, int]] = {}
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for base, model_id in managed:
        entry = codex_api.catalog_row(entries, base) or {}
        promised = codex_api.promised_window(entry)
        row = {
            "model": model_id,
            "base": base,
            "advertised": entry.get("context_window"),
            "catalog_ceiling": entry.get("max_context_window"),
            "promised": promised,
            "target": config.target_context_window,
        }
        if promised and promised >= config.target_context_window:
            row["reason"] = f"catalog already promises {promised:,}"
            skipped.append(row)
            continue
        for provider in config.provider_key_list:
            overrides.setdefault(provider, {})[model_id] = config.target_context_window
        rows.append(row)

    return {
        "overrides": overrides,
        "rows": rows,
        "skipped": skipped,
        "catalog_error": catalog_error,
        "signed_in": bool(entries),
    }


def _fallback_slugs() -> list[str]:
    """Curated Codex model ids, used only when live discovery is unavailable."""
    try:
        from hermes_cli.codex_models import DEFAULT_CODEX_MODELS

        return list(DEFAULT_CODEX_MODELS)
    except Exception:  # pragma: no cover
        return ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna"]


def apply_overrides(config: CodexTrulyConfig, *, dry_run: bool = False) -> dict[str, Any]:
    """Write the planned overrides. Returns a report dict; never raises."""
    try:
        planned = plan(config)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if not planned["signed_in"]:
        # Without a credential there is no account-scoped catalog to act on, and
        # the offline fallback would report "already at target" for windows the
        # user does not actually have. Refuse rather than write a guess.
        return {"ok": False, "error": planned["catalog_error"] or "Not signed in to Codex."}

    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "changed": planned["rows"],
        "unchanged": planned["skipped"],
        "catalog_error": planned["catalog_error"],
    }
    if not planned["rows"]:
        result["note"] = (
            "Nothing to change — every managed model is already at or above the target.")
        return result
    if dry_run:
        result["would_write"] = planned["overrides"]
        return result

    raw = read_raw()
    section = raw.setdefault("model_overrides", {})
    if not isinstance(section, dict):  # a malformed value must not lose data
        return {"ok": False, "error": "config.yaml model_overrides is not a mapping; fix it first."}
    for provider, models in planned["overrides"].items():
        bucket = section.setdefault(provider, {})
        if not isinstance(bucket, dict):
            return {"ok": False,
                    "error": f"model_overrides.{provider} is not a mapping; fix it first."}
        for model_id, window in models.items():
            # The override schema is per-model MAPPING with a context_window field
            # (agent/models_dev.py::_override_int reads it); a bare int here is
            # silently ignored and the catalog clamp wins again.
            existing = bucket.get(model_id)
            entry = dict(existing) if isinstance(existing, dict) else {}
            entry["context_window"] = window
            bucket[model_id] = entry

    image_note = _ensure_image_provider(config, raw)
    try:
        write_raw(raw)
    except Exception as exc:
        return {"ok": False, "error": f"could not write config.yaml: {type(exc).__name__}: {exc}"}

    result["written"] = planned["overrides"]
    result["image_provider"] = image_note
    _remember(planned["overrides"])
    return result


def _ensure_image_provider(config: CodexTrulyConfig, raw: dict[str, Any]) -> str:
    """Select core's Codex-backed image provider when nothing is chosen yet.

    An explicit existing choice is reported, never overwritten — the user may
    deliberately be on a paid image API.
    """
    if not config.ensure_image_provider:
        return "skipped (ensure_image_provider is off)"
    section = raw.setdefault("image_gen", {})
    if not isinstance(section, dict):
        return "skipped (image_gen is not a mapping)"
    current = str(section.get("provider") or "").strip()
    if current:
        return f"left as {current!r}"
    section["provider"] = "openai-codex"
    return "set to 'openai-codex' (Codex subscription, no API key)"


def _remember(overrides: dict[str, dict[str, int]]) -> None:
    """Record what we wrote so revert removes exactly our own entries."""
    try:
        from . import runtime

        ctx = runtime.get_ctx()
        if ctx is None:
            return
        stored = ctx.state.get(_STATE_KEY, {}) or {}
        if not isinstance(stored, dict):
            stored = {}
        for provider, models in overrides.items():
            bucket = stored.setdefault(provider, {})
            if isinstance(bucket, dict):
                bucket.update(models)
        ctx.state.set(_STATE_KEY, stored)
    except Exception:
        logger.debug("could not record applied overrides", exc_info=True)


def remembered() -> dict[str, dict[str, int]]:
    try:
        from . import runtime

        ctx = runtime.get_ctx()
        stored = ctx.state.get(_STATE_KEY, {}) if ctx is not None else {}
    except Exception:
        return {}
    return stored if isinstance(stored, dict) else {}


def revert_overrides(config: CodexTrulyConfig, *, purge_all: bool = False) -> dict[str, Any]:
    """Remove the overrides this plugin wrote (``purge_all``: every managed id).

    Only entries whose current value we recognise are removed — an override the
    user edited by hand is left alone unless ``purge_all`` is given.
    """
    raw = read_raw()
    section = raw.get("model_overrides")
    if not isinstance(section, dict):
        return {"ok": True, "removed": {}, "note": "No model_overrides in config.yaml."}

    # Candidates: every id this plugin manages (from the live catalog) plus
    # anything it recorded writing — the record survives a catalog change.
    candidates = {model_id for _base, model_id in _all_managed(config)}
    for models in remembered().values():
        if isinstance(models, dict):
            candidates.update(models)

    removed: dict[str, list[str]] = {}
    for provider in config.provider_key_list:
        bucket = section.get(provider)
        if not isinstance(bucket, dict):
            continue
        for model_id in list(bucket):
            if model_id not in candidates:
                continue
            entry = bucket.get(model_id)
            current = entry.get("context_window") if isinstance(entry, dict) else entry
            # Without --all, only remove an entry whose window still matches what
            # we write — a hand-edited value is the user's, not ours.
            if not purge_all and current != config.target_context_window:
                continue
            bucket.pop(model_id)
            removed.setdefault(provider, []).append(model_id)
        if not bucket:
            section.pop(provider, None)
    if not section:
        raw.pop("model_overrides", None)

    try:
        write_raw(raw, replace=True)
    except Exception as exc:
        return {"ok": False, "error": f"could not write config.yaml: {type(exc).__name__}: {exc}"}

    try:
        from . import runtime

        if runtime.get_ctx() is not None:
            runtime.get_ctx().state.set(_STATE_KEY, {})
    except Exception:
        logger.debug("could not clear applied-override record", exc_info=True)

    return {"ok": True, "removed": removed,
            "scope": "all managed ids" if purge_all else "plugin-written entries"}


def _all_managed(config: CodexTrulyConfig) -> list[tuple[str, str]]:
    entries, _error = codex_api.fetch_catalog()
    slugs = [str(e.get("slug") or "").strip() for e in entries] or _fallback_slugs()
    return targets.managed_ids(
        slugs, apply_to_base_slugs=config.apply_to_base_slugs, only=config.model_list)
