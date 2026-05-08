# Troubleshooting

Common errors when driving the API, and what they actually mean.

## HTTP 401 — Unauthorized

- The API key is missing, malformed, expired, or revoked.
- **Do not retry.** Tell the user to check `RAVN_API_KEY` is set and not stale.
- Verify with `python3 scripts/raven_cli.py whoami` — that's the cheapest probe.

## HTTP 403 — Forbidden

- The user's tier does not include the feature.
- Examples: Monte Carlo backtest with `--trials > 1` requires `FEATURE_MONTE_CARLO`. The in-product strategy assistant requires `FEATURE_STRATEGY_ASSISTANT` (you don't need this — your AI does the drafting).
- **Do not retry.** Tell the user which feature they need.

### Service-account-specific 403s

- **"Service accounts are not enabled for your current plan"** — owner's tier needs `FEATURE_SERVICE_ACCOUNTS`. Hit on `--as-service-account` login, enrollment-token creation, enroll, approve-live, rotate, deactivate.
- **"API keys are not enabled for your current plan"** — owner's tier needs `FEATURE_API_KEYS`. Hit on personal-key login (`login` without `--as-service-account`) and `/api/keys`. Note: SA mode does *not* require this flag.
- **"Service accounts cannot access this endpoint"** — the agent is running as a service account and tried to hit a human-only route (`/api/keys`, settings, leaderboards, Discord linking, wallet export, owner-side `/api/service-accounts/*` management). Tell the user to perform the action in the web app under their owner identity.
- **"Service account live trading has not been approved"** — the SA's `can_trade_live` is still false. Owner must open Settings → Service Accounts → Approve live for this child. The agent cannot self-approve.
- **"Your account is not eligible for live trading"** (on `approve-live` for a child) — the *owner* doesn't have live trading themselves, so they can't grant it to a child. Owner needs `live_trading` on their tier or a per-user `can_trade_live=true` admin override.

## HTTP 404 — Not found

- Wrong id, or the resource belongs to another user.
- Try the public_id and the numeric id — both are accepted by `bots`/`strategies`/`sessions` get/update/delete.

## HTTP 409 — Conflict

- Common cases: deleting a strategy that still has bots/sessions referencing it, deleting a session whose bot is still running.
- The error message names the conflict; relay it.

## HTTP 422 — Unprocessable Entity

- Pydantic validation failed at the request layer (wrong shape, wrong types, missing required fields).
- For strategy bodies: run `strategies validate --json <file>` first; that catches everything `create`/`update` would catch, plus more.

## HTTP 429 — Too Many Requests

- You hit a rate limit (deploy, start/stop, backtest are all expensive-mutation rate-limited).
- Back off. Don't retry tight. Tell the user you're rate-limited and propose waiting a minute.

## "Strategy validation failed"

- The schema is enforced both at validate-time and at create/update-time.
- Re-fetch `docs prompt` and `docs signals` — the server may have introduced new signal types since you last loaded the schema.

## Bot stuck in `STARTING`

- Wait up to 60 seconds. Bots launch async via background tasks; they go `STOPPED` → `STARTING` → `RUNNING` (or `ERROR`).
- After 60s with no movement, check `bots logs <id> --tail 200` and look for tracebacks, schema-version mismatches, or missing venue accounts.

## Bot in `ERROR` with `last_error` set

- Inspect `last_error` first — it's usually a clear message ("strategy validation failed", "venue account not configured", "schema version mismatch").
- Fetch full logs only if `last_error` is opaque.

## Connection refused / URLError

- The API isn't reachable at `RAVN_API_URL`.
- For local dev: did the user start the API? `make docker` or `uvicorn api.main:app`.
- For hosted: confirm the URL with the user.

## Browser sign-in shows "Load error"

- For CLI login on staging, make sure the CLI was started with the staging API, not the frontend URL or production API:
  `python3 scripts/raven_cli.py --api-url https://dev-api.ravn.gg login`
- If the browser reaches the consent page but cannot load the pairing, re-run login with a freshly installed CLI. Newer clients put the API origin in the consent URL so the staging page approves the same environment that created the pairing.
- If Magic itself shows the load error before the Raven consent page appears, the staging domain may be missing from the Magic app's allowed domains. That is a deployment/config issue, not an API-key retry problem.

## "Trading blocked" in bootstrap

- `trading_blocked=true` means the bot's RiskManager is preventing new orders.
- Read `trading_block_reason` — common reasons: circuit breaker tripped, missing market data, venue account suspended, insufficient balance.
- This is not a bug to fix from the agent side; surface it to the user.
