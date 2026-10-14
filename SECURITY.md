# Security

## Reporting a vulnerability

Open a private [security advisory](https://github.com/kyssta-exe/hermes-codex-truly/security/advisories/new)
rather than a public issue, or email the maintainer listed in `plugin.yaml`.

Please include the Hermes version (`hermes --version`), the plugin version
(`plugin.yaml`, or `hermes plugins list`), and a reproduction.

## What this plugin can and cannot do

State the threat model honestly, so a finding can be scoped:

**It can**

- Read the profile's `config.yaml` and write **`model_overrides`** entries and
  `image_gen.provider` (only when that key is unset) through Hermes's atomic
  config writers.
- Make HTTP requests to **`chatgpt.com`** only — `GET /backend-api/codex/models`
  and `POST /backend-api/codex/responses` — carrying the profile's Codex sign-in.
- Spend subscription quota when `probe` is run (`apply`, `status` and `image`
  spend nothing beyond a normal catalog read and one image generation).

**It cannot**

- Read or write any credential store. Every request goes through
  `hermes_cli.plugin_provider_requests.credentialed_provider_request`, which
  resolves the credential itself, attaches it, and enforces the origin
  allowlist. The plugin never sees the token.
- Reach any host other than `chatgpt.com`: the allowlist above refuses anything
  else, including a custom endpoint the profile's credential happens to route to.
- Run shell commands, spawn processes, register background work, or update
  itself. There is no self-updater by design — the plugin catalog's SHA pin is
  the trust model, and a listed build must not move on its own.
- Touch Hermes core. The plugin registers a CLI command, a slash command, a tool
  and a bundled skill; it rebinds nothing (`hermes plugins validate` enforces
  the `no core override` check).
- Touch another Hermes profile. Paths resolve through the active
  `HERMES_HOME`/profile.

## Scope notes for reviewers

- `requires_auth: [openai-codex]` in `plugin.yaml` is load-bearing. Without it
  Hermes refuses every credentialed request; with it, a reviewer can see exactly
  which sign-in the plugin uses.
- `probe` is the only path that sends a large payload. Oversized requests are
  refused by the backend before generation and are not billed; accepted probes
  bill their input, which is why the command warns before running and is never
  triggered automatically.
- The plugin declares no Python dependencies, so there is no supply-chain surface
  beyond Hermes itself and the standard library.
