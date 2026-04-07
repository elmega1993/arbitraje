#!/usr/bin/env python3
import asyncio
import time
import logging
from dataclasses import asdict
from typing import Any, Dict

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from funding_arb_bot import (
    Bot,
    CONFIG_PATH,
    command_paper_open,
    command_paper_close,
    fetch_recent_error_logs,
    http_get_json,
    http_post_json,
    load_config,
    load_env,
    load_state,
    log_structured_event,
    new_run_id,
    HL_API,
)

logging.basicConfig(
    filename="bot_app.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8790

cfg = load_config()
env = load_env()
bot = Bot(cfg, env)

app = FastAPI(title="Funding Arb Unified Server", version="0.2.0")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled API error processing {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _timed_check(label: str, fn) -> Dict[str, Any]:
    started = time.time()
    try:
        result = fn()
        elapsed_ms = (time.time() - started) * 1000.0
        return {"label": label, "ok": True, "latency_ms": elapsed_ms, "detail": result}
    except Exception as exc:
        elapsed_ms = (time.time() - started) * 1000.0
        return {"label": label, "ok": False, "latency_ms": elapsed_ms, "detail": str(exc)}


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "service": "funding-arb-server"}


@app.get("/api/assets")
async def assets() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, bot.common_assets)
    return {"assets": result}


@app.get("/api/inspect/{symbol}")
async def inspect(symbol: str, hours: int = 168) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, bot.inspect_payload, symbol, hours)
    except SystemExit as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/scan")
async def scan(hours: int = 168, top: int = 40) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(None, bot.scan, hours, top)
        return {"results": [asdict(sig) for sig in results], "errors": bot.last_scan_errors}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/status")
async def status() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    try:
        positions = await loop.run_in_executor(None, bot.get_enriched_status)
        return {"positions": positions}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/errors")
async def recent_errors(limit: int = 20) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    try:
        rows = await loop.run_in_executor(None, fetch_recent_error_logs, limit)
        return {"errors": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class PaperOpenRequest(BaseModel):
    symbol: str
    hours: int = 168
    notional: float = 1000.0
    force: bool = False

@app.post("/api/paper/open")
async def paper_open(req: PaperOpenRequest) -> Dict[str, Any]:
    import argparse
    started_at = time.time()
    run_id = new_run_id()
    args = argparse.Namespace(symbol=req.symbol, hours=req.hours, notional=req.notional, force=req.force)
    args.run_id = run_id
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, command_paper_open, args, bot, cfg)
        return {"ok": True, "message": f"Oppened paper position for {req.symbol}"}
    except SystemExit as exc:
        await loop.run_in_executor(
            None,
            lambda: log_structured_event(
                "paper_open",
                run_id=run_id,
                status="error",
                started_at=started_at,
                event_input={"symbol": req.symbol, "hours": req.hours, "notional": req.notional, "force": req.force},
                error=str(exc),
                meta={"via": "api"},
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc))

class PaperCloseRequest(BaseModel):
    symbol: str
    hours: int = 168

@app.post("/api/paper/close")
async def paper_close(req: PaperCloseRequest) -> Dict[str, Any]:
    import argparse
    started_at = time.time()
    run_id = new_run_id()
    args = argparse.Namespace(symbol=req.symbol, hours=req.hours)
    args.run_id = run_id
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, command_paper_close, args, bot)
        return {"ok": True, "message": f"Closed paper position for {req.symbol}"}
    except SystemExit as exc:
        await loop.run_in_executor(
            None,
            lambda: log_structured_event(
                "paper_close",
                run_id=run_id,
                status="error",
                started_at=started_at,
                event_input={"symbol": req.symbol, "hours": req.hours},
                error=str(exc),
                meta={"via": "api"},
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc))

class PrepareTradeRequest(BaseModel):
    symbol: str
    hours: int = 24
    notional: float = 1000.0

@app.post("/api/prepare-trade")
async def prepare_trade(req: PrepareTradeRequest) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    started_at = time.time()
    run_id = new_run_id()
    try:
        res = await loop.run_in_executor(None, bot.prepare_trade, req.symbol, req.hours, req.notional, run_id)
        await loop.run_in_executor(
            None,
            lambda: log_structured_event(
                "prepare_trade",
                run_id=run_id,
                status="ok",
                started_at=started_at,
                event_input={"symbol": req.symbol, "hours": req.hours, "notional": req.notional},
                result=res,
                meta={"via": "api"},
            ),
        )
        return {"ok": True, "data": res}
    except SystemExit as exc:
        await loop.run_in_executor(
            None,
            lambda: log_structured_event(
                "prepare_trade_rejected",
                run_id=run_id,
                status="error",
                started_at=started_at,
                event_input={"symbol": req.symbol, "hours": req.hours, "notional": req.notional},
                error=str(exc),
                meta={"via": "api"},
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc))

class ExecuteTradeRequest(BaseModel):
    plan_id: str
    confirm: bool = False

@app.post("/api/execute-trade")
async def execute_trade(req: ExecuteTradeRequest) -> Dict[str, Any]:
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Must confirm with confirm=True")
    loop = asyncio.get_event_loop()
    started_at = time.time()
    run_id = new_run_id()
    try:
        res = await loop.run_in_executor(None, bot.execute_trade, req.plan_id, run_id)
        await loop.run_in_executor(
            None,
            lambda: log_structured_event(
                "execute_trade",
                run_id=run_id,
                status="ok" if res.get("status") == "ok" else "degraded",
                started_at=started_at,
                event_input={"plan_id": req.plan_id, "confirm": req.confirm},
                result=res,
                warnings=[] if res.get("status") == "ok" else [f"execute_trade_status_{res.get('status')}"],
                meta={"via": "api"},
            ),
        )
        return {"ok": True, "data": res}
    except SystemExit as exc:
        await loop.run_in_executor(
            None,
            lambda: log_structured_event(
                "execute_trade_rejected",
                run_id=run_id,
                status="error",
                started_at=started_at,
                event_input={"plan_id": req.plan_id, "confirm": req.confirm},
                error=str(exc),
                meta={"via": "api"},
            ),
        )
        raise HTTPException(status_code=400, detail=str(exc))

class KillSwitchRequest(BaseModel):
    active: bool

@app.get("/api/kill-switch")
async def get_kill_switch() -> Dict[str, Any]:
    active = cfg.get("risk", {}).get("kill_switch_active", False)
    return {"active": active}

@app.post("/api/kill-switch")
async def set_kill_switch(req: KillSwitchRequest) -> Dict[str, Any]:
    import json
    cfg.setdefault("risk", {})["kill_switch_active"] = req.active
    # Persist relative to the project root, not the shell cwd.
    try:
        with CONFIG_PATH.open("w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "active": req.active}

@app.get("/api/balances")
async def balances() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()

    def _fetch_balances() -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "hl_equity": 0.0,
            "hl_available": 0.0,
            "hl_spot_usdc": 0.0,
            "hl_accounts": [],
            "lt_equity": None,
            "total": None,
        }

        hl_equity = 0.0
        hl_available = 0.0
        hl_spot_usdc = 0.0

        # Expose per-address detail instead of silently picking the maximum.
        addresses_to_check = [
            env.get("HYPERLIQUID_ACCOUNT_ADDRESS", ""),
            env.get("LIGHTER_WALLET_ADDRESS", ""),
            env.get("HYPERLIQUID_AGENT_WALLET_ADDRESS", ""),
        ]
        seen = set()

        for addr in addresses_to_check:
            if not addr or addr in seen:
                continue
            seen.add(addr)
            try:
                state = http_post_json(HL_API, {"type": "clearinghouseState", "user": addr})
                margin = state.get("marginSummary", {})
                perps_val = float(margin.get("accountValue", 0) or 0.0)
                withdrawable = float(state.get("withdrawable", 0) or 0.0)

                spot_state = http_post_json(HL_API, {"type": "spotClearinghouseState", "user": addr})
                spot_val = 0.0
                for bal in spot_state.get("balances", []):
                    if bal.get("coin") == "USDC":
                        spot_val += float(bal.get("total", 0) or 0.0)

                total_val = perps_val + spot_val
                hl_equity += total_val
                hl_available += withdrawable
                hl_spot_usdc += spot_val
                result["hl_accounts"].append(
                    {
                        "address": addr,
                        "perps_equity": perps_val,
                        "spot_usdc": spot_val,
                        "withdrawable": withdrawable,
                        "total_equity": total_val,
                    }
                )
            except Exception:
                result["hl_accounts"].append({"address": addr, "error": "fetch_failed"})

        result["hl_equity"] = hl_equity
        result["hl_available"] = hl_available
        result["hl_spot_usdc"] = hl_spot_usdc
        # Lighter balance
        try:
            lt_resp = http_get_json("http://127.0.0.1:8787/api/private/lighter")
            lt_equity = 0.0
            if lt_resp and "account" in lt_resp:
                accounts = lt_resp["account"].get("accounts", [])
                for acc in accounts:
                    lt_equity += float(acc.get("total_asset_value", 0))
            result["lt_equity"] = lt_equity
        except Exception:
            result["lt_equity"] = None

        # Total
        result["total"] = hl_equity + (result["lt_equity"] or 0)
        return result

    return await loop.run_in_executor(None, _fetch_balances)

@app.get("/api/doctor")
async def doctor() -> Dict[str, Any]:
    loop = asyncio.get_event_loop()

    def _run_doctor() -> Dict[str, Any]:
        checks = []
        # Env check
        checks.append(
            _timed_check(
                "env",
                lambda: {
                    "hyperliquid_ready": all(
                        [
                            bool(env.get("HYPERLIQUID_ACCOUNT_ADDRESS")),
                            bool(env.get("HYPERLIQUID_AGENT_WALLET_ADDRESS")),
                            bool(env.get("HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY")),
                        ]
                    ),
                    "lighter_ready": all(
                        [
                            bool(env.get("LIGHTER_API_PUBLIC_KEY")),
                            bool(env.get("LIGHTER_API_PRIVATE_KEY")),
                            bool(env.get("LIGHTER_ACCOUNT_INDEX")),
                            bool(env.get("LIGHTER_API_KEY_INDEX")),
                        ]
                    ),
                },
            )
        )
        # Public API checks
        checks.append(
            _timed_check(
                "hl_public_meta",
                lambda: {"assets": len(bot.common_assets())},
            )
        )
        checks.append(
            _timed_check(
                "lt_public_markets",
                lambda: {"markets": len(bot.ensure_lt_markets())},
            )
        )
        # HL private state
        account_address = env.get("HYPERLIQUID_ACCOUNT_ADDRESS", "")
        if account_address:
            checks.append(
                _timed_check(
                    "hl_private_user_state",
                    lambda: {
                        "has_state": bool(
                            http_post_json(
                                HL_API,
                                {"type": "clearinghouseState", "user": account_address},
                            )
                        )
                    },
                )
            )
        # Private backend
        checks.append(
            _timed_check(
                "private_backend_health",
                lambda: http_get_json("http://127.0.0.1:8787/health", retries=0, timeout=5.0),
            )
        )
        # State check
        checks.append(
            _timed_check(
                "bot_state",
                lambda: {
                    "common_assets": len(bot.common_assets()),
                    "open_positions": len(load_state().get("positions", [])),
                },
            )
        )
        overall_ok = all(check["ok"] for check in checks)
        return {"ok": overall_ok, "checks": checks}

    return await loop.run_in_executor(None, _run_doctor)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
