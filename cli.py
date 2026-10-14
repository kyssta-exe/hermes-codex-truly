"""``hermes codex-truly <subcommand>``.

  status  — advertised vs Hermes vs measured, per Codex model
  models  — the same table as JSON
  apply   — write the scoped model_overrides (--dry-run to preview)
  revert  — remove the entries this plugin wrote (--all to purge every managed id)
  probe   — measure the real input ceiling live (spends subscription quota)
  image   — generate an image through the Codex subscription
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from . import images, overrides, probe, report
from .config import VALID_ASPECT_RATIOS, CodexTrulyConfig

_PROBE_DEFAULT_MODEL = "gpt-6.1-sol"


def _config(args: Any) -> CodexTrulyConfig:
    """Config for a CLI run, with the settings that the CLI can override applied."""
    from . import runtime

    ctx = runtime.get_ctx()
    config = CodexTrulyConfig.from_ctx(ctx) if ctx is not None else CodexTrulyConfig()
    if getattr(args, "target", None):
        config.target_context_window = int(args.target)
        config.__post_init__()
    if getattr(args, "base_slugs", False):
        config.apply_to_base_slugs = True
    return config


def _emit(payload: dict[str, Any], as_json: bool, renderer=None) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    elif renderer is not None:
        print(renderer(payload))
    else:
        print(json.dumps(payload, indent=2, default=str))
    return 0 if payload.get("ok", True) else 1


# -- status ---------------------------------------------------------------

def _cmd_status(args: Any) -> int:
    try:
        status = report.collect(_config(args))
    except Exception as exc:
        print(f"codex-truly: status failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2, default=str))
        return 0
    print(report.text_report(status))
    return 0


def _cmd_models(args: Any) -> int:
    try:
        status = report.collect(_config(args))
    except Exception as exc:
        print(f"codex-truly: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(status.get("models", []), indent=2, default=str))
    return 0


# -- apply / revert -------------------------------------------------------

def _cmd_apply(args: Any) -> int:
    payload = overrides.apply_overrides(
        _config(args), dry_run=bool(getattr(args, "dry_run", False)))
    if not payload.get("ok"):
        print(f"codex-truly: {payload.get('error')}", file=sys.stderr)
        return 1
    if payload.get("dry_run"):
        print("DRY RUN — nothing written.\n")
    for row in payload.get("changed", []):
        print(f"  {row['model']:<22} {row.get('current') or '?':>10} -> {row['target']:,}")
    for row in payload.get("unchanged", []):
        print(f"  {row['model']:<22} {'':>10}    (skipped: {row.get('reason')})")
    if payload.get("note"):
        print(payload["note"])
    if payload.get("image_provider"):
        print(f"  image_gen provider : {payload['image_provider']}")
    if payload.get("catalog_error"):
        print(f"  note: live catalog unavailable ({payload['catalog_error']});"
              " used Hermes' curated model list.", file=sys.stderr)
    if not payload.get("dry_run"):
        print("\nRestart the gateway (or start a new session) for the new window to take effect:")
        print("  hermes gateway restart")
        print("A 900K window also means a single turn can now submit far more context — worth it")
        print("for large-repo or long-document work, wasteful for short chats. `revert` undoes it.")
    return 0


def _cmd_revert(args: Any) -> int:
    payload = overrides.revert_overrides(_config(args), purge_all=bool(getattr(args, "all", False)))
    if not payload.get("ok"):
        print(f"codex-truly: {payload.get('error')}", file=sys.stderr)
        return 1
    removed = payload.get("removed") or {}
    if not removed:
        print("Nothing to remove.")
        return 0
    for provider, models in removed.items():
        print(f"  {provider}: removed {', '.join(models)}")
    print(f"\nRemoved the {payload.get('scope')}. Restart the gateway to apply.")
    return 0


# -- probe ----------------------------------------------------------------

def _cmd_probe(args: Any) -> int:
    model = str(getattr(args, "model", None) or _PROBE_DEFAULT_MODEL).strip()
    low = int(getattr(args, "low", None) or 872_000)
    high = int(getattr(args, "high", None) or 1_200_000)
    tolerance = int(getattr(args, "tolerance", None) or 20_000)
    if high <= low:
        print("codex-truly: --high must be greater than --low", file=sys.stderr)
        return 1
    print(f"Probing {model} between {low:,} and {high:,} tokens.")
    print("Rejected probes cost nothing; each accepted probe bills its input to your subscription.")
    print("")
    payload = probe.probe_model(model, low=low, high=high, tolerance=tolerance)
    if not payload.get("ok"):
        print(f"codex-truly: {payload.get('error')}", file=sys.stderr)
        return 1
    for attempt in payload.get("attempts", []):
        verdict = "accepted" if attempt.get("accepted") else "REJECTED"
        print(f"  ~{attempt['probe_tokens']:>10,} tokens -> {verdict}"
              f"  (reported input {attempt.get('input_tokens') or attempt['units']:,})")
    print("")
    print(f"  largest accepted : {payload['largest_accepted']:,}")
    print(f"  smallest rejected: {payload['smallest_rejected']:,}")
    print(f"  {payload['note']}")
    print("")
    print("Set the value with: hermes codex-truly apply --target <n>")
    return 0


# -- image ----------------------------------------------------------------

def _cmd_image(args: Any) -> int:
    prompt = " ".join(getattr(args, "prompt", []) or []).strip()
    if not prompt:
        print("codex-truly: give a prompt, e.g. hermes codex-truly image \"a red apple\"",
              file=sys.stderr)
        return 1
    aspect = str(getattr(args, "aspect", "landscape") or "landscape").lower()
    if aspect not in VALID_ASPECT_RATIOS:
        aspect = "landscape"
    state = images.provider_state()
    if not state["codex_backed"]:
        print(f"  note: image_gen.provider is {state['provider']}, not the Codex route.")
        print("  Run `hermes codex-truly apply` to select it.\n")
    payload = images.generate(prompt, aspect)
    if not payload.get("ok"):
        print(f"codex-truly: {payload.get('error')}", file=sys.stderr)
        return 1
    print(f"  provider : {payload.get('provider')}")
    print(f"  model    : {payload.get('model')}  ({payload.get('reported_size')})")
    print(f"  image    : {payload.get('image')}")
    return 0


# -- argparse -------------------------------------------------------------

def build_parser(parser: Any) -> None:
    """Add codex-truly's subcommands to the parser the host provides.

    ``ctx.register_cli_command`` handlers receive the ALREADY-created top-level
    parser for ``hermes <name>``; adding a second parser here would nest the
    command as ``hermes codex-truly codex-truly <sub>``.
    """
    sub = parser.add_subparsers(dest="codex_truly_command")

    status = sub.add_parser("status", help="Advertised vs Hermes vs measured context, per model")
    status.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    status.add_argument("--target", type=int, help="Preview against a different target window")
    status.set_defaults(func=_cmd_status)

    models = sub.add_parser("models", help="Managed Codex models and their windows as JSON")
    models.add_argument("--target", type=int, help="Preview against a different target window")
    models.set_defaults(func=_cmd_models)

    apply_cmd = sub.add_parser("apply", help="Write the model_overrides that widen the window")
    apply_cmd.add_argument("--dry-run", action="store_true", help="Show what would change")
    apply_cmd.add_argument("--target", type=int, help="Window to grant (272000-918000)")
    apply_cmd.add_argument("--base-slugs", action="store_true",
                           help="Also widen the plain model ids, not just the -900k variants")
    apply_cmd.set_defaults(func=_cmd_apply)

    revert = sub.add_parser("revert", help="Remove the model_overrides this plugin wrote")
    revert.add_argument("--all", action="store_true",
                        help="Purge every managed id, including entries edited by hand")
    revert.set_defaults(func=_cmd_revert)

    prob = sub.add_parser("probe", help="Measure the real input ceiling against the live API")
    prob.add_argument("model", nargs="?", default=_PROBE_DEFAULT_MODEL,
                      help=f"Codex model id (default {_PROBE_DEFAULT_MODEL})")
    prob.add_argument("--low", type=int, default=872_000,
                      help="Lower bound in tokens, assumed accepted (default 872000)")
    prob.add_argument("--high", type=int, default=1_200_000,
                      help="Upper bound in tokens, assumed rejected (default 1200000)")
    prob.add_argument("--tolerance", type=int, default=20_000,
                      help="Stop when the bracket is this narrow (default 20000)")
    prob.set_defaults(func=_cmd_probe)

    image = sub.add_parser("image", help="Generate an image through the Codex subscription")
    image.add_argument("prompt", nargs=argparse.REMAINDER, help="What to draw")
    image.add_argument("--aspect", choices=list(VALID_ASPECT_RATIOS), default="landscape",
                       help="landscape, square or portrait")
    image.set_defaults(func=_cmd_image)


def run(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point (tests + `python -m codex-truly`); the host calls the handler."""
    parser = argparse.ArgumentParser(prog="hermes codex-truly", description=__doc__)
    build_parser(parser)
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


def command(args: Any) -> int:
    """``handler_fn`` the plugin host wires via ``set_defaults(func=...)``."""
    handler = getattr(args, "func", None)
    if handler is None:
        print("codex-truly: pick a subcommand (status, apply, revert, probe, image, models)",
              file=sys.stderr)
        return 1
    return handler(args)
