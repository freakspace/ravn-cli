---
name: raven
description: Drive a Raven prediction-market trading platform from chat. Generate, validate, deploy, and monitor trading strategies on a user's Raven instance via its REST API. Use when the user asks to build a strategy, deploy or stop a bot, run a backtest or simulation, inspect bot status/logs/events/sessions, review trades or P&L, or otherwise manage their Raven account. Covers: strategies (CRUD + validate), bots (deploy/start/stop/redeploy/delete/inspect), sessions (list/trades/orders/equity), backtests, simulations, and recordings.
---

# Raven

Connect to a user's Raven trading platform and manage their strategies and bots through the REST API. The user retains full control — every meaningful action goes through their account, gated by their tier.

The skill ships a single Python helper, `scripts/raven_cli.py`, that wraps the API. Stdlib only, no installation needed beyond Python 3.10+.

## Setup (run once per environment)

The user must have:

1. A Raven account with API keys enabled (`FEATURE_API_KEYS` on their tier).
2. The API base URL — defaults to `http://localhost:8000` for self-hosted dev. For hosted, ask the user.

### Recommended: browser sign-in (zero copy-paste)

Tell the user you're going to sign them in, then run:

```bash
python3 scripts/raven_cli.py --api-url <api_url> login
```

The CLI starts a local listener on `127.0.0.1:<random>`, opens the user's browser to a Raven consent page, and waits. The user signs in (existing Magic Link flow), clicks **Approve**, and the consent page POSTs an API key to the local listener. The key is saved to `~/.config/raven/config.json` (mode 0600). End-to-end: ~30 seconds, no manual key paste.

### Alternative: bring-your-own key

If the user already has a key (created in Settings → API Keys), they can export it:

```bash
export RAVEN_API_KEY=rvn_...
export RAVEN_API_URL=https://app.example.com   # optional; defaults to localhost
```

### Verify

```bash
python3 scripts/raven_cli.py whoami
```

If `whoami` prints the user's email and tier, you're good. If it returns 401, the key is wrong or revoked. If it returns 403 on subsequent calls, the user's tier doesn't include the feature you're using — tell them, don't retry.

### Logout

```bash
python3 scripts/raven_cli.py logout
```

Removes the local config file. Tell the user that the key is still valid until they revoke it in **Settings → API Keys** on the web app.

### Auth lookup order

`--api-key` flag → `RAVEN_API_KEY` env → `~/.config/raven/config.json` (set by `login`). Same for `--api-url` / `RAVEN_API_URL`.

## How to drive a conversation

The user is talking to you in natural language. Translate their intent into CLI calls; do not paste raw JSON at them unless they ask. Key flows below.

### Authoring a new strategy

1. **Load the schema first.** Run `docs prompt` once per session to get the canonical strategy JSON schema and signal list. Run `docs signals` for the live signal types if the prompt looks stale.
2. Discuss the strategy with the user in plain language: what market, what signal, what trigger, what action.
3. Draft the strategy JSON yourself, applying the schema. Save it to a temp file.
4. **Validate before saving.** `strategies validate --json /tmp/draft.json`. Validation is public (no key needed) and is the cheapest way to catch mistakes.
5. If invalid, fix and re-validate. Don't create a known-broken strategy.
6. `strategies create --json /tmp/draft.json --name "..."` to persist.
7. Show the user a brief summary and ask if they want to deploy it.

### Deploying a bot

```bash
bots deploy --strategy <id> --mode simulated --name "EMA cross test"
```

- **Default to simulated.** Always. Live trading risks real funds; the user must explicitly approve every live deployment.
- For live: `bots deploy --strategy <id> --mode live --allow-live` — the `--allow-live` flag is a hard gate. Do not pass it unless the user has clearly said "yes, real money."
- After deploy, poll status: `bots get <id>` until `status=RUNNING` (usually within seconds).

### Inspecting a running bot

```bash
bots get <id>            # status, uptime, orders_placed, last_error
bots bootstrap <id>      # current strategy graph + pending/active orders
bots events <id>         # recent timeline events
bots logs <id> --tail 200
bots sessions <id>       # list trading sessions belonging to this bot
```

If the user asks "is my bot okay?", run `bots get`, `bots bootstrap`, and `bots events --hours 1` and summarize — don't dump raw output. Lead with status, surface anomalies (`trading_blocked=true`, recent `ACTION_REJECTED`/`ORDER_FAILED`, stale events), then offer next steps.

### Reviewing performance

```bash
sessions list --bot <bot_id>     # find the session
sessions get <session_id>        # P&L, win rate, drawdown
sessions trades <session_id>     # individual fills
sessions equity <session_id>     # equity curve summary
```

### Backtesting

Backtests need a data source — either a recording or a past session.

```bash
recordings list                                    # available recordings
backtest --strategy <id> --recording <rec_id> --trials 1
# or with a past session as the source:
backtest --strategy <id> --source-session <sess_id> --trials 100  # Monte Carlo
```

Monte Carlo (`--trials > 1`) is tier-gated. If 403, fall back to `--trials 1`.

After kicking off, poll `sessions get <id>` until `status=COMPLETED`.

### Updating an existing strategy

- Metadata only (name/description): `strategies rename <id> --name "..."` (no new version).
- Logic changes: edit the JSON, then `strategies update <id> --json edited.json` (creates a new version).
- After updating, `bots redeploy <bot_id>` to restart the bot on the latest version.

## Rules

1. **Default to simulated.** Live deployment requires `--allow-live` *and* explicit user approval in the same conversation turn. Past approval doesn't carry forward.
2. **Validate before create.** Always run `strategies validate` on a draft before `strategies create`.
3. **Confirm destructive actions.** `bots delete`, `sessions delete`, and `recordings delete` all need `--confirm`. Read the user's words back to them before passing it.
4. **Don't retry on 403.** That's a tier gate, not a transient error. Tell the user which feature they need.
5. **Don't retry on 401.** Auth is broken; tell the user to check their key.
6. **Quote, don't dump.** When summarizing logs/events, pull the key lines. The full output goes to the bundle/file; the conversation gets the summary.
7. **Use `--json` for chained logic.** When piping output to `jq` or another command, pass `--json`. For human conversation, default summary mode is better.

See [references/safety.md](references/safety.md) for trading-safety rules in detail and [references/troubleshooting.md](references/troubleshooting.md) for common error patterns.

## Common recipes

See [references/workflows.md](references/workflows.md) for end-to-end transcripts: "build and deploy from scratch", "backtest a draft before going live", "diagnose a stuck bot", "rotate to a new strategy version".

## Self-test

Verify the CLI parses correctly without hitting the network:

```bash
python3 scripts/raven_cli.py self-test
```

Expected: `raven_cli self-test: 37 commands wired correctly`.
