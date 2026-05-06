# Raven Skill for AI Agents

A self-contained skill that lets an AI assistant (Claude Code, Codex, or any agent that supports skills) drive your [Raven](https://github.com/freakspace/raven) trading platform via its REST API. Generate strategies, deploy bots, run backtests, and monitor trading — all from chat.

## What you get

A natural-language interface to your Raven account:

- **"Build me a strategy that buys when EMA crosses up on this market and sells at 5% profit."** → AI drafts the strategy JSON, validates it against the live server schema, and saves it to your account.
- **"Deploy that simulated."** → bot starts in seconds.
- **"How is bot X doing?"** → AI summarizes status, P&L, and recent anomalies.
- **"Backtest the latest version against last week's data."** → backtest runs, AI reports the summary.
- **"Stop the EMA bot and roll it onto v3."** → bot stops, redeploys on the new version.

## Requirements

- **Python 3.10 or newer** (no third-party packages — stdlib only).
- A Raven instance you can reach (self-hosted or hosted).
- A Raven account with **API keys enabled** on your tier.

## Install

### Claude Code

```bash
mkdir -p ~/.claude/skills
cp -r raven ~/.claude/skills/
```

Restart Claude Code (or open a new conversation) — `raven` will appear in available skills.

### Codex / OpenAI agents

```bash
mkdir -p ~/.agents/skills
cp -r raven ~/.agents/skills/
```

Codex picks up the manifest at `agents/openai.yaml`.

### Project-local install

If you only want the skill in one project, copy it into `<project>/.claude/skills/raven` (Claude Code) or `<project>/.agents/skills/raven` (Codex) instead.

## Configure

You have two options.

### Option 1 (recommended): browser sign-in

```bash
python3 ~/.claude/skills/raven/scripts/raven_cli.py --api-url https://your.raven.url login
```

The CLI opens your browser to a Raven consent page. Sign in, click **Approve**, and a fresh API key is saved to `~/.config/raven/config.json` (mode 0600). You won't need to set any environment variables.

### Option 2: bring your own key

Generate an API key in **Settings → API Keys** on the web app, then export:

```bash
export RAVEN_API_KEY=rvn_...                  # required
export RAVEN_API_URL=https://your.raven.url   # optional, defaults to http://localhost:8000
```

### Verify

```bash
python3 ~/.claude/skills/raven/scripts/raven_cli.py whoami
```

You should see your email and tier. If you get HTTP 401, re-run `login` (or check `RAVEN_API_KEY`). If you get a connection error, check the API URL.

### Logout

```bash
python3 ~/.claude/skills/raven/scripts/raven_cli.py logout
```

Removes the local config file. The API key itself remains valid until you revoke it in **Settings → API Keys**.

### Auth lookup order

The CLI tries each in order: `--api-key` flag → `RAVEN_API_KEY` env var → `~/.config/raven/config.json` (set by `login`). Same precedence for the API URL.

## Usage

Once installed, just ask your agent in plain language. The skill description triggers automatically when you mention strategies, bots, backtests, or trading.

Example:

> Use the raven skill. Show me my bots and pick the one that's losing the most this week.

The agent will translate that into the right CLI calls and summarize the results.

## What it can do

| Area | Operations |
|------|------------|
| **Auth / discovery** | whoami, fetch live schema docs, list available signal types |
| **Strategies** | list, get, create, update (new version), rename (metadata only), delete, validate-without-saving |
| **Bots** | list, get, deploy, start, stop, redeploy, delete, rename, logs, events, bootstrap, sessions |
| **Sessions** | list, get, stop, delete, trades, orders, equity, logs |
| **Simulations** | start a real-time simulation session |
| **Backtests** | single trial or Monte Carlo against a recording or past session |
| **Recordings** | list, start, stop, delete |

Run `python3 scripts/raven_cli.py --help` for the full command tree.

## Safety defaults

- **All deployments are simulated by default.** Live deployment requires explicit `--allow-live` *and* explicit user approval in the conversation. The agent is instructed not to carry approval across turns.
- **Destructive operations require `--confirm`.** Deleting bots, sessions, and recordings is irreversible.
- **The agent does not auto-close positions.** Stranded positions or stuck pending orders are surfaced for human decision, not auto-resolved.

See `references/safety.md` for the full rule set.

## How it works

The skill is just a folder of instructions and a single Python script:

```
raven/
  SKILL.md                   # entry point — what the agent reads
  agents/openai.yaml         # Codex/OpenAI manifest
  scripts/raven_cli.py       # the API client (~700 lines, stdlib only)
  references/
    workflows.md             # end-to-end recipes
    safety.md                # trading safety rules
    troubleshooting.md       # error reference
  README.md                  # this file
```

The agent reads `SKILL.md`, calls `raven_cli.py` via Bash for each action, and reads the references on demand. There's no daemon, no MCP server, no webhook — just stdlib HTTP calls to your Raven instance.

The CLI also exposes the server's own agent docs (`docs prompt`, `docs signals`) so the agent always works against the schema your server actually accepts, even if the schema evolves.

## Self-test

Verify the CLI parses cleanly without hitting the network:

```bash
python3 scripts/raven_cli.py self-test
```

## Limitations

- WebSocket streams (live event monitoring) are not wrapped — use `bots events --hours N` polling instead.
- Wallet operations (cancel order, sell position, claim winnings) are intentionally not exposed. Those are user-initiated actions; deferring to the web app prevents the agent from moving real funds without a clear UI confirmation.
- API key creation/revocation is JWT-only on the server side, so the agent can't rotate its own key. Manage keys in the web app.

## License

MIT. See `LICENSE` if shipped, or follow the license of your distribution.
