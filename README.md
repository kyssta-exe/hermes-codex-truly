# Codex Truly

**Give your ChatGPT/Codex subscription the context window you actually pay for — and generate
images through it, with no OpenAI API key.**

[![CI](https://github.com/kyssta-exe/hermes-codex-truly/actions/workflows/ci.yml/badge.svg)](https://github.com/kyssta-exe/hermes-codex-truly/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Hermes](https://img.shields.io/badge/hermes-%E2%89%A50.21.5-black)](https://hermes-agent.nousresearch.com)

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin.

---

## The problem

Hermes talks to your ChatGPT/Codex subscription over OAuth. It then believes the numbers that API
publishes **about itself** — and those numbers are conservative. Measured against the live backend
on 2026-10-14:

| model | catalog advertises | catalog caps at | **backend accepts** | you were getting |
|---|---|---|---|---|
| `gpt-6.1-sol` | 500,000 | 872,000 | **919,956** | 500,000 |
| `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna` | 272,000 | 872,000 | ~920,000 | 872,000 ¹ |
| `gpt-5.6-sol`, `-terra`, `-luna` | 272,000 | 872,000 | ~920,000 | 872,000 ¹ |
| `gpt-5.5` | 272,000 | 272,000 | 272,000 | 272,000 (not widen-able) |

¹ only via the `-900k` opt-in variant, and only up to the catalog ceiling.

Two separate defects were hiding here:

1. **`872,000` is OpenAI's published ceiling, not a number Hermes can beat by asking nicely** — it
   is `1,000,000 − 128,000`, a 1M window minus the output reserve. The backend demonstrably
   accepts ~48K more than it advertises.
2. **The `-900k` variant had silently stopped working for `gpt-6.1-sol`.** Hermes' bump from the
   advertised window to its live-verified 900,000 fires only when the advertised value is exactly
   the legacy `272,000`. OpenAI raised that model's advertisement to `500,000`, so the bump never
   ran and the variant delivered nothing — leaving the flagship model on the *lowest* window of
   the set.

Raw runs, every probe and both failures: [`docs/evidence.md`](docs/evidence.md).

---

## What this plugin does

Reads the real numbers off the wire, then writes **one scoped config entry per model** — step 2 of
Hermes' documented context-resolution chain, which runs before the catalog probe that does the
clamping. No core file is patched; nothing it writes is more than a normal user setting.

| Feature | What you get |
|---|---|
| **`hermes codex-truly status`** | Per model: what the catalog advertises, what it caps at, what override is applied, and the largest input the live backend was measured to accept. |
| **`hermes codex-truly apply`** | Writes the `model_overrides` entries that grant the widened window. `--dry-run`, `--target N`, `--base-slugs`. |
| **`hermes codex-truly revert`** | Removes exactly the entries it wrote, restoring your config. `--all` purges every managed id. |
| **`hermes codex-truly probe [model]`** | Bisects the live API for the true input ceiling and prints every attempt with its verdict — your own measurement, not our table. |
| **`hermes codex-truly image "<prompt>"`** | Generates an image through the Codex subscription (gpt-image-2) — no API key, no per-image credits. |
| **`/codex-truly`** | The status one-liner in any session (`/codex-truly full` for the table). |
| **`codex_truly` tool** | The agent can inspect and fix its own context window: `status`, `apply`, `revert`, `probe`, `image`. |
| **`codex-truly` skill** | Bundled guidance so a Hermes agent knows when and how to use the above. |

---

## Reality check: there is no full 1M of input

OpenAI advertises ~1.05M **total** context for these models. That window is shared with the
response, so with a 128K output reserve roughly **920K of input** fits — and the backend refuses
anything beyond it:

```
919,956 input tokens → accepted
930,151 input tokens → rejected  (context_length_exceeded)
```

That refusal is **late**: the request returns `200`, streams `response.created` and
`response.in_progress`, and only then emits an `error` event and `response.failed`. An over-large
window therefore looks like a mid-turn crash, not a configuration error.

So this plugin defaults to **900,000** — about 20K below the smallest input the backend refused in
testing — and clamps the configurable ceiling to **918,000**. Any tool promising 1M of *input* on
this route is guessing.

---

## Install

```bash
hermes plugins install kyssta-exe/hermes-codex-truly
hermes plugins enable codex-truly
```

Or drop the directory into `$HERMES_HOME/plugins/codex-truly/` and add `codex-truly` to
`plugins.enabled` in `config.yaml`.

Requires Hermes ≥ 0.21.5 and a signed-in Codex/ChatGPT profile:

```bash
hermes auth add openai-codex      # or sign in to Codex under Providers
```

## Quick start

```bash
hermes codex-truly status                 # see the gap on your own account
hermes codex-truly apply --dry-run        # preview the change
hermes codex-truly apply                  # write it
hermes gateway restart                    # the window resolves per session

hermes codex-truly image "a red apple on a white table" --aspect square
```

Then pick a widened model in-session with the `-900k` suffix:

```
/model gpt-6.1-sol-900k
```

**Undo at any time:**

```bash
hermes codex-truly revert
```

---

## How the fix works

Hermes resolves a model's context length through a fixed chain. The first three layers are
config-only and run **before every cache and live probe**, which is what makes an override the
supported lever rather than a hack:

1. `model.context_length` — global, every model.
2. **`model_overrides.<provider>.<model_id>.context_window`** ← what this plugin writes.
3. `custom_providers[].models.<model_id>.context_length`.

```yaml
model_overrides:
  openai-codex:
    gpt-6.1-sol-900k:
      context_window: 900000
```

Notes that matter:

- The key must match the model id **as resolved**, so the `-900k` variant is what gets keyed — a
  base-slug key does not match the variant.
- The value is a **mapping** (`{context_window: N}`), not a scalar. A bare integer is accepted
  silently and then ignored, and the catalog clamp wins again.
- Editing `context_length_cache.yaml` is not a lever: Codex-route resolution bypasses the
  persistent cache entirely.
- A live session keeps the window it resolved at startup — hence the gateway restart.

By default the plugin widens the **`-900k` variants only**, preserving Hermes' own opt-in
semantics: the plain ids stay on the cheap advertised window because a large window burns
subscription quota faster. `--base-slugs` widens the plain ids too.

---

## Image generation

Hermes core already ships the backend for this: `plugins/image_gen/openai-codex` posts to the
Codex backend's native `images/generations` and `images/edits` endpoints on the same OAuth token,
using gpt-image-2. No `OPENAI_API_KEY`, no per-image API credits — it comes out of your
subscription.

What a Codex subscriber usually lacks is the *selection*: `image_gen.provider` has to name that
plugin. So this plugin **drives and verifies** the existing path instead of reimplementing it:

- `apply` sets `image_gen.provider: openai-codex` **when nothing is configured yet** — an explicit
  choice (you may deliberately be on a paid image API) is reported, never overwritten.
- `status` reports which provider is live and whether it is Codex-backed.
- `image` runs a real generation, so the route is proven rather than asserted.

---

## Configuration

All settings live under `plugins.entries.codex-truly.settings` and are editable in the desktop
app's Settings → Plugins form.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Let the plugin manage the window and the Codex image provider. Turning it off leaves existing overrides in place. |
| `target_context_window` | `900000` | Window to grant each managed model. Values outside 272000–918000 are clamped, never rejected. |
| `apply_to_base_slugs` | `false` | Also widen the plain ids (`gpt-6.1-sol`), not just the `-900k` variants. |
| `provider_keys` | `openai-codex` | Comma-separated provider ids to write overrides for. Add e.g. `custom:my-codex-proxy` when routing Codex through a custom provider with `api_mode: codex_responses`. |
| `ensure_image_provider` | `true` | Select the Codex image provider when none is configured. |
| `models` | `""` | Explicit model list. Empty manages every widen-able model in the live catalog. |

```bash
hermes config set plugins.entries.codex-truly.settings.target_context_window 918000
hermes codex-truly apply      # re-apply after changing the target
```

---

## Credentials, privacy and side effects

Full disclosure, because you should know what you are installing:

- **Your Codex sign-in is used, and the plugin never holds it.** Every request goes through
  Hermes's `credentialed_provider_request`, which resolves the profile's credential, attaches it,
  and enforces an origin allowlist. `plugin.yaml` declares `requires_auth: [openai-codex]` so
  Hermes knows the sign-in is in use. The plugin does not read `auth.json`, `.env`, a token file or
  another tool's login, and never refreshes or writes a token.
- **Network calls go only to `chatgpt.com`** — the Codex backend core itself calls: `GET /models`
  for the catalog, `POST /responses` for `probe`. A profile whose Codex credential routes to a
  custom endpoint is refused rather than guessed at.
- **No telemetry.** Nothing is reported anywhere.
- **Config writes are the only side effect.** `apply` writes scoped `model_overrides` entries (and
  `image_gen.provider` if unset) through Hermes's atomic config writers; `revert` removes exactly
  what it wrote. No core file is modified, and no other profile is touched.
- **`probe` spends subscription quota.** Refused requests are free; each *accepted* one bills its
  input. Bisection costs roughly one accepted request at the final size (the recorded run cost
  ~1.86M input tokens). It is never run automatically.
- **No background processes**, no shell commands, no self-updater.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| A turn dies with `context_length_exceeded` mid-stream | The window is set above ~925K. Lower it and re-apply: `hermes codex-truly apply --target 880000`. |
| `status` says the catalog returned no models | Not signed in for this profile, or the credential routes to a custom endpoint. Re-run `hermes auth add openai-codex`. |
| The window did not change | Overrides apply at session start — `hermes gateway restart`, then check `/status`. |
| A `-900k` model still shows the old window | Re-run `hermes codex-truly status` and `apply`; OpenAI moves these numbers and the plugin re-reads them live. |
| `image` says no provider is active | Run `hermes codex-truly apply`, or choose one in `hermes tools`. |

---

## Development

```bash
pip install ruff pytest pyyaml
ruff check .
python -m pytest tests/
```

The suite has three layers:

- **Unit** — config clamping, model selection, catalog parsing, report formatting.
- **Contracts** — the manifest declares the sign-in it uses; the catalog path goes through
  `credentialed_provider_request`; declared tools match `register()`.
- **End to end** — drives Hermes' *real* context-resolution chain against a throwaway
  `HERMES_HOME`, asserting the catalog-clamped window changes after `apply` and is restored by
  `revert`. It skips cleanly when Hermes or a Codex credential is absent, and it refuses to pass
  off the credentialless fallback path (which would prove nothing).

Point the end-to-end tests at another install with `HERMES_SRC=/path/to/hermes-agent`, and at
another credential with `CODEX_TRULY_TEST_AUTH=/path/to/auth.json`.

To run Hermes' own admission checks locally:

```bash
hermes plugins validate /path/to/this/repo
hermes plugins doctor   /path/to/this/repo
```

---

## Compatibility

| | |
|---|---|
| Hermes | `>= 0.21.5` |
| Python | 3.11+ |
| Platforms | Linux, macOS, Windows (pure Python, no dependencies) |
| Provider | `openai-codex` (ChatGPT/Codex subscription); custom Codex-routed providers via `provider_keys` |

No Python dependencies: the plugin uses Hermes' own HTTP route and the standard library.

---

## License

MIT — see [LICENSE](LICENSE).
