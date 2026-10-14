# Changelog

Notable changes to Codex Truly. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [1.0.0] — 2026-10-14

First release.

### Added

- `hermes codex-truly status` — per-model comparison of the catalog's advertised
  window, its published ceiling, the override this plugin applied, and the
  largest input the live backend was measured to accept.
- `hermes codex-truly apply` — writes scoped
  `model_overrides.<provider>.<model_id>.context_window` entries so the Codex
  `-900k` variants resolve to a live-verified window instead of the catalog
  clamp. `--dry-run`, `--target` and `--base-slugs` variants included.
- `hermes codex-truly revert` — removes exactly the entries the plugin wrote,
  with `--all` to purge every managed id.
- `hermes codex-truly probe [model]` — bisects the live API for the real input
  ceiling and prints every attempt with its verdict.
- `hermes codex-truly image "<prompt>"` — generates through the Codex
  subscription via core's `openai-codex` image provider (gpt-image-2, no API key).
- `/codex-truly` slash command and a `codex_truly` agent tool with the same
  actions, so the agent can inspect and fix its own context window.
- `apply` selects the Codex-backed image provider when `image_gen.provider` is
  unset; an explicit existing choice is never overwritten.
- Bundled `codex-truly` skill documenting the numbers, the failure mode and the
  pitfalls.

### Security

- The plugin never reads or holds a credential. All Codex requests go through
  Hermes's `credentialed_provider_request`, which attaches the profile's sign-in
  and enforces an origin allowlist; `plugin.yaml` declares
  `requires_auth: [openai-codex]`.
- Writes go through Hermes's atomic config writers, and never touch a core file.

### Notes

- Measured against the live backend on this date: `gpt-6.1-sol` accepts 919,956
  input tokens and refuses 930,151, while its catalog entry advertises 500,000
  and caps at 872,000. Full reasoning and raw runs: [`docs/evidence.md`](docs/evidence.md).
