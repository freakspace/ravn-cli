---
name: ravn
description: Drive a Ravn prediction-market trading platform from chat. Generate, validate, deploy, and monitor trading strategies on a user's Ravn instance via its REST API. Use when the user asks to build a strategy, deploy or stop a bot, run a backtest or simulation, inspect bot status/logs/events/sessions, review trades or P&L, or otherwise manage their Ravn account. Covers: strategies (CRUD + validate), bots (deploy/start/stop/redeploy/delete/inspect), sessions (list/trades/orders/equity), backtests, simulations, and recordings.
---

# Ravn

Connect to a user's Ravn trading platform and manage their strategies and bots through the REST API. The user retains full control — every meaningful action goes through their account, gated by their tier.

The skill ships a single Python helper, `scripts/raven_cli.py`, that wraps the API. Stdlib only, no installation needed beyond Python 3.10+.

## Setup (run once per environment)

The user must have:

1. A Ravn account with API keys enabled (`FEATURE_API_KEYS` on their tier).
2. The API base URL — defaults to `https://api.ravn.gg` (hosted Ravn). Self-hosted users override via `RAVN_API_URL`, `--api-url`, or by editing `api_url` in `~/.config/ravn/config.json`.

### Recommended: browser sign-in (zero copy-paste)

Tell the user you're going to sign them in, then run:

```bash
python3 scripts/raven_cli.py --api-url <api_url> login
```

The CLI opens the user's browser to a Ravn consent page and polls the API until the user approves. The user signs in (existing Magic Link flow), verifies the confirmation code, clicks **Approve**, and the API key is delivered back to the waiting CLI over the same API environment. The key is saved to `~/.config/ravn/config.json` (mode 0600). End-to-end: ~30 seconds, no manual key paste.

For staging, use the staging API URL explicitly:

```bash
python3 scripts/raven_cli.py --api-url https://dev-api.ravn.gg login
```

### Service-account mode: recommended for long-lived agent installs

A "service account" is a child principal the human owns. Instead of acting as the human (with a personal key), the agent acts as a *child* of the human — its own user record, its own API key, independent revocation, independent live-trading approval, separate audit trail.

**Default to suggesting service-account mode on first install.** Personal-key mode is fine for one-off scripts; service-account mode is the right default for any agent the user will rely on repeatedly. Ask the user explicitly before provisioning — it's a durable identity under their account that consumes their tier's resources.

```bash
# Ask first, then run:
python3 scripts/raven_cli.py login \
  --as-service-account \
  --service-account-name "claude-cli"   # or whatever name fits the use case
```

The flow is identical to personal sign-in (browser opens, user approves), except:
- The consent page tells the human a *child* will be created, not a personal key.
- A new `account_type=service` user is provisioned under their account.
- The CLI saves the *child's* API key to `~/.config/ravn/config.json` and stores `account_mode: "service_account"` so subsequent commands know which principal they're acting as.

**Things the agent must tell the user up front:**

1. **It's a separate identity.** Bots/strategies the agent creates belong to the child, not to them. They show up in **Settings → Service Accounts** in the web app, with options to revoke or deactivate.
2. **Quotas are pooled.** `max_bots`, `max_strategy_versions`, `max_stored_sessions`, `session_retention_days`, etc. are shared between the human and all their children. If the human is on a tier with `max_bots=3`, that's 3 bots *across* themselves and the new child, not 3 each.
3. **Live trading needs a second approval.** The child starts with `can_trade_live=false`. Even after the human approves the bot's `--allow-live` flag, the child's live-trading flag must be flipped separately by the owner in **Settings → Service Accounts → Approve live**. Until then, any live deploy by the child returns 403. Surface this if the user asks the agent to deploy live.
4. **Tier requirement.** The owner's tier must include the `service_accounts` feature flag. If they don't have it, the CLI fails with a 403; tell them to enable it (admin) or fall back to personal-key mode.

**Remote / headless agents (no local browser):** the consent flow needs the user's browser on the same machine the CLI is running on. If you're running on a remote server / SSH session / CI / Codex Cloud where there's no browser to open, this flow won't work. The user instead creates an **enrollment token** in Settings → Service Accounts and pastes it to the agent, which calls:

```bash
curl -X POST "$RAVN_API_URL/api/service-accounts/enroll" \
  -H "Content-Type: application/json" \
  -d '{"enrollment_token":"rvn_sat_...","name":"<agent-name>"}'
```

The response includes the child account summary and the API key (shown once). Save the key to `~/.config/ravn/config.json` manually or pass it as `RAVN_API_KEY`.

### Asking the user to manage their service accounts

The agent **cannot** approve live trading on itself, rotate its own key, or deactivate itself — those are owner-only routes (`get_current_human_jwt_user`). When the user needs to do any of these, point them at the web app:

> "Go to **Settings → Service Accounts** in the web app. You'll see the `<name>` agent there with options to approve live trading, rotate the API key, or deactivate the account."

### Alternative: bring-your-own key

If the user already has a key (created in Settings → API Keys), they can export it:

```bash
export RAVN_API_KEY=rvn_...
export RAVN_API_URL=https://app.example.com   # optional; defaults to https://api.ravn.gg
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

`--api-key` flag → `RAVN_API_KEY` env → `~/.config/ravn/config.json` (set by `login`). Same for `--api-url` / `RAVN_API_URL`.

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
8. **Ask before provisioning a service account.** `--as-service-account` creates a persistent child identity that pools the user's quota and shows up in their Settings → Service Accounts. Always describe what you're about to create (name, what it'll be used for) and get explicit yes/no before running the command. Don't reuse a "yes" from earlier in the conversation — fresh approval per provision.

See [references/safety.md](references/safety.md) for trading-safety rules in detail and [references/troubleshooting.md](references/troubleshooting.md) for common error patterns.

## Common recipes

See [references/workflows.md](references/workflows.md) for end-to-end transcripts: "build and deploy from scratch", "backtest a draft before going live", "diagnose a stuck bot", "rotate to a new strategy version".

## Self-test

Verify the CLI parses correctly without hitting the network:

```bash
python3 scripts/raven_cli.py self-test
```

Expected: `raven_cli self-test: 37 commands wired correctly`.
