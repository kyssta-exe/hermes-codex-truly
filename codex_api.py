"""The Codex backend, reached through Hermes's own credentialed request route.

Two rules shape this module:

* **The plugin never holds the token.** ``hermes_cli.plugin_provider_requests.
  credentialed_provider_request`` attaches the sign-in and enforces the origin
  allowlist, so nothing here touches ``auth.json``, a token file or any other
  credential store. The plugin declares ``requires_auth: [openai-codex]`` in
  ``plugin.yaml`` and Hermes does the rest.
* **Only chatgpt.com.** Requests go to the Codex backend core itself calls. A
  profile whose Codex credential routes to a custom endpoint is refused by the
  helper rather than guessed at.

The catalog endpoint answers ``{"models": []}`` with HTTP 200 unless it is asked
with the account headers — the helper adds those from the sign-in, which is the
other reason this goes through Hermes instead of a hand-built request.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

PROVIDER = "openai-codex"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
MODELS_URL = f"{CODEX_BASE_URL}/models?client_version=99.0.0"
RESPONSES_URL = f"{CODEX_BASE_URL}/responses"

_NOT_SIGNED_IN = (
    "No Codex/ChatGPT sign-in for this Hermes profile. Run `hermes auth add openai-codex` "
    "(or sign in to Codex under Providers in the desktop app)."
)


class BackendError(RuntimeError):
    """A Codex request could not be made — message is user-facing."""


def _send(method: str, url: str, *, json_body: Any = None, headers: dict | None = None,
          timeout: float = 30.0) -> Any:
    """One credentialed request; raises :class:`BackendError` with a readable reason."""
    try:
        from hermes_cli.plugin_provider_requests import (
            ProviderNotSignedIn,
            credentialed_provider_request,
        )
    except Exception as exc:  # pragma: no cover - core moved
        raise BackendError(f"credentialed requests unavailable: {type(exc).__name__}") from exc
    try:
        return credentialed_provider_request(
            PROVIDER, method, url, json=json_body, headers=headers, timeout=timeout)
    except ProviderNotSignedIn as exc:
        raise BackendError(str(exc) or _NOT_SIGNED_IN) from exc
    except PermissionError as exc:
        # Origin refused, or this profile's credential routes to a custom endpoint.
        raise BackendError(str(exc)) from exc


def fetch_catalog(timeout: float = 20.0) -> tuple[list[dict[str, Any]], str | None]:
    """``(entries, error)`` from the account-scoped Codex model catalog."""
    try:
        response = _send("GET", MODELS_URL, timeout=timeout)
    except BackendError as exc:
        return [], str(exc)
    if response.status != 200:
        return [], f"Codex catalog returned HTTP {response.status}: {response.text[:200]}"
    try:
        payload = response.json()
    except ValueError as exc:
        return [], f"Codex catalog was not JSON: {exc}"
    entries = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        return [], "Codex catalog returned no models"
    return [entry for entry in entries if isinstance(entry, dict)], None


def post_responses(body: dict[str, Any], *, headers: dict | None = None,
                   timeout: float = 300.0) -> Any:
    """POST a Responses request to the Codex backend with the sign-in attached."""
    return _send("POST", RESPONSES_URL, json_body=body, headers=headers, timeout=timeout)


def catalog_row(entries: list[dict[str, Any]], slug: str) -> dict[str, Any] | None:
    """The catalog row for ``slug``, matched exactly then case-insensitively."""
    for entry in entries:
        if str(entry.get("slug") or "").strip() == slug:
            return entry
    lowered = slug.lower()
    for entry in entries:
        if str(entry.get("slug") or "").strip().lower() == lowered:
            return entry
    return None


def promised_window(entry: dict[str, Any]) -> int:
    """The largest window the catalog itself promises for a model.

    ``max_context_window`` is the ceiling Hermes will not exceed without an
    override; ``context_window`` is what it uses by default. A model whose promise
    already reaches the target needs no override.
    """
    best = 0
    for key in ("max_context_window", "context_window"):
        value = entry.get(key)
        if isinstance(value, int) and value > best:
            best = value
    return best
