# Evidence: what was measured, and how

This plugin's numbers are readings, not estimates. This file records the raw
runs so anyone can reproduce or challenge them.

Everything below was measured on **2026-10-14** against the live ChatGPT/Codex
OAuth backend (`https://chatgpt.com/backend-api/codex`) using the reasoning
"High"/"Pro" tier account's own sign-in.

## 1. What the account-scoped catalog advertises

`GET /models?client_version=99.0.0` with the account headers:

| slug | `context_window` | `max_context_window` | visibility |
|---|---|---|---|
| `gpt-6.1-sol` | **500,000** | 872,000 | list |
| `gpt-6-astra` | 272,000 | 872,000 | list |
| `gpt-6-sol` | 272,000 | 872,000 | list |
| `gpt-6-luna` | 272,000 | 872,000 | list |
| `gpt-reserve` | 272,000 | 872,000 | hide |
| `gpt-5.6-sol` | 272,000 | 872,000 | list |
| `gpt-5.6-terra` | 272,000 | 872,000 | list |
| `gpt-5.6-luna` | 272,000 | 872,000 | list |
| `gpt-5.5` | 272,000 | **272,000** | hide |
| `codex-auto-review` | 272,000 | 872,000 | hide |

The same request with `client_version=0.0.0` returns a frozen seven-model legacy
list, which is why the newest client version is the one Hermes asks as.

**`872,000` is OpenAI's own published ceiling**, not a Hermes limit. It is
`1,000,000 − 128,000`, i.e. a 1M window with the output reserve subtracted.

## 2. What Hermes resolves from that catalog

Measured with the credential attached, `provider="openai-codex"`:

| model id | resolved |
|---|---|
| `gpt-6.1-sol` | 500,000 |
| `gpt-6.1-sol-900k` | **500,000** ← the opt-in variant delivered nothing |
| `gpt-6-sol` | 272,000 |
| `gpt-6-sol-900k` | 872,000 |
| `gpt-6-astra-900k`, `gpt-6-luna-900k`, `gpt-5.6-*-900k` | 872,000 |

The `-900k` variant is supposed to lift the window to a live-verified 900,000.
It did not for `gpt-6.1-sol` because Hermes' bump fires only when the advertised
value is exactly the legacy `272,000`; OpenAI raised that model's advertisement
to 500,000, so the bump never ran. For the others the bump did run and was then
clamped by the catalog ceiling: `min(900,000, 872,000) = 872,000`.

## 3. What the backend actually accepts

Bisection over input size on `POST /responses`, with `max_output_tokens: 16` and
`reasoning.effort: "low"`. Padding is `"word " × N`, calibrated against a small
accepted request (**1 unit = 1.0036 tokens**).

| probe (approx. tokens) | reported `input_tokens` | verdict |
|---|---|---|
| 1,036,000 | 1,032,283 | **rejected** |
| 954,000 | 950,577 | **rejected** |
| 913,000 | 909,742 | accepted |
| 933,500 | 930,151 | **rejected** |
| 923,250 | 919,956 | accepted |

**Result: the largest accepted input is 919,956; the smallest rejection is
930,151.** The true ceiling sits between them, ≈922,000 — which is exactly
`1,050,000 − 128,000`, the model page's total context minus the output reserve.

### How a rejection looks

An oversized input is **not** an HTTP 400 on the streaming path. The backend
returns `200`, streams `response.created` and `response.in_progress`, and only
then:

```
event: error
data: {"type":"error","error":{"message":"Your input exceeds the context window
      of this model. Please adjust your input and try again.",
      "type":"invalid_request_error","param":"input",
      "code":"context_length_exceeded"}}

event: response.failed
```

That late failure is why an over-large window looks like a mid-turn crash rather
than a configuration error — and why `probe` reads the whole reply instead of the
status code.

## 4. The conclusion, and its limits

* **Full 1M of input is not available on this route.** The advertised ~1.05M is
  the *total* window, shared with a 128K output reserve. Nothing can make 1M of
  input fit.
* **900,000 is safe.** It sits ~20,000 below the smallest input the backend
  refused in testing, and ~26,000 below the largest it accepted.
* **918,000 is the aggressive setting.** It is still inside the accepted zone,
  but with a much thinner margin. `target_context_window` clamps to it.
* **Per-model caveat.** `gpt-6.1-sol` was bisected directly. For the other
  models the 900,000 default comes from Hermes core's own live-verified constant
  (`_CODEX_OAUTH_VERIFIED_ABOVE_ADVERTISED_*`), which independently records
  `gpt-6-astra` accepting 920,043 and refusing 1,000,043, and `gpt-6.1-sol`
  accepting 918,137. Re-measure any of them with `hermes codex-truly probe`.

## 5. Cost of measuring

A **rejected** probe never generates and is not billed. An **accepted** probe is
a real request for that many input tokens. Bisecting from a bracket therefore
costs roughly one accepted request at the final size.

The run above billed approximately **1.86M input tokens** of subscription quota
(5,018 + 909,742 + 919,956 for the accepted probes). That is the price of
knowing the number instead of guessing it — and a reason `probe` is not run
automatically.

## 6. Reproducing

```bash
hermes codex-truly status                 # catalog vs applied overrides
hermes codex-truly probe gpt-6.1-sol      # re-bisect the ceiling (spends quota)
hermes codex-truly probe gpt-6-sol --low 872000 --high 1000000
```

`probe` prints every attempt with its verdict, so the bracket is auditable
rather than a single asserted number.
