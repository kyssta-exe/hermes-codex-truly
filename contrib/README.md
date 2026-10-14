# Catalog submission

`catalog-entry.yaml` in this directory is the ready-to-submit
[Hermes plugin catalog](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md)
entry for this plugin.

The catalog is the only discovery system for out-of-tree Hermes plugins, and
presence in it **is** the trust signal — entries are added only by a
human-reviewed PR to `NousResearch/hermes-agent`, pinned to an exact 40-character
commit SHA (see the admission rules in that repo's `plugin-catalog/README.md`).

## Submitting (or re-pinning) it

1. Take the commit SHA of the release to list:

   ```bash
   git -C . rev-parse HEAD            # or the release tag's commit
   ```

2. Put that SHA in `catalog-entry.yaml` under `sha:`, and make sure `version:`
   matches `plugin.yaml` at that commit.

3. Fork `NousResearch/hermes-agent`, copy the file to
   `plugin-catalog/codex-truly.yaml`, and open a PR.

A **SHA bump is a new PR** — the diff `old SHA → new SHA` gets reviewed like any
other change, so re-pinning means submitting the updated file again.

Admission rule 3 forbids a self-updater in the listed build: the exact pin *is*
the trust model, and the plugin deliberately ships no "check for updates" code.
Updates reach users through a SHA-bump PR plus `hermes plugins update codex-truly`.

## Local checks before submitting

Run Hermes' own admission checks — the same ones the catalog's CI runs:

```bash
hermes plugins validate .
hermes plugins doctor   .
```

The `security scan`, `declared tools`, `no core override` and `capability probe`
checks are the ones that matter here. The plugin declares its sign-in use so the
reviewer sees it:

```yaml
requires_auth:
  - openai-codex
```
