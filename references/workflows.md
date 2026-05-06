# Workflow recipes

End-to-end recipes the agent can adapt. All commands assume `RAVN_API_KEY` and (optionally) `RAVN_API_URL` are set, and that the agent is in the skill folder so `scripts/raven_cli.py` resolves.

## First-time setup as a service account

Use this on a fresh install when the user wants the agent to act under its own child identity (recommended for any long-lived agent install — see SKILL.md for the rationale and trade-offs).

```bash
# Ask the user first:
# "I'd like to create a service account named '<name>' under your Ravn account.
#  It'll have its own API key, share your tier's quota, and won't be able to
#  trade live until you approve it in Settings. Sound good?"

# After yes, run (this opens their browser):
python3 scripts/raven_cli.py login \
  --as-service-account \
  --service-account-name "<descriptive-name>"

# Verify the new identity. Should show account_type=service, owner=<their email>.
python3 scripts/raven_cli.py whoami

# If the user later wants live trading, point them at:
# "Open Settings → Service Accounts → '<name>' → Approve live."
```

**If the consent flow can't open a browser** (remote server, CI, headless container), fall back to enrollment tokens:

```bash
# 1. Tell the user to open Settings → Service Accounts → Create enrollment token,
#    name it (e.g. '<agent-name>-prod'), and paste the rvn_sat_... value.

# 2. With the token in $RAVN_ENROLL_TOKEN:
RESPONSE=$(curl -s -X POST "$RAVN_API_URL/api/service-accounts/enroll" \
  -H "Content-Type: application/json" \
  -d "{\"enrollment_token\":\"$RAVN_ENROLL_TOKEN\",\"name\":\"<agent-name>\"}")

# 3. Extract the API key (shown once — save it now or lose it).
echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key']['key'])"

# 4. Save it for subsequent CLI calls. Either:
#    a) export RAVN_API_KEY=rvn_...
#    b) or write the config file manually:
mkdir -p ~/.config/ravn
cat > ~/.config/ravn/config.json <<EOF
{"api_url": "$RAVN_API_URL", "api_key": "<paste rvn_...>", "account_mode": "service_account"}
EOF
chmod 600 ~/.config/ravn/config.json
```

## Build and deploy from scratch

```bash
# 1. Verify auth, see what features the user has
python3 scripts/raven_cli.py whoami

# 2. Load the strategy JSON schema for this server version
python3 scripts/raven_cli.py docs prompt > /tmp/raven-prompt.md
python3 scripts/raven_cli.py docs signals > /tmp/raven-signals.txt

# 3. Discuss with user, draft strategy JSON to /tmp/draft.json

# 4. Validate (public endpoint, no auth needed for validate alone)
python3 scripts/raven_cli.py strategies validate --json /tmp/draft.json

# 5. Persist
python3 scripts/raven_cli.py strategies create --json /tmp/draft.json --name "EMA cross v1"
# -> note the public_id from the summary

# 6. Deploy in simulated mode
python3 scripts/raven_cli.py bots deploy --strategy <strategy_public_id> --mode simulated

# 7. Wait for RUNNING, then inspect
python3 scripts/raven_cli.py bots get <bot_public_id>
```

## Backtest a draft before going live

```bash
# 1. Find a recording covering the markets you care about
python3 scripts/raven_cli.py recordings list

# 2. Run a single-trial backtest
python3 scripts/raven_cli.py backtest \
    --strategy <strategy_id> \
    --recording <recording_id> \
    --initial-balance 10000 \
    --trials 1

# 3. Poll until COMPLETED
python3 scripts/raven_cli.py sessions get <session_id>

# 4. Read the summary; if win_rate / drawdown look acceptable, deploy live (with explicit user approval)
python3 scripts/raven_cli.py sessions get <session_id>
python3 scripts/raven_cli.py sessions trades <session_id>
python3 scripts/raven_cli.py sessions equity <session_id>
```

## Diagnose a stuck or misbehaving bot

```bash
# Status snapshot
python3 scripts/raven_cli.py bots get <bot_id>
# Look for: status != RUNNING, last_error set, trading_blocked

# Strategy graph + pending orders
python3 scripts/raven_cli.py bots bootstrap <bot_id> --hours 2

# Recent timeline — focus on rejections/failures
python3 scripts/raven_cli.py bots events <bot_id> --hours 2 --limit 1000 --json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(e) for e in d['events'] if e['event_type'] in ('ACTION_REJECTED','ACTION_ATTEMPT_BLOCKED','ORDER_FAILED','ORDER_REJECTED','ORDER_EXPIRED')]"

# Runtime logs
python3 scripts/raven_cli.py bots logs <bot_id> --tail 500
```

If you find a stranded position or stuck pending order, surface it to the user — do **not** auto-cancel or auto-sell. Position exits are the user's call.

## Update strategy logic and roll the bot

```bash
# Edit the JSON locally, then push
python3 scripts/raven_cli.py strategies update <strategy_id> --json edited.json

# Restart the running bot on the new version
python3 scripts/raven_cli.py bots redeploy <bot_id>

# Confirm the new version is live
python3 scripts/raven_cli.py bots get <bot_id>
# Check that strategy_version_id changed
```

## Switch a deployed bot from simulated to live

There is no in-place mode swap. Stop and redeploy:

```bash
# 1. Stop simulated
python3 scripts/raven_cli.py bots stop <sim_bot_id>

# 2. Get explicit user approval for live trading

# 3. Deploy live (note --allow-live)
python3 scripts/raven_cli.py bots deploy \
    --strategy <strategy_id> \
    --mode live \
    --allow-live \
    --name "live: EMA cross v1"
```

## Quick "how am I doing?" snapshot

```bash
python3 scripts/raven_cli.py bots list
# pick the bots you want to look at, then for each:
python3 scripts/raven_cli.py bots get <id>
python3 scripts/raven_cli.py sessions list --bot <id> --page-size 5
# pick the most recent live/sim session:
python3 scripts/raven_cli.py sessions get <session_id>
```

Summarize: number of bots running, total orders placed today, top winning/losing session. Don't dump raw output.
