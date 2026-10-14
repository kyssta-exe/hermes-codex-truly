"""Which Codex models can be widened, to what, and on what evidence.

Two numbers matter per model:

* **advertised** — ``context_window`` from the live catalog. Hermes uses this
  directly, and only bumps it when it reads exactly the legacy 272_000.
* **published ceiling** — ``max_context_window``. Hermes caps any bump here.

Neither is the truth. Measured against the live backend on 2026-10-14,
``gpt-6.1-sol`` accepted 919_956 input tokens and refused 930_151 — roughly
1.05M total context minus the model's 128K output reserve — so the catalog
under-reports by 30-80% depending on the model. ``MEASURED`` records what was
actually sent to the API, so the plugin's default is a reading, not a guess.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MEASUREMENT_DATE = "2026-10-14"

# Direct measurements against POST {base}/responses. "accepted" is the largest
# input the backend completed; "rejected" is the smallest it refused with
# context_length_exceeded. The real ceiling lies between them.
MEASURED: dict[str, dict[str, Any]] = {
    "gpt-6.1-sol": {
        "accepted": 919_956,
        "rejected": 930_151,
        "date": MEASUREMENT_DATE,
        "note": "bracketed by bisection on the live Codex OAuth route",
    },
}

# Hermes core's own live-verified constant for the rest of the family
# (agent/model_metadata.py, _CODEX_OAUTH_VERIFIED_ABOVE_ADVERTISED_*). Recorded
# here so the plugin can say where its default comes from per model instead of
# implying it measured everything itself.
CORE_VERIFIED = 900_000


def is_widenable(slug: str) -> bool:
    """True when Hermes itself recognises ``slug`` as eligible for a large window."""
    try:
        from agent.model_metadata import is_codex_900k_base

        return bool(is_codex_900k_base(slug))
    except Exception:  # pragma: no cover - core moved/renamed
        logger.debug("is_codex_900k_base unavailable", exc_info=True)
        return False


def variant_id(slug: str) -> str:
    """The ``-900k`` picker variant Hermes synthesises for an eligible base slug."""
    try:
        from agent.model_metadata import CODEX_CONTEXT_VARIANT_SUFFIX

        return f"{slug}{CODEX_CONTEXT_VARIANT_SUFFIX}"
    except Exception:  # pragma: no cover
        return f"{slug}-900k"


def measured_ceiling(slug: str) -> dict[str, Any] | None:
    """The measurement for ``slug`` if one exists, else None."""
    return MEASURED.get(slug)


def managed_ids(
    catalog_slugs: list[str], *, apply_to_base_slugs: bool, only: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """``[(base_slug, override_model_id), ...]`` for every model worth widening.

    Returns the ``-900k`` variant of each eligible slug, and the base slug too
    when the user opted in — preserving Hermes' own opt-in semantics by default,
    since a large window burns subscription quota faster.
    """
    wanted = {item.lower() for item in only}
    out: list[tuple[str, str]] = []
    for slug in catalog_slugs:
        clean = str(slug or "").strip()
        if not clean or clean.endswith("-900k"):
            continue
        if wanted and clean.lower() not in wanted:
            continue
        if not is_widenable(clean):
            continue
        out.append((clean, variant_id(clean)))
        if apply_to_base_slugs:
            out.append((clean, clean))
    return out
