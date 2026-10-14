---
name: codex-truly
description: "Give ChatGPT/Codex-subscription models in Hermes the context window the backend really accepts (~900K, not the 272K/500K it advertises) and generate images through the same subscription with no OpenAI API key."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [codex, openai, oauth, context-window, image-generation, subscription]
    homepage: https://github.com/kyssta-exe/hermes-codex-truly
---

# Codex Truly — the window you actually pay for

Hermes talks to ChatGPT/Codex subscriptions over OAuth, but it believes the
numbers that API publishes about itself. Those numbers are conservative:

| | Advertised `context_window` | Published `max_context_window` | **Measured accepted** |
|---|---|---|---|
| gpt-6.1-sol | 500,000 | 872,000 | **919,956** (refused 930,151) |
| gpt-6-astra / gpt-6-sol / gpt-6-luna | 272,000 | 872,000 | ~920,000 (core-verified 900,000) |
| gpt-5.6-sol / -terra / -luna | 272,000 | 872,000 | core-verified 900,000 |

Measured 2026-10-14 against `POST https://chatgpt.com/backend-api/codex/responses`
by bisection — see `hermes codex-truly probe`.

## There is no full 1M of input. Say so.

OpenAI's model page advertises ~1.05M **total** context. That window is shared
with the response: with a 128K output reserve, roughly **920K of input** fits,
and the backend refuses anything past it with
`context_length_exceeded` — a *late* failure that arrives after
`response.created` has already streamed, so it looks like a mid-turn crash
rather than a request error. **900K is the safe ceiling**; this plugin clamps to
918,000 and defaults to 900,000. Never promise a user 1M of input.

## Use it

```bash
hermes codex-truly status          # catalog vs applied override vs measured
hermes codex-truly apply           # write the scoped model_overrides
hermes codex-truly apply --dry-run # preview
hermes codex-truly revert          # undo (--all purges every managed id)
hermes codex-truly probe gpt-6-sol # re-measure live (spends subscription quota)
hermes codex-truly image "a red apple on a white table" --aspect square
```

In a session: `/codex-truly` (add `full` for the table). The agent can call the
`codex_truly` tool with `status` / `apply` / `revert` / `probe` / `image`.

In the `status` table, `advertised` and `catalog max` come from the live catalog,
`override` is what this plugin wrote (an override wins, so that is what a new
session gets), and `measured ok` is the largest input a probe was accepted at.
There is deliberately no "current window" column: Hermes resolves that per
session, and predicting it here would be a guess.

After `apply`, restart the gateway (`hermes gateway restart`) — the window is
resolved per session.

## How the fix works (and why it is safe)

Hermes resolves a model's context length in a fixed chain: step 1 is
`model.context_length`, step 2 is
`model_overrides.<provider>.<model_id>.context_window`, step 3 is
`custom_providers`. All three are config-only and run **before** the Codex catalog
probe that clamps everything to `max_context_window`. So one scoped config entry
is the supported lever — this plugin never patches core, and nothing it writes is
more than a normal user setting.

The entry is a **mapping**, not a scalar: `{context_window: N}`. A bare integer
is accepted silently and then ignored, and the clamp wins again.

`apply` writes the `-900k` variants by default, preserving Hermes' own opt-in
semantics: the plain ids stay on the cheap advertised window because a large
window burns subscription quota faster. `--base-slugs` widens the plain ids too.

`apply` also selects core's `openai-codex` image provider when `image_gen.provider`
is unset. It never overwrites an explicit choice — the user may deliberately be
on a paid image API.

## Credentials

The plugin never holds the sign-in. Every Codex request goes through
`hermes_cli.plugin_provider_requests.credentialed_provider_request`, which
resolves the profile's credential, attaches it and enforces an origin allowlist
(`chatgpt.com` only). `plugin.yaml` declares `requires_auth: [openai-codex]`;
without that declaration Hermes refuses the request.

Never "fix" this by reading `auth.json` or resolving the token directly — a
profile that routes Codex through a custom endpoint must be refused by the
allowlist, not served the wrong host's credential.

## Image generation

Core plugin `image_gen/openai-codex` already posts to the Codex backend's native
`images/generations` / `images/edits` on the same OAuth token (gpt-image-2, no
API key). This plugin *drives and verifies* that path instead of reimplementing
it: `provider_state()` reports what is wired up, and `hermes codex-truly image`
runs a real generation so the route is proven, not asserted.

Do not add an `image_generate` hook or a second image backend — a duplicate
provider would fight core's own for `image_gen.provider`.

## Pitfalls

- **Rejected probes are free, accepted probes bill.** `probe` bisects, so it
  costs roughly one accepted request at the final size. Ask before running it on
  a metered subscription.
- **A too-large window fails late**, not with HTTP 400. If a turn dies with
  `context_length_exceeded` mid-stream, the window is set above ~925K — lower
  `target_context_window` and re-apply.
- **`gpt-5.5` cannot be widened** (catalog `max_context_window` = 272,000). Neither
  can models Hermes does not list as `-900k`-eligible.
- **Overrides are per provider key.** Routing Codex through a custom provider
  with `api_mode: codex_responses` needs that provider id in `provider_keys`.
- **Re-verify before trusting numbers.** OpenAI moved `gpt-6.1-sol`'s advertised
  window from 272K to 500K between September and October 2026, which silently
  disabled Hermes' own 272K→900K bump. `probe` is the answer, not the table.
