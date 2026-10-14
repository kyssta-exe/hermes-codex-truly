"""Measure the real input ceiling of a Codex model against the live backend.

Whether a request fits is only knowable from the wire: the catalog's numbers are
advisory. An oversized input is refused by the backend with
``context_length_exceeded``, which is the signal this module bisects on.

Requests go through Hermes's credentialed route (:mod:`codex_api`), so the probe
never holds the sign-in. They are sent non-streaming: the answer is either a
completed response carrying ``usage.input_tokens`` or an error carrying the
reason, and there is no SSE framing to reassemble.

Cost note the caller must respect: a refused request never generates and is not
billed, but every *accepted* probe is a real request for that many input tokens
of subscription quota. Bisection from a bracket therefore costs roughly one
accepted request, not one per round.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import codex_api

logger = logging.getLogger(__name__)

# "word " tokenises to ~1 token per unit on these models; calibration against a
# small accepted request replaces the estimate with the measured ratio.
_PAD_UNIT = "word "
_CALIBRATION_UNITS = 4_000
_MIN_OUTPUT_TOKENS = 16  # the backend refuses anything lower
_REJECTION_CODE = "context_length_exceeded"
_REJECTION_PHRASE = "exceeds the context window"


def _body(model: str, units: int) -> dict[str, Any]:
    return {
        "model": model,
        "instructions": "Answer with the single word OK.",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": _PAD_UNIT * units}]}],
        # The Codex backend refuses a non-streaming request outright
        # ("Stream must be set to true"), and it reports an oversized input as a
        # LATE event on this stream rather than an HTTP status. The credentialed
        # helper reads the whole body, so the SSE text arrives intact.
        "stream": True,
        "store": False,
        "tools": [],
        "max_output_tokens": _MIN_OUTPUT_TOKENS,
        "reasoning": {"effort": "low"},
    }


def _error_message(text: str) -> str:
    """The ``error.message`` from an SSE ``event: error`` frame, else the raw head."""
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "error":
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"].strip()[:200]
    return text[:200]


def _is_rejection(text: str) -> bool:
    return _REJECTION_CODE in text or _REJECTION_PHRASE in text


def _stream_input_tokens(text: str) -> int | None:
    """``usage.input_tokens`` from the terminal ``response.completed`` frame."""
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except ValueError:
            continue
        response = payload.get("response") if isinstance(payload, dict) else None
        usage = response.get("usage") if isinstance(response, dict) else None
        tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        if isinstance(tokens, int) and tokens > 0:
            return tokens
    return None


def _classify(status: int, text: str) -> dict[str, Any]:
    """``{accepted, input_tokens, detail}`` from one SSE reply.

    ``accepted`` is None when the failure was not about size — the caller aborts
    rather than recording a bogus bracket point.
    """
    if status != 200:
        return {"accepted": False if _is_rejection(text) else None,
                "input_tokens": None, "detail": f"HTTP {status}: {_error_message(text)}"}
    if _is_rejection(text) or '"response.failed"' in text:
        return {"accepted": False, "input_tokens": None,
                "detail": f"rejected: {_error_message(text)}"}
    return {
        "accepted": True,
        "input_tokens": _stream_input_tokens(text),
        "detail": "completed",
    }


def _send(model: str, units: int, timeout: float) -> dict[str, Any]:
    """One probe request through the credentialed route."""
    try:
        response = codex_api.post_responses(_body(model, units), timeout=timeout)
    except codex_api.BackendError as exc:
        return {"accepted": None, "input_tokens": None, "detail": str(exc)}
    except Exception as exc:  # unexpected transport failure
        return {"accepted": None, "input_tokens": None,
                "detail": f"{type(exc).__name__}: {exc}"}
    return _classify(response.status, response.text)


def _calibrate(model: str, timeout: float) -> float:
    """Measured tokens per padding unit, from one small accepted request."""
    result = _send(model, _CALIBRATION_UNITS, timeout)
    if result.get("accepted") and result.get("input_tokens"):
        return float(result["input_tokens"]) / _CALIBRATION_UNITS
    return 1.0


def probe_model(
    model: str, *, low: int = 872_000, high: int = 1_200_000,
    tolerance: int = 20_000, max_rounds: int = 6, timeout: float = 300.0,
) -> dict[str, Any]:
    """Bisect ``[low, high]`` for the largest accepted input, in tokens.

    ``low`` is assumed accepted and is not probed (default: the catalog's own
    published ceiling); ``high`` must be past the limit so the first probe is
    free. Returns the bracket plus every attempt, so the conclusion is auditable.
    """
    if not str(model or "").strip():
        return {"ok": False, "error": "A model id is required."}
    if high <= low:
        return {"ok": False, "error": "The high bound must exceed the low bound."}

    ratio = _calibrate(model, timeout)
    attempts: list[dict[str, Any]] = []
    cursor_low, cursor_high = int(low), int(high)

    for _round in range(max_rounds):
        if cursor_high - cursor_low <= tolerance:
            break
        midpoint = (cursor_low + cursor_high) // 2
        units = max(1, int(round(midpoint / ratio)))
        result = _send(model, units, timeout)
        attempts.append({"probe_tokens": midpoint, "units": units, **result})
        if result.get("accepted") is None:
            return {"ok": False, "error": result.get("detail"), "attempts": attempts,
                    "tokens_per_unit": ratio}
        if result["accepted"]:
            cursor_low = midpoint
        else:
            cursor_high = midpoint

    return {
        "ok": True,
        "model": model,
        "tokens_per_unit": round(ratio, 4),
        "largest_accepted": cursor_low,
        "smallest_rejected": cursor_high,
        "attempts": attempts,
        "note": (
            "Largest accepted is the highest bracket known to fit; the true limit sits "
            "between it and smallest_rejected. Accepted probes were billed to the subscription."
        ),
    }
