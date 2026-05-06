# Trading safety rules

The user trusts you to manage real money. These rules are non-negotiable.

## Live trading

1. **Default to simulated.** Every `bots deploy` call without explicit user approval must use `--mode simulated` (the default).
2. **`--allow-live` is a per-decision flag, not a session flag.** Past approval does not carry to the next deployment. Re-confirm every time.
3. **Read the user's words back before passing `--allow-live`.** Example: "You want me to deploy strategy `ema-cross-v1` LIVE on Polymarket with $500 — confirm?" If the user gave a vague "yes, deploy", default to simulated and ask again.
4. **Do not infer live intent from indirect signals.** "Run it for real", "deploy it properly", "go ahead" — none of these are explicit live approval. Ask.

## Position exits

1. **Never auto-close, auto-sell, or auto-cancel a position.** Stranded positions hurt, but unauthorized exits hurt more. Always surface to the user and let them decide.
2. **The bot's RiskManager itself never blocks exits.** If you see an exit blocked at runtime, that's a real bug — report it, don't work around it.

## Destructive operations

These commands require `--confirm`:

- `bots delete <id>` — removes the bot, sessions, timeline events, and metrics. Irreversible.
- `sessions delete <id>` — removes a session and all its trades/orders/equity. Irreversible.
- `recordings delete <id>` — removes the recording file from the server. Backtests that referenced it can no longer be re-run.

Before passing `--confirm`, restate to the user what will be lost.

## Things that look safe but aren't

- **`strategies update`** creates a new version — the old version is preserved, but any bot already running keeps using its version_id. To roll the live bot, follow up with `bots redeploy`.
- **`strategies delete`** fails if any bot or session references the strategy. That's intentional. Don't try to clear references first.
- **`bots stop`** is reversible (`bots start`), but `bots delete` is not. Default to stopping unless the user clearly wants the bot gone forever.

## Rate limits

The server rate-limits expensive mutations (deploy/start/stop/redeploy/delete/backtest/simulation). If you get HTTP 429, **back off and tell the user** — don't retry in a tight loop.

## When in doubt

Ask. The cost of a clarifying question is one extra turn. The cost of an unwanted live trade is real money.
