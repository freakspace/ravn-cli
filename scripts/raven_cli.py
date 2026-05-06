#!/usr/bin/env python3
"""raven_cli — Drive a Ravn trading platform instance from the command line.

Designed for AI-agent use: every command prints a compact human-readable summary
by default, or raw JSON with --json. Auth is via Bearer API key (`rvn_*`) read
from --api-key or the RAVN_API_KEY env var.

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_URL = "https://api.ravn.gg"
DEFAULT_TIMEOUT = 30.0
LOGIN_LISTENER_TIMEOUT_SECONDS = 300

# Crockford-ish alphabet kept in sync with api/routers/cli_auth.py to derive a
# short OOB confirmation code from the PKCE challenge. Both sides compute the
# same string so the user can verify their browser belongs to this CLI session.
_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_USER_CODE_LENGTH = 6


def _derive_user_code(code_challenge: str) -> str:
    digest = hashlib.sha256(code_challenge.encode("utf-8")).hexdigest().upper()
    raw = bytes.fromhex(digest)
    out: list[str] = []
    for byte in raw:
        out.append(_USER_CODE_ALPHABET[byte % len(_USER_CODE_ALPHABET)])
        if len(out) >= _USER_CODE_LENGTH:
            break
    return "".join(out[:_USER_CODE_LENGTH])


def config_path() -> Path:
    """Return the path to the persistent config file (XDG-aware)."""
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ravn" / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_config(data: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def resolve_api_key(cli_value: str | None) -> str | None:
    """Resolve API key with precedence: --api-key > env var > config file."""
    if cli_value:
        return cli_value
    env = os.getenv("RAVN_API_KEY")
    if env:
        return env
    cfg = load_config()
    return cfg.get("api_key")


def resolve_api_url(cli_value: str | None) -> str:
    """Resolve API URL with precedence: --api-url > $RAVN_API_URL > config file > default."""
    if cli_value:
        return cli_value
    env = os.getenv("RAVN_API_URL")
    if env:
        return env
    cfg = load_config()
    return cfg.get("api_url") or DEFAULT_API_URL


class ApiError(RuntimeError):
    """Raised for Ravn API failures."""


class RavnClient:
    def __init__(self, api_url: str, api_key: str | None):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        params: dict[str, Any] | None = None,
        require_auth: bool = True,
        accept: str = "application/json",
    ) -> Any:
        if require_auth and not self.api_key:
            raise ApiError(
                "Missing API key. Set RAVN_API_KEY or pass --api-key."
            )

        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                path = f"{path}?{urlencode(filtered)}"

        body = None
        headers = {"Accept": accept, "User-Agent": "ravn-cli/0.1"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(
            f"{self.api_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
                raw = response.read()
                if not raw:
                    return None
                text = raw.decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
                if accept.startswith("application/json") and "application/json" in content_type:
                    return json.loads(text)
                return text
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(
                f"{method} {path} failed: HTTP {exc.code}: {detail.strip()}"
            ) from exc
        except URLError as exc:
            raise ApiError(f"{method} {path} failed: {exc.reason}") from exc

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, payload: Any = None, **kwargs: Any) -> Any:
        return self.request("POST", path, payload=payload or {}, **kwargs)

    def put(self, path: str, payload: Any, **kwargs: Any) -> Any:
        return self.request("PUT", path, payload=payload, **kwargs)

    def patch(self, path: str, payload: Any, **kwargs: Any) -> Any:
        return self.request("PATCH", path, payload=payload, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)


def load_json_arg(value: str) -> Any:
    """Load JSON from a file path or '-' for stdin."""
    if value == "-":
        return json.loads(sys.stdin.read())
    path = Path(value)
    if not path.is_file():
        raise SystemExit(f"JSON file not found: {value}")
    return json.loads(path.read_text(encoding="utf-8"))


def emit(args: argparse.Namespace, summary: str, payload: Any) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(summary)


def fmt_strategy_row(s: dict[str, Any]) -> str:
    public = s.get("public_id") or s.get("id")
    name = s.get("name") or "(unnamed)"
    versions = s.get("version_number")
    active = "active" if s.get("is_active", True) else "inactive"
    return f"{public:<20}  v{versions:<4}  {active:<8}  {name}"


def fmt_bot_row(b: dict[str, Any]) -> str:
    public = b.get("public_id") or b.get("id")
    name = b.get("name") or b.get("strategy_name") or "(unnamed)"
    status = b.get("status", "?")
    mode = b.get("execution_mode", "?")
    orders = b.get("orders_placed", 0)
    return f"{public:<20}  {status:<10}  {mode:<10}  orders={orders:<5}  {name}"


def fmt_session_row(s: dict[str, Any]) -> str:
    public = s.get("public_id") or s.get("id")
    stype = s.get("session_type", "?")
    status = s.get("status", "?")
    summary = s.get("summary") or {}
    pnl = summary.get("total_pnl")
    pnl_str = f"pnl={pnl:.2f}" if isinstance(pnl, (int, float)) else "pnl=-"
    return f"{public:<20}  {stype:<11}  {status:<10}  {pnl_str:<14}"


# -------- command handlers --------


def cmd_whoami(client: RavnClient, args: argparse.Namespace) -> int:
    # `/api/agent/whoami` accepts both interactive JWT and `rvn_*` API keys,
    # which is what the CLI uses. The legacy `/api/auth/session` route is
    # human-only and 403s on API keys.
    data = client.get("/api/agent/whoami")
    user = (data or {}).get("user") or {}
    summary = (
        f"authenticated as {user.get('email') or user.get('username') or '?'}\n"
        f"  id                  : {user.get('id')}\n"
        f"  account_type        : {user.get('account_type')}\n"
        f"  is_service_account  : {user.get('is_service_account')}\n"
        f"  owner_user_id       : {user.get('owner_user_id')}\n"
        f"  is_admin            : {user.get('is_admin')}\n"
        f"  scopes              : {', '.join(user.get('scopes') or []) or '(none)'}"
    )
    emit(args, summary, data)
    return 0


def cmd_docs_guide(client: RavnClient, args: argparse.Namespace) -> int:
    text = client.get("/api/agent/guide", require_auth=False, accept="text/markdown")
    print(text if isinstance(text, str) else json.dumps(text, indent=2))
    return 0


def cmd_docs_prompt(client: RavnClient, args: argparse.Namespace) -> int:
    text = client.get(
        "/api/agent/system-prompt", require_auth=False, accept="text/markdown"
    )
    print(text if isinstance(text, str) else json.dumps(text, indent=2))
    return 0


def cmd_docs_signals(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get("/api/strategies/signals/available", require_auth=False)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    sigs = (data or {}).get("signals") or []
    schemas = (data or {}).get("schemas") or {}
    print(f"version: {(data or {}).get('version', '?')}")
    print(f"signals ({len(sigs)}):")
    for sig in sigs:
        schema = schemas.get(sig) or {}
        req = ", ".join(schema.get("required_fields") or [])
        opt = ", ".join(schema.get("optional_fields") or [])
        print(f"  {sig}")
        if req:
            print(f"    required: {req}")
        if opt:
            print(f"    optional: {opt}")
    return 0


def cmd_strategies_list(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get("/api/strategies")
    items = data if isinstance(data, list) else []
    summary = "\n".join(fmt_strategy_row(s) for s in items) or "(no strategies)"
    summary = f"{len(items)} strategies\n" + summary
    emit(args, summary, data)
    return 0


def cmd_strategies_get(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(f"/api/strategies/{args.id}")
    s = data or {}
    summary = (
        f"strategy {s.get('public_id') or s.get('id')}\n"
        f"  name        : {s.get('name')}\n"
        f"  description : {s.get('description') or ''}\n"
        f"  version     : v{s.get('version_number')}  (latest_version_id={s.get('latest_version_id')})\n"
        f"  schema      : {s.get('schema_version')}  app={s.get('app_version')}\n"
        f"  is_active   : {s.get('is_active')}\n"
        f"  updated_at  : {s.get('updated_at')}"
    )
    emit(args, summary, data)
    return 0


def cmd_strategies_create(client: RavnClient, args: argparse.Namespace) -> int:
    payload = load_json_arg(args.json_file)
    # Auto-wrap a bare strategy graph (matches `strategies validate` tolerance).
    if "definition" not in payload:
        bare = payload
        payload = {"definition": bare}
        if "name" in bare:
            payload["name"] = bare["name"]
        if "description" in bare:
            payload["description"] = bare["description"]
    if args.name:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if "name" not in payload:
        raise SystemExit("Strategy JSON must include 'name' (or pass --name).")
    data = client.post("/api/strategies", payload)
    s = data or {}
    summary = (
        f"created strategy {s.get('public_id') or s.get('id')} "
        f"(v{s.get('version_number')}): {s.get('name')}"
    )
    emit(args, summary, data)
    return 0


def cmd_strategies_update(client: RavnClient, args: argparse.Namespace) -> int:
    payload = load_json_arg(args.json_file)
    # Auto-wrap a bare strategy graph (matches `strategies validate` tolerance).
    if "definition" not in payload:
        bare = payload
        payload = {"definition": bare}
        if "name" in bare:
            payload["name"] = bare["name"]
        if "description" in bare:
            payload["description"] = bare["description"]
    data = client.put(f"/api/strategies/{args.id}", payload)
    s = data or {}
    summary = (
        f"updated strategy {s.get('public_id') or s.get('id')} "
        f"to v{s.get('version_number')}: {s.get('name')}"
    )
    emit(args, summary, data)
    return 0


def cmd_strategies_rename(client: RavnClient, args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {}
    if args.name is not None:
        payload["name"] = args.name
    if args.description is not None:
        payload["description"] = args.description
    if not payload:
        raise SystemExit("Provide at least one of --name or --description.")
    data = client.patch(f"/api/strategies/{args.id}", payload)
    s = data or {}
    summary = f"renamed strategy {s.get('public_id') or s.get('id')}: {s.get('name')}"
    emit(args, summary, data)
    return 0


def cmd_strategies_delete(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.delete(f"/api/strategies/{args.id}")
    summary = f"deleted strategy {args.id}: {data}"
    emit(args, summary, data)
    return 0


def cmd_strategies_validate(client: RavnClient, args: argparse.Namespace) -> int:
    payload = load_json_arg(args.json_file)
    request_body = {"definition": payload.get("definition", payload)}
    data = client.post(
        "/api/strategies/validate", request_body, require_auth=False
    )
    d = data or {}
    valid = d.get("valid")
    errors = d.get("errors") or []
    warnings = d.get("warnings") or []
    lines = [f"valid: {valid}"]
    if errors:
        lines.append(f"errors ({len(errors)}):")
        lines.extend(f"  - {e}" for e in errors)
    if warnings:
        lines.append(f"warnings ({len(warnings)}):")
        lines.extend(f"  - {w}" for w in warnings)
    summary = "\n".join(lines)
    emit(args, summary, data)
    return 0 if valid else 2


def cmd_bots_list(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get("/api/bots")
    items = (data or {}).get("bots") if isinstance(data, dict) else (data or [])
    if not isinstance(items, list):
        items = []
    summary = "\n".join(fmt_bot_row(b) for b in items) or "(no bots)"
    summary = f"{len(items)} bots\n" + summary
    emit(args, summary, data)
    return 0


def cmd_bots_get(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(f"/api/bots/{args.id}")
    b = data or {}
    summary = (
        f"bot {b.get('public_id') or b.get('id')}\n"
        f"  name           : {b.get('name') or b.get('strategy_name')}\n"
        f"  status         : {b.get('status')}\n"
        f"  execution_mode : {b.get('execution_mode')}\n"
        f"  strategy       : {b.get('strategy_name')} (id={b.get('strategy_id')}, version={b.get('strategy_version_id')})\n"
        f"  uptime_sec     : {b.get('uptime')}\n"
        f"  orders_placed  : {b.get('orders_placed')}\n"
        f"  last_error     : {b.get('last_error') or ''}\n"
        f"  runtime        : {b.get('runtime_version')} (target={b.get('runtime_target_version')})\n"
        f"  placement      : {b.get('runtime_placement')}"
    )
    emit(args, summary, data)
    return 0


def cmd_bots_deploy(client: RavnClient, args: argparse.Namespace) -> int:
    if args.mode == "live" and not args.allow_live:
        raise SystemExit(
            "Refusing live deployment. Re-run with --allow-live ONLY after the user "
            "has explicitly approved trading with real funds."
        )
    payload: dict[str, Any] = {"execution_mode": args.mode}
    if args.strategy:
        payload["strategy_id"] = args.strategy
    if args.version:
        payload["strategy_version_id"] = args.version
    if args.name:
        payload["name"] = args.name
    if args.season:
        payload["season_id"] = args.season
    if args.enable_recording:
        payload["enable_recording"] = True
    sim_cfg: dict[str, Any] = {}
    if args.initial_balance is not None:
        sim_cfg["initial_balance"] = args.initial_balance
    if args.fill_rate is not None:
        sim_cfg["fill_rate"] = args.fill_rate
    if args.slippage is not None:
        sim_cfg["slippage_pct"] = args.slippage
    if sim_cfg:
        payload["simulated_config"] = sim_cfg
    if not payload.get("strategy_id") and not payload.get("strategy_version_id"):
        raise SystemExit("Provide --strategy <id> or --version <id>.")
    data = client.post("/api/bots", payload)
    b = (data or {}).get("bot") or {}
    summary = (
        f"deployed bot {b.get('public_id') or b.get('id')} "
        f"({b.get('execution_mode')}): {b.get('name') or b.get('strategy_name')} "
        f"status={b.get('status')}"
    )
    emit(args, summary, data)
    return 0


def cmd_bots_start(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.post(f"/api/bots/{args.id}/start")
    b = (data or {}).get("bot") or {}
    emit(args, f"started bot {args.id}: status={b.get('status')}", data)
    return 0


def cmd_bots_stop(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.post(f"/api/bots/{args.id}/stop")
    b = (data or {}).get("bot") or {}
    emit(args, f"stopped bot {args.id}: status={b.get('status')}", data)
    return 0


def cmd_bots_redeploy(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.post(f"/api/bots/{args.id}/redeploy")
    b = (data or {}).get("bot") or {}
    emit(args, f"redeployed bot {args.id}: status={b.get('status')}", data)
    return 0


def cmd_bots_delete(client: RavnClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        raise SystemExit(
            "Refusing to delete bot without --confirm. This is irreversible "
            "and removes all sessions/timeline/metrics for the bot."
        )
    data = client.delete(f"/api/bots/{args.id}")
    emit(args, f"deleted bot {args.id}: {data}", data)
    return 0


def cmd_bots_rename(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.patch(f"/api/bots/{args.id}", {"name": args.name})
    b = data or {}
    emit(args, f"renamed bot {b.get('public_id') or b.get('id')}: {b.get('name')}", data)
    return 0


def cmd_bots_logs(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(f"/api/bots/{args.id}/logs", params={"tail": args.tail})
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    d = data or {}
    print(f"# logs source={d.get('source')} bot={d.get('bot_id')}")
    print(d.get("logs") or "")
    return 0


def cmd_bots_events(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(
        f"/api/bots/{args.id}/monitor/events",
        params={"hours": args.hours, "limit": args.limit},
    )
    events = (data or {}).get("events") or []
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0
    print(f"{len(events)} events (hours={args.hours}, limit={args.limit})")
    for e in events[-args.show :]:
        print(
            f"  {e.get('ts')}  {e.get('event_type'):<28}  "
            f"market={e.get('market_id') or '-'}  price={e.get('price')}"
        )
    return 0


def cmd_bots_bootstrap(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(
        f"/api/bots/{args.id}/monitor/bootstrap",
        params={"hours": args.hours, "load_cold_data": "true"},
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0
    d = data or {}
    sg = (d.get("strategy_graph") or {}).get("definition") or {}
    market_nodes = sg.get("market_nodes") or []
    signals = sg.get("signals") or []
    pending = d.get("pending_orders") or []
    active = d.get("active_orders") or []
    summary = (
        f"bot bootstrap (hours={args.hours})\n"
        f"  bot_id          : {d.get('bot_id')}\n"
        f"  session_id      : {d.get('session_id')}\n"
        f"  trading_blocked : {d.get('trading_blocked')}\n"
        f"  block_reason    : {d.get('trading_block_reason') or ''}\n"
        f"  strategy_graph  : {len(market_nodes)} market nodes, {len(signals)} signals\n"
        f"  pending_orders  : {len(pending)}\n"
        f"  active_orders   : {len(active)}"
    )
    print(summary)
    return 0


def cmd_bots_sessions(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(f"/api/bots/{args.id}/sessions")
    items = data if isinstance(data, list) else []
    summary = "\n".join(fmt_session_row(s) for s in items) or "(no sessions)"
    summary = f"{len(items)} sessions for bot {args.id}\n" + summary
    emit(args, summary, data)
    return 0


def cmd_sessions_list(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(
        "/api/sessions",
        params={
            "bot_id": args.bot,
            "session_type": args.type,
            "page": args.page,
            "page_size": args.page_size,
        },
    )
    items = (data or {}).get("sessions") if isinstance(data, dict) else (data or [])
    if not isinstance(items, list):
        items = []
    summary = "\n".join(fmt_session_row(s) for s in items) or "(no sessions)"
    summary = f"{len(items)} sessions (page {args.page})\n" + summary
    emit(args, summary, data)
    return 0


def cmd_sessions_get(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(f"/api/sessions/{args.id}")
    s = data or {}
    summary_dict = s.get("summary") or {}
    pnl = summary_dict.get("total_pnl")
    summary = (
        f"session {s.get('public_id') or s.get('id')}\n"
        f"  type        : {s.get('session_type')}\n"
        f"  status      : {s.get('status')}\n"
        f"  venues      : {', '.join(s.get('venues') or [])}\n"
        f"  started_at  : {s.get('started_at')}\n"
        f"  duration    : {s.get('duration_seconds')} sec\n"
        f"  total_pnl   : {pnl}\n"
        f"  trades      : {summary_dict.get('total_trades')}\n"
        f"  win_rate    : {summary_dict.get('win_rate')}\n"
        f"  drawdown    : {summary_dict.get('max_drawdown_pct')}\n"
        f"  sharpe      : {summary_dict.get('sharpe_ratio')}"
    )
    emit(args, summary, data)
    return 0


def cmd_sessions_stop(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.post(f"/api/sessions/{args.id}/stop")
    emit(args, f"stopped session {args.id}: {data}", data)
    return 0


def cmd_sessions_delete(client: RavnClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        raise SystemExit("Refusing to delete session without --confirm.")
    data = client.delete(f"/api/sessions/{args.id}")
    emit(args, f"deleted session {args.id}: {data}", data)
    return 0


def cmd_sessions_trades(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(
        f"/api/sessions/{args.id}/trades",
        params={"page": args.page, "page_size": args.page_size},
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0
    items = (data or {}).get("trades") or (data or [])
    if not isinstance(items, list):
        items = []
    print(f"{len(items)} trades (page {args.page})")
    for t in items:
        print(
            f"  {t.get('timestamp')}  {t.get('side'):<5}  "
            f"size={t.get('size')}  fill={t.get('fill_price')}  "
            f"pnl={t.get('realized_pnl')}"
        )
    return 0


def cmd_sessions_orders(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(
        f"/api/sessions/{args.id}/orders",
        params={"page": args.page, "page_size": args.page_size, "status": args.status},
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0
    items = (data or {}).get("orders") or []
    print(f"{len(items)} orders (page {args.page})")
    for o in items:
        print(
            f"  {o.get('first_seen_at')}  {o.get('status'):<12}  "
            f"side={o.get('side')}  filled={o.get('filled_size')}/{o.get('size')}  "
            f"avg_fill={o.get('avg_fill_price')}"
        )
    return 0


def cmd_sessions_equity(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get(f"/api/sessions/{args.id}/equity")
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0
    snaps = (data or {}).get("snapshots") or []
    print(f"{len(snaps)} equity snapshots")
    if snaps:
        first, last = snaps[0], snaps[-1]
        print(
            f"  first: {first.get('timestamp')}  equity={first.get('total_equity')}\n"
            f"  last : {last.get('timestamp')}  equity={last.get('total_equity')}\n"
            f"  realized_pnl   : {last.get('realized_pnl')}\n"
            f"  unrealized_pnl : {last.get('unrealized_pnl')}\n"
            f"  drawdown_pct   : {last.get('drawdown_pct')}"
        )
    return 0


def cmd_sessions_logs(client: RavnClient, args: argparse.Namespace) -> int:
    text = client.get(
        f"/api/sessions/{args.id}/logs",
        params={"tail": args.tail},
        accept="text/plain",
    )
    print(text if isinstance(text, str) else json.dumps(text, indent=2))
    return 0


def cmd_simulate(client: RavnClient, args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"strategy_id": args.strategy}
    if args.initial_balance is not None:
        payload["initial_balance"] = args.initial_balance
    if args.fill_rate is not None:
        payload["fill_rate"] = args.fill_rate
    if args.slippage is not None:
        payload["slippage_pct"] = args.slippage
    if args.duration_sec is not None:
        payload["duration_seconds"] = args.duration_sec
    data = client.post("/api/simulations", payload)
    s = data or {}
    summary = (
        f"started simulation session {s.get('public_id') or s.get('id')} "
        f"status={s.get('status')}"
    )
    emit(args, summary, data)
    return 0


def cmd_backtest(client: RavnClient, args: argparse.Namespace) -> int:
    if bool(args.recording) == bool(args.source_session):
        raise SystemExit(
            "Provide exactly one of --recording <id> or --source-session <id>."
        )
    payload: dict[str, Any] = {"strategy_id": args.strategy}
    if args.recording:
        payload["recording_id"] = args.recording
    else:
        payload["source_session_id"] = args.source_session
    for key, val in (
        ("initial_balance", args.initial_balance),
        ("fill_rate", args.fill_rate),
        ("slippage_pct", args.slippage),
        ("speed_multiplier", args.speed),
        ("trials", args.trials),
        ("fill_rate_stddev", args.fill_rate_stddev),
        ("slippage_stddev", args.slippage_stddev),
        ("random_seed", args.seed),
        ("max_workers", args.max_workers),
    ):
        if val is not None:
            payload[key] = val
    data = client.post("/api/simulations/backtest", payload)
    s = data or {}
    summary = (
        f"started backtest session {s.get('public_id') or s.get('id')} "
        f"trials={args.trials or 1} status={s.get('status')}"
    )
    emit(args, summary, data)
    return 0


def cmd_recordings_list(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.get("/api/simulations/recordings")
    items = data if isinstance(data, list) else []
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
        return 0
    print(f"{len(items)} recordings")
    for r in items:
        markets = ",".join((r.get("market_ids") or [])[:3])
        if len(r.get("market_ids") or []) > 3:
            markets += "..."
        print(
            f"  {r.get('public_id') or r.get('id'):<20}  "
            f"{r.get('status'):<10}  events={r.get('event_count')}  "
            f"dur={r.get('duration_seconds')}s  [{markets}]"
        )
    return 0


def cmd_recordings_start(client: RavnClient, args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"market_ids": args.markets.split(",")}
    if args.duration_sec is not None:
        payload["duration_seconds"] = args.duration_sec
    data = client.post("/api/simulations/recordings", payload)
    r = data or {}
    summary = (
        f"started recording {r.get('public_id') or r.get('id')} "
        f"status={r.get('status')}"
    )
    emit(args, summary, data)
    return 0


def cmd_recordings_stop(client: RavnClient, args: argparse.Namespace) -> int:
    data = client.post(f"/api/simulations/recordings/{args.id}/stop")
    emit(args, f"stopped recording {args.id}: {data}", data)
    return 0


def cmd_recordings_delete(client: RavnClient, args: argparse.Namespace) -> int:
    if not args.confirm:
        raise SystemExit("Refusing to delete recording without --confirm.")
    data = client.delete(f"/api/simulations/recordings/{args.id}")
    emit(args, f"deleted recording {args.id}: {data}", data)
    return 0


class _LoginCallbackHandler(http.server.BaseHTTPRequestHandler):
    """One-shot HTTP handler that captures the API key delivery from the consent page."""

    expected_path = "/callback"
    payload: dict[str, Any] | None = None
    delivered_event: threading.Event | None = None

    def log_message(self, format: str, *args: Any) -> None:  # silence stderr access logs
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # CORS preflight
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != self.expected_path:
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            data = None
        type(self).payload = data if isinstance(data, dict) else None
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<!doctype html><meta charset='utf-8'>"
            b"<title>Ravn CLI</title>"
            b"<body style='font-family:system-ui;padding:2em;text-align:center'>"
            b"<h1>You're signed in</h1>"
            b"<p>Return to your terminal. You can close this tab.</p></body>"
        )
        if type(self).delivered_event is not None:
            type(self).delivered_event.set()


def cmd_login(client: RavnClient, args: argparse.Namespace) -> int:
    if not args.api_url:
        raise SystemExit("API URL not configured.")

    as_service_account = bool(getattr(args, "as_service_account", False))
    sa_name = getattr(args, "service_account_name", None)
    account_mode = "service_account" if as_service_account else "personal_key"

    # PKCE: the verifier is held by this CLI process only. The server
    # receives only the SHA-256 hash. The same hash is later echoed by the
    # browser to the loopback listener, which lets us reject any payload
    # that didn't come from the legitimate flow we started.
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = hashlib.sha256(code_verifier.encode("utf-8")).hexdigest()
    expected_user_code = _derive_user_code(code_challenge)

    server = http.server.HTTPServer(("127.0.0.1", 0), _LoginCallbackHandler)
    port = server.server_address[1]
    callback_url = f"http://127.0.0.1:{port}/callback"
    delivered = threading.Event()

    _LoginCallbackHandler.payload = None
    _LoginCallbackHandler.delivered_event = delivered

    init_payload: dict[str, Any] = {
        "callback_url": callback_url,
        "account_mode": account_mode,
        "code_challenge": code_challenge,
    }
    if account_mode == "service_account" and sa_name:
        init_payload["service_account_name"] = sa_name

    init_client = RavnClient(args.api_url, api_key=None)
    try:
        init_response = init_client.request(
            "POST",
            "/api/cli-auth/init",
            payload=init_payload,
            require_auth=False,
        )
    except ApiError as exc:
        server.server_close()
        raise SystemExit(f"Could not start login flow: {exc}") from exc

    consent_url = (init_response or {}).get("consent_url")
    server_user_code = (init_response or {}).get("user_code")
    if not consent_url:
        server.server_close()
        raise SystemExit(f"Unexpected /init response: {init_response!r}")
    if server_user_code and server_user_code != expected_user_code:
        # If the server returns a different code than we computed locally,
        # we cannot trust the OOB confirmation — abort rather than mislead
        # the user.
        server.server_close()
        raise SystemExit(
            "Server returned a user_code that does not match the local PKCE "
            "challenge. Refusing to continue."
        )

    if account_mode == "service_account":
        print(
            "Provisioning a new child service account on approve "
            f"(name={sa_name or 'raven-cli'})."
        )
    print()
    print(f"  Confirmation code: {expected_user_code}")
    print()
    print(
        "  Verify this code matches what your browser shows on the consent "
        "page. If it does not match, click Cancel — someone may be trying "
        "to trick you into approving their CLI session."
    )
    print()
    print(f"Opening browser to: {consent_url}")
    print("(If your browser doesn't open, paste the URL above into one manually.)")
    print(f"Listening on {callback_url} for the API key…")
    del code_verifier  # only the challenge is needed beyond this point

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        webbrowser.open(consent_url, new=1, autoraise=True)
    except Exception:
        pass

    completed = delivered.wait(timeout=LOGIN_LISTENER_TIMEOUT_SECONDS)
    server.shutdown()
    server.server_close()

    if not completed:
        raise SystemExit(
            f"Login timed out after {LOGIN_LISTENER_TIMEOUT_SECONDS}s. "
            "Re-run `login` and approve in the browser."
        )

    payload = _LoginCallbackHandler.payload or {}
    api_key = payload.get("api_key")
    user = payload.get("user") or {}
    delivered_mode = payload.get("account_mode") or account_mode
    service_account = payload.get("service_account") or None
    delivered_challenge = payload.get("code_challenge")
    if not isinstance(api_key, str) or not api_key.startswith("rvn_"):
        raise SystemExit(f"Did not receive a valid API key: {payload!r}")
    if not isinstance(delivered_challenge, str) or not secrets.compare_digest(
        delivered_challenge, code_challenge
    ):
        # A racing local process could POST `{api_key: "rvn_attacker"}` to the
        # listener before the legitimate consent page does. Without the PKCE
        # echo the CLI cannot tell them apart; with it, any payload that
        # doesn't carry our local challenge is rejected.
        raise SystemExit(
            "Refusing payload: missing or mismatched PKCE challenge. The "
            "credential delivered to the local listener was not from the "
            "consent flow this CLI started."
        )

    cfg = load_config()
    cfg.update(
        {
            "api_url": args.api_url,
            "api_key": api_key,
            "user": user,
            "account_mode": delivered_mode,
            "service_account": service_account,
            "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    saved_to = save_config(cfg)
    if delivered_mode == "service_account":
        sa_label = (
            (service_account or {}).get("name")
            or (service_account or {}).get("email")
            or "service account"
        )
        owner_label = user.get("email") or user.get("id") or "?"
        print(
            f"Provisioned child service account '{sa_label}' under {owner_label}. "
            f"API key saved to {saved_to}"
        )
    else:
        print(
            f"Logged in as {user.get('email') or user.get('id') or '?'}. "
            f"API key saved to {saved_to}"
        )
    return 0


def cmd_logout(client: RavnClient, args: argparse.Namespace) -> int:
    path = config_path()
    if not path.is_file():
        print("Already logged out (no config file).")
        return 0
    try:
        path.unlink()
    except OSError as exc:
        raise SystemExit(f"Could not remove config file {path}: {exc}") from exc
    print(f"Removed {path}. Revoke the key in Settings → API Keys to fully invalidate it.")
    return 0


def cmd_self_test(client: RavnClient, args: argparse.Namespace) -> int:
    """Verify the parser wires every command correctly without hitting the network."""
    parser = build_parser()
    samples = [
        ["whoami"],
        ["login"],
        ["logout"],
        ["docs", "guide"],
        ["docs", "prompt"],
        ["docs", "signals"],
        ["strategies", "list"],
        ["strategies", "get", "X"],
        ["strategies", "create", "--json", "f.json"],
        ["strategies", "update", "X", "--json", "f.json"],
        ["strategies", "rename", "X", "--name", "Y"],
        ["strategies", "delete", "X"],
        ["strategies", "validate", "--json", "f.json"],
        ["bots", "list"],
        ["bots", "get", "X"],
        ["bots", "deploy", "--strategy", "X"],
        ["bots", "start", "X"],
        ["bots", "stop", "X"],
        ["bots", "redeploy", "X"],
        ["bots", "delete", "X", "--confirm"],
        ["bots", "rename", "X", "--name", "Y"],
        ["bots", "logs", "X"],
        ["bots", "events", "X"],
        ["bots", "bootstrap", "X"],
        ["bots", "sessions", "X"],
        ["sessions", "list"],
        ["sessions", "get", "X"],
        ["sessions", "trades", "X"],
        ["sessions", "orders", "X"],
        ["sessions", "equity", "X"],
        ["sessions", "logs", "X"],
        ["simulate", "--strategy", "X"],
        ["backtest", "--strategy", "X", "--recording", "Y"],
        ["recordings", "list"],
        ["recordings", "start", "--markets", "m1,m2"],
        ["recordings", "stop", "X"],
        ["recordings", "delete", "X", "--confirm"],
    ]
    for argv in samples:
        ns = parser.parse_args(argv)
        if not getattr(ns, "func", None):
            raise SystemExit(f"self-test failed: {argv} did not bind a handler")
    print(f"raven_cli self-test: {len(samples)} commands wired correctly")
    return 0


# -------- argparse setup --------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="raven_cli",
        description="Drive a Ravn trading platform instance via the REST API.",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            "Ravn API base URL. Resolution order: --api-url > $RAVN_API_URL "
            f"> ~/.config/ravn/config.json > {DEFAULT_API_URL}."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("RAVN_API_KEY"),
        help="Bearer API key (default: $RAVN_API_KEY).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of the human-readable summary.",
    )

    sub = parser.add_subparsers(dest="group", required=True)

    sub.add_parser("whoami", help="Show authenticated user.").set_defaults(func=cmd_whoami)
    login_p = sub.add_parser(
        "login",
        help="Browser-based sign-in. Stores API key in ~/.config/ravn/config.json.",
    )
    login_p.add_argument(
        "--as-service-account",
        action="store_true",
        help=(
            "Provision a new child service account owned by the approving "
            "human and use it for this CLI session, instead of issuing a key "
            "on the human's own account. Requires the owning account's plan "
            "to have API keys enabled."
        ),
    )
    login_p.add_argument(
        "--service-account-name",
        default=None,
        help=(
            "Display name for the new child service account. Only used when "
            "--as-service-account is passed. Defaults to 'raven-cli'."
        ),
    )
    login_p.set_defaults(func=cmd_login)
    sub.add_parser(
        "logout",
        help="Remove the locally stored API key.",
    ).set_defaults(func=cmd_logout)
    sub.add_parser("self-test", help="Verify CLI parser wiring.").set_defaults(func=cmd_self_test)

    docs = sub.add_parser("docs", help="Fetch agent-facing docs from the server.")
    docs_sub = docs.add_subparsers(dest="action", required=True)
    docs_sub.add_parser("guide").set_defaults(func=cmd_docs_guide)
    docs_sub.add_parser("prompt").set_defaults(func=cmd_docs_prompt)
    docs_sub.add_parser("signals").set_defaults(func=cmd_docs_signals)

    strat = sub.add_parser("strategies", help="Strategy CRUD.")
    strat_sub = strat.add_subparsers(dest="action", required=True)
    strat_sub.add_parser("list").set_defaults(func=cmd_strategies_list)
    p = strat_sub.add_parser("get")
    p.add_argument("id")
    p.set_defaults(func=cmd_strategies_get)
    p = strat_sub.add_parser("create")
    p.add_argument("--json", dest="json_file", required=True, help="JSON file path or '-' for stdin.")
    p.add_argument("--name")
    p.add_argument("--description")
    p.set_defaults(func=cmd_strategies_create)
    p = strat_sub.add_parser("update")
    p.add_argument("id")
    p.add_argument("--json", dest="json_file", required=True)
    p.set_defaults(func=cmd_strategies_update)
    p = strat_sub.add_parser("rename")
    p.add_argument("id")
    p.add_argument("--name")
    p.add_argument("--description")
    p.set_defaults(func=cmd_strategies_rename)
    p = strat_sub.add_parser("delete")
    p.add_argument("id")
    p.set_defaults(func=cmd_strategies_delete)
    p = strat_sub.add_parser("validate")
    p.add_argument("--json", dest="json_file", required=True)
    p.set_defaults(func=cmd_strategies_validate)

    bots = sub.add_parser("bots", help="Bot lifecycle and inspection.")
    bots_sub = bots.add_subparsers(dest="action", required=True)
    bots_sub.add_parser("list").set_defaults(func=cmd_bots_list)
    p = bots_sub.add_parser("get")
    p.add_argument("id")
    p.set_defaults(func=cmd_bots_get)
    p = bots_sub.add_parser("deploy")
    p.add_argument("--strategy", help="Strategy id or public_id.")
    p.add_argument("--version", help="Strategy version id.")
    p.add_argument("--mode", choices=("simulated", "live"), default="simulated")
    p.add_argument("--allow-live", action="store_true", help="Required when --mode live.")
    p.add_argument("--name")
    p.add_argument("--season", dest="season")
    p.add_argument("--enable-recording", action="store_true")
    p.add_argument("--initial-balance", type=float)
    p.add_argument("--fill-rate", type=float)
    p.add_argument("--slippage", type=float)
    p.set_defaults(func=cmd_bots_deploy)
    p = bots_sub.add_parser("start")
    p.add_argument("id")
    p.set_defaults(func=cmd_bots_start)
    p = bots_sub.add_parser("stop")
    p.add_argument("id")
    p.set_defaults(func=cmd_bots_stop)
    p = bots_sub.add_parser("redeploy")
    p.add_argument("id")
    p.set_defaults(func=cmd_bots_redeploy)
    p = bots_sub.add_parser("delete")
    p.add_argument("id")
    p.add_argument("--confirm", action="store_true", help="Required.")
    p.set_defaults(func=cmd_bots_delete)
    p = bots_sub.add_parser("rename")
    p.add_argument("id")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_bots_rename)
    p = bots_sub.add_parser("logs")
    p.add_argument("id")
    p.add_argument("--tail", type=int, default=200)
    p.set_defaults(func=cmd_bots_logs)
    p = bots_sub.add_parser("events")
    p.add_argument("id")
    p.add_argument("--hours", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--show", type=int, default=20, help="How many events to print in summary mode.")
    p.set_defaults(func=cmd_bots_events)
    p = bots_sub.add_parser("bootstrap")
    p.add_argument("id")
    p.add_argument("--hours", type=float, default=1.0)
    p.set_defaults(func=cmd_bots_bootstrap)
    p = bots_sub.add_parser("sessions")
    p.add_argument("id")
    p.set_defaults(func=cmd_bots_sessions)

    sess = sub.add_parser("sessions", help="Trading session inspection.")
    sess_sub = sess.add_subparsers(dest="action", required=True)
    p = sess_sub.add_parser("list")
    p.add_argument("--bot", help="Filter by bot id.")
    p.add_argument("--type", choices=("live", "simulation", "backtest"))
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=25)
    p.set_defaults(func=cmd_sessions_list)
    p = sess_sub.add_parser("get")
    p.add_argument("id")
    p.set_defaults(func=cmd_sessions_get)
    p = sess_sub.add_parser("stop")
    p.add_argument("id")
    p.set_defaults(func=cmd_sessions_stop)
    p = sess_sub.add_parser("delete")
    p.add_argument("id")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_sessions_delete)
    p = sess_sub.add_parser("trades")
    p.add_argument("id")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=50)
    p.set_defaults(func=cmd_sessions_trades)
    p = sess_sub.add_parser("orders")
    p.add_argument("id")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--page-size", type=int, default=50)
    p.add_argument("--status")
    p.set_defaults(func=cmd_sessions_orders)
    p = sess_sub.add_parser("equity")
    p.add_argument("id")
    p.set_defaults(func=cmd_sessions_equity)
    p = sess_sub.add_parser("logs")
    p.add_argument("id")
    p.add_argument("--tail", type=int, default=500)
    p.set_defaults(func=cmd_sessions_logs)

    p = sub.add_parser("simulate", help="Start a real-time simulation session.")
    p.add_argument("--strategy", required=True)
    p.add_argument("--initial-balance", type=float)
    p.add_argument("--fill-rate", type=float)
    p.add_argument("--slippage", type=float)
    p.add_argument("--duration-sec", type=int)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("backtest", help="Run a backtest against a recording or past session.")
    p.add_argument("--strategy", required=True)
    p.add_argument("--recording", help="Recording id (mutually exclusive with --source-session).")
    p.add_argument("--source-session", help="Reuse a past session's market data.")
    p.add_argument("--initial-balance", type=float)
    p.add_argument("--fill-rate", type=float)
    p.add_argument("--slippage", type=float)
    p.add_argument("--speed", type=float, dest="speed")
    p.add_argument("--trials", type=int)
    p.add_argument("--fill-rate-stddev", type=float)
    p.add_argument("--slippage-stddev", type=float)
    p.add_argument("--seed", type=int)
    p.add_argument("--max-workers", type=int)
    p.set_defaults(func=cmd_backtest)

    rec = sub.add_parser("recordings", help="Market data recordings (sources for backtests).")
    rec_sub = rec.add_subparsers(dest="action", required=True)
    rec_sub.add_parser("list").set_defaults(func=cmd_recordings_list)
    p = rec_sub.add_parser("start")
    p.add_argument("--markets", required=True, help="Comma-separated market ids.")
    p.add_argument("--duration-sec", type=int)
    p.set_defaults(func=cmd_recordings_start)
    p = rec_sub.add_parser("stop")
    p.add_argument("id")
    p.set_defaults(func=cmd_recordings_stop)
    p = rec_sub.add_parser("delete")
    p.add_argument("id")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(func=cmd_recordings_delete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.api_url = resolve_api_url(args.api_url)
    args.api_key = resolve_api_key(args.api_key)
    client = RavnClient(args.api_url, args.api_key)
    return args.func(client, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
