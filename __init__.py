"""codex-truly — the real context window and image generation for Codex subscriptions.

Hermes already speaks the ChatGPT/Codex OAuth API, but it believes the numbers
that API publishes about itself: 272K or 500K advertised per model, an 872K
``max_context_window`` ceiling. Measured against the live backend on 2026-10-14,
``gpt-6.1-sol`` accepted 919,956 input tokens and refused 930,151 — so a Codex
subscriber was being handed between 30% and 80% of the window they pay for.

This plugin closes that gap without patching core. Hermes resolves a model's
context length through a documented chain, and
``model_overrides.<provider>.<model_id>.context_window`` is consulted before the
Codex catalog probe, so writing one scoped entry is enough. The plugin also ships
the probe that produced the number, so the claim stays checkable after OpenAI
changes something.

Surfaces added:

* ``hermes codex-truly`` — status / models / apply / revert / probe / image
* ``/codex-truly`` — the status one-liner in any session
* ``codex_truly`` tool — the agent can inspect and fix its own window
* ``codex-truly`` skill — when and how to use either

Image generation is *driven*, not reimplemented: core's ``image_gen/openai-codex``
backend already posts to the Codex ``images/generations`` endpoint on the same
OAuth token, so the plugin selects it, reports it, and runs a real generation to
prove the route works.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import cli, images, report, runtime, tool
from .config import CONFIG_SCHEMA, CodexTrulyConfig

logger = logging.getLogger(__name__)

PLUGIN_ID = "codex-truly"


def _handle_status_command(raw_args: str = "", **kwargs: Any) -> str:
    """``/codex-truly`` — the status one-liner, plus the gap table when asked."""
    try:
        ctx = runtime.get_ctx()
        config = CodexTrulyConfig.from_ctx(ctx) if ctx else CodexTrulyConfig()
        status = report.collect(config)
    except Exception as exc:
        return f"codex-truly: {type(exc).__name__}: {exc}"
    if "full" in (raw_args or "").lower():
        return report.text_report(status)
    lines = [report.summary(status)]
    rows = [r for r in status.get("models", []) if r.get("widenable")]
    for row in rows[:6]:
        lines.append(
            f"  {row['model']}: {row['hermes_now'] or '?'} -> {row['target']:,}")
    if rows:
        lines.append("  Apply with: hermes codex-truly apply")
    image = status.get("image_gen", {})
    lines.append(
        f"  image_gen: {image.get('provider')} (Codex-backed: {image.get('codex_backed')})")
    return "\n".join(lines)


def register(ctx) -> None:
    """Register the CLI subcommand, slash command, tool and bundled skill."""
    runtime.set_ctx(ctx)

    ctx.register_cli_command(
        name=PLUGIN_ID,
        help="Real context windows and image generation for ChatGPT/Codex subscriptions",
        setup_fn=cli.build_parser,
        handler_fn=cli.command,
        description=(
            "Grant Codex/ChatGPT-subscription models the context window the backend actually "
            "accepts (~900K) instead of the 272K/500K it advertises, and route image generation "
            "through the same subscription. See: hermes codex-truly status"),
    )
    ctx.register_command(
        "codex-truly",
        _handle_status_command,
        description="Codex context window and image-generation status",
        args_hint="[full]",
    )
    ctx.register_tool(
        name="codex_truly",
        toolset="codex_truly",
        schema=tool.SCHEMA,
        handler=tool.codex_truly,
        emoji="📐",
        description="Inspect and widen the Codex subscription context window; generate images "
                    "through the subscription.",
    )

    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()) if skills_dir.is_dir() else []:
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file():
            ctx.register_skill(child.name, skill_md)

    logger.debug("codex-truly registered (schema keys: %s)", ", ".join(sorted(CONFIG_SCHEMA)))


__all__ = ["register", "CONFIG_SCHEMA", "CodexTrulyConfig", "cli", "images", "report", "tool"]
