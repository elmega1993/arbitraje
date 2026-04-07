#!/usr/bin/env python3
import argparse
import bisect
import concurrent.futures
import hashlib
import json
import math
import os
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import dataclass, asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request
from exchange_adapters import get_adapter


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LEGACY_DIR = DATA_DIR / "legacy"
LOG_DIR = ROOT / "logs"
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_CONFIG_PATH = ROOT / "config.example.json"
STATE_PATH = ROOT / "paper_state.json"
SCAN_CACHE_PATH = ROOT / "scan_cache.json"
LEGACY_STATE_PATH = LEGACY_DIR / "paper_state.legacy.json"
LEGACY_SCAN_CACHE_PATH = LEGACY_DIR / "scan_cache.legacy.json"
DB_PATH = ROOT / "arb_bot.db"
ENV_PATH = ROOT / ".env"
ERROR_LOG_PATH = LOG_DIR / "bot_errors.ndjson"

HL_API = "https://api.hyperliquid.xyz/info"
LT_BASE = "https://mainnet.zklighter.elliot.ai/api/v1"

DISPLAY_ALIAS = {
    "1000PEPE": "PEPE",
    "1000BONK": "BONK",
    "1000SHIB": "SHIB",
    "KPEPE": "PEPE",
    "KSHIB": "SHIB",
    "KBONK": "BONK",
    "KFLOKI": "FLOKI"
}
LT_SYMBOL_ALIAS = {"PEPE": "1000PEPE", "BONK": "1000BONK", "SHIB": "1000SHIB", "FLOKI": "1000FLOKI"}
HL_SYMBOL_ALIAS = {"PEPE": "kPEPE", "SHIB": "kSHIB", "BONK": "kBONK", "FLOKI": "kFLOKI"}
DB_INITIALIZED = False
_DB_INIT_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
CACHE_LOCK = threading.RLock()
ORDERBOOK_LOCK = threading.RLock()


class RequestGate:
    def __init__(self, min_interval_sec: float, max_concurrent: int) -> None:
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self._semaphore = threading.BoundedSemaphore(max(1, int(max_concurrent)))
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    def acquire(self) -> None:
        self._semaphore.acquire()
        wait_sec = 0.0
        with self._lock:
            now = time.monotonic()
            wait_sec = self._next_allowed_at - now
            if wait_sec > 0:
                self._next_allowed_at += self.min_interval_sec
            else:
                self._next_allowed_at = now + self.min_interval_sec
        if wait_sec > 0:
            time.sleep(wait_sec)

    def release(self) -> None:
        self._semaphore.release()


REQUEST_GATES = {
    "hyperliquid": RequestGate(min_interval_sec=0.20, max_concurrent=2),
    "lighter": RequestGate(min_interval_sec=0.75, max_concurrent=1),
    "default": RequestGate(min_interval_sec=0.0, max_concurrent=4),
}


def normalize_symbol(raw: Any) -> str:
    sym = str(raw or "").strip().upper().replace(" ", "")
    if not sym:
        return ""
    changed = True
    while changed and sym:
        changed = False
        for suffix in ("/USDC", "/USDT", "-USDC", "-USDT", "-USD", "-PERP", "USDC", "USDT", "USD", "PERP"):
            if sym.endswith(suffix):
                sym = sym[: -len(suffix)]
                changed = True
                break
    return DISPLAY_ALIAS.get(sym, sym)


def lt_symbol(sym: str) -> str:
    norm = normalize_symbol(sym)
    return LT_SYMBOL_ALIAS.get(norm, norm)

def hl_symbol(sym: str) -> str:
    norm = normalize_symbol(sym)
    return HL_SYMBOL_ALIAS.get(norm, norm)

def should_retry(exc: Exception) -> bool:
    if isinstance(exc, error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (error.URLError, TimeoutError))


def is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, error.HTTPError) and exc.code == 429


def classify_request_target(url: str) -> str:
    if "hyperliquid.xyz" in url:
        return "hyperliquid"
    if "zklighter" in url or "elliot.ai" in url:
        return "lighter"
    return "default"


def retry_sleep_seconds(exc: Exception, attempt: int) -> float:
    if isinstance(exc, error.HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 20.0)
            except ValueError:
                pass
    base = min(0.75 * (2**attempt), 6.0) + 0.15 * attempt
    if is_rate_limited(exc):
        base = max(base, min(2.0 * (2**attempt), 20.0))
    return base


def _http_json(
    url: str,
    method: str,
    payload: Optional[Dict[str, Any]] = None,
    retries: int = 4,
    timeout: float = 20.0,
    *,
    run_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    last_exc: Optional[Exception] = None
    gate = REQUEST_GATES[classify_request_target(url)]
    for attempt in range(retries + 1):
        gate.acquire()
        try:
            if method == "POST":
                data = json.dumps(payload or {}).encode()
                req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
            else:
                req = request.Request(url)
            with request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except Exception as exc:
            last_exc = exc
            is_final = attempt >= retries or not should_retry(exc)
            log_error_detail(
                source=classify_request_target(url),
                operation=f"http_{method.lower()}",
                error_obj=exc,
                target=url,
                retryable=should_retry(exc),
                attempt=attempt + 1,
                run_id=run_id,
                context={
                    "method": method,
                    "retries": retries,
                    "timeout": timeout,
                    "payload": payload,
                    **(context or {}),
                },
                final_attempt=is_final,
            )
            if is_final:
                raise
            time.sleep(retry_sleep_seconds(exc, attempt))
        finally:
            gate.release()
    raise RuntimeError(f"{method} failed unexpectedly: {url}") from last_exc


def http_get_json(
    url: str,
    retries: int = 4,
    timeout: float = 20.0,
    *,
    run_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    return _http_json(url, "GET", retries=retries, timeout=timeout, run_id=run_id, context=context)


def http_post_json(
    url: str,
    payload: Dict[str, Any],
    retries: int = 4,
    timeout: float = 20.0,
    *,
    run_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    return _http_json(url, "POST", payload=payload, retries=retries, timeout=timeout, run_id=run_id, context=context)


def load_config() -> Dict[str, Any]:
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH
    with path.open() as fh:
        return json.load(fh)


def load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            env[key.strip()] = value.strip()
            os.environ.setdefault(key.strip(), value.strip())
    for key in (
        "HYPERLIQUID_ACCOUNT_ADDRESS",
        "HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY",
        "HYPERLIQUID_AGENT_WALLET_ADDRESS",
        "LIGHTER_API_PUBLIC_KEY",
        "LIGHTER_API_PRIVATE_KEY",
        "LIGHTER_ACCOUNT_INDEX",
        "LIGHTER_API_KEY_INDEX",
    ):
        if os.getenv(key):
            env[key] = os.getenv(key, "")
    return env


def load_state() -> Dict[str, Any]:
    ensure_db_initialized()
    with STATE_LOCK:
        conn = get_db()
        rows = conn.execute("SELECT payload FROM positions ORDER BY opened_at ASC, id ASC").fetchall()
        conn.close()
        return {"positions": [json.loads(row[0]) for row in rows]}


def save_state(state: Dict[str, Any]) -> None:
    ensure_db_initialized()
    with STATE_LOCK:
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM positions")
            for pos in state.get("positions", []):
                conn.execute(
                    "INSERT INTO positions(symbol, opened_at, payload) VALUES (?, ?, ?)",
                    (pos.get("symbol", ""), int(pos.get("opened_at", 0) or 0), json.dumps(pos)),
                )
        conn.close()


def load_scan_cache() -> Dict[str, Any]:
    ensure_db_initialized()
    with CACHE_LOCK:
        conn = get_db()
        rows = conn.execute("SELECT bucket, cache_key, payload FROM cache_entries").fetchall()
        conn.close()
        out = {"histories": {}, "orderbooks": {}, "scans": {}}
        for bucket, cache_key, payload in rows:
            out.setdefault(bucket, {})[cache_key] = json.loads(payload)
        return out


def save_scan_cache(cache: Dict[str, Any]) -> None:
    ensure_db_initialized()
    with CACHE_LOCK:
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM cache_entries")
            for bucket, entries in cache.items():
                for cache_key, payload in entries.items():
                    conn.execute(
                        "INSERT INTO cache_entries(bucket, cache_key, payload, updated_at) VALUES (?, ?, ?, ?)",
                        (bucket, cache_key, json.dumps(payload), time.time()),
                    )
        conn.close()


def get_db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def ensure_db_initialized() -> None:
    global DB_INITIALIZED
    if DB_INITIALIZED:
        return
    with _DB_INIT_LOCK:
        if DB_INITIALIZED:
            return
        _do_db_init()
        DB_INITIALIZED = True


def _do_db_init() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            opened_at INTEGER NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_entries (
            bucket TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (bucket, cache_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            ts REAL NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS error_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            source TEXT NOT NULL,
            operation TEXT NOT NULL,
            target TEXT,
            error_type TEXT NOT NULL,
            error_message TEXT NOT NULL,
            status_code INTEGER,
            retryable INTEGER NOT NULL,
            attempt INTEGER,
            run_id TEXT,
            context_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_plans (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            trade TEXT NOT NULL,
            notional_usd REAL NOT NULL,
            plan_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_executions (
            id PRIMARY KEY,
            plan_id TEXT NOT NULL,
            result_json TEXT NOT NULL,
            status TEXT NOT NULL,
            executed_at REAL NOT NULL
        )
        """
    )
    migrate_legacy_json(conn)
    conn.close()


def migrate_legacy_json(conn: sqlite3.Connection) -> None:
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    has_positions = conn.execute("SELECT 1 FROM positions LIMIT 1").fetchone() is not None
    if not has_positions and STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception:
            state = {"positions": []}
        for pos in state.get("positions", []):
            conn.execute(
                "INSERT INTO positions(symbol, opened_at, payload) VALUES (?, ?, ?)",
                (pos.get("symbol", ""), int(pos.get("opened_at", 0) or 0), json.dumps(pos)),
            )
        try:
            STATE_PATH.replace(LEGACY_STATE_PATH)
        except Exception:
            pass
    has_cache = conn.execute("SELECT 1 FROM cache_entries LIMIT 1").fetchone() is not None
    if not has_cache and SCAN_CACHE_PATH.exists():
        try:
            cache = json.loads(SCAN_CACHE_PATH.read_text())
        except Exception:
            cache = {"histories": {}, "orderbooks": {}, "scans": {}}
        for bucket, entries in cache.items():
            for cache_key, payload in entries.items():
                conn.execute(
                    "INSERT OR REPLACE INTO cache_entries(bucket, cache_key, payload, updated_at) VALUES (?, ?, ?, ?)",
                    (bucket, cache_key, json.dumps(payload), time.time()),
                )
        try:
            SCAN_CACHE_PATH.replace(LEGACY_SCAN_CACHE_PATH)
        except Exception:
            pass
    conn.commit()


def log_event(event_type: str, payload: Dict[str, Any]) -> None:
    log_structured_event(event_type, result=payload)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _normalize_event_payload(raw_payload: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw_payload)
    except Exception:
        return {
            "schema": "event.v0",
            "run_id": None,
            "status": "legacy",
            "duration_ms": None,
            "input": None,
            "result": raw_payload,
            "error": None,
            "warnings": [],
            "meta": {},
        }
    if isinstance(payload, dict) and payload.get("schema") == "event.v1":
        return payload
    return {
        "schema": "event.v0",
        "run_id": None,
        "status": "legacy",
        "duration_ms": None,
        "input": None,
        "result": payload,
        "error": None,
        "warnings": [],
        "meta": {},
    }


def log_structured_event(
    event_type: str,
    *,
    run_id: Optional[str] = None,
    status: str = "ok",
    started_at: Optional[float] = None,
    event_input: Optional[Dict[str, Any]] = None,
    result: Any = None,
    error: Optional[str] = None,
    warnings: Optional[List[str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    ensure_db_initialized()
    now = time.time()
    effective_run_id = run_id or new_run_id()
    payload = {
        "schema": "event.v1",
        "run_id": effective_run_id,
        "status": status,
        "duration_ms": round((now - started_at) * 1000.0, 2) if started_at is not None else None,
        "input": event_input or {},
        "result": result,
        "error": error,
        "warnings": list(warnings or []),
        "meta": meta or {},
    }
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT INTO events(event_type, ts, payload) VALUES (?, ?, ?)",
            (event_type, now, json.dumps(payload)),
        )
    conn.close()
    return effective_run_id


def log_error_detail(
    *,
    source: str,
    operation: str,
    error_obj: Exception,
    target: Optional[str] = None,
    retryable: Optional[bool] = None,
    attempt: Optional[int] = None,
    run_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    final_attempt: bool = True,
) -> None:
    now = time.time()
    status_code = error_obj.code if isinstance(error_obj, error.HTTPError) else None
    payload = {
        "ts": now,
        "source": source,
        "operation": operation,
        "target": target,
        "error_type": type(error_obj).__name__,
        "error_message": str(error_obj),
        "status_code": status_code,
        "retryable": bool(should_retry(error_obj) if retryable is None else retryable),
        "attempt": attempt,
        "run_id": run_id,
        "context": context or {},
    }
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    if not final_attempt:
        return

    ensure_db_initialized()
    conn = get_db()
    with conn:
        conn.execute(
            """
            INSERT INTO error_logs(
                ts, source, operation, target, error_type, error_message, status_code,
                retryable, attempt, run_id, context_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                source,
                operation,
                target,
                payload["error_type"],
                payload["error_message"],
                status_code,
                1 if payload["retryable"] else 0,
                attempt,
                run_id,
                json.dumps(payload["context"]),
            ),
        )
    conn.close()


def fetch_recent_events(limit: int = 20) -> List[Dict[str, Any]]:
    ensure_db_initialized()
    conn = get_db()
    rows = conn.execute(
        "SELECT id, event_type, ts, payload FROM events ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    out = []
    for row_id, event_type, ts, raw_payload in rows:
        payload = _normalize_event_payload(raw_payload)
        out.append(
            {
                "id": row_id,
                "event_type": event_type,
                "ts": ts,
                "payload": payload,
            }
        )
    return out


def print_recent_events(limit: int = 20) -> None:
    rows = fetch_recent_events(limit)
    if not rows:
        print("No hay eventos registrados.")
        return
    print("id    ts                  event_type              status    dur_ms   warnings run_id")
    for row in rows:
        payload = row["payload"]
        ts_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"]))
        status = str(payload.get("status") or "—")
        dur = payload.get("duration_ms")
        dur_txt = f"{dur:.1f}" if isinstance(dur, (int, float)) else "—"
        warnings_count = len(payload.get("warnings") or [])
        run_id = payload.get("run_id") or f"legacy-{row['id']}"
        print(
            f"{row['id']:<5} {ts_txt:<19} {row['event_type']:<22} "
            f"{status:<9} {dur_txt:>7} {warnings_count:>9} {run_id}"
        )
        if payload.get("error"):
            print(f"  error: {payload['error']}")
        if warnings_count:
            print(f"  warnings: {json.dumps(payload.get('warnings'), ensure_ascii=True)}")


def print_recent_runs(limit: int = 10) -> None:
    rows = fetch_recent_events(max(20, limit * 10))
    if not rows:
        print("No hay corridas registradas.")
        return
    severity_rank = {"error": 3, "degraded": 2, "ok": 1, "legacy": 0}
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        payload = row["payload"]
        run_id = payload.get("run_id") or f"legacy-{row['id']}"
        item = grouped.setdefault(
            run_id,
            {
                "run_id": run_id,
                "last_ts": row["ts"],
                "event_count": 0,
                "events": [],
                "status": payload.get("status") or "legacy",
                "warnings": 0,
            },
        )
        item["last_ts"] = max(item["last_ts"], row["ts"])
        item["event_count"] += 1
        item["events"].append(row["event_type"])
        item["warnings"] += len(payload.get("warnings") or [])
        current_rank = severity_rank.get(item["status"], 0)
        new_rank = severity_rank.get(payload.get("status") or "legacy", 0)
        if new_rank > current_rank:
            item["status"] = payload.get("status") or "legacy"
    ordered = sorted(grouped.values(), key=lambda item: item["last_ts"], reverse=True)[: max(1, int(limit))]
    print("run_id         last_ts              status    events warnings event_types")
    for item in ordered:
        ts_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["last_ts"]))
        event_types = ",".join(item["events"][:4])
        print(
            f"{item['run_id']:<14} {ts_txt:<19} {item['status']:<9} "
            f"{item['event_count']:>6} {item['warnings']:>8} {event_types}"
        )


def fetch_recent_error_logs(limit: int = 20) -> List[Dict[str, Any]]:
    ensure_db_initialized()
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, ts, source, operation, target, error_type, error_message,
               status_code, retryable, attempt, run_id, context_json
        FROM error_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        context = {}
        if row[11]:
            try:
                context = json.loads(row[11])
            except Exception:
                context = {"raw": row[11]}
        out.append(
            {
                "id": row[0],
                "ts": row[1],
                "source": row[2],
                "operation": row[3],
                "target": row[4],
                "error_type": row[5],
                "error_message": row[6],
                "status_code": row[7],
                "retryable": bool(row[8]),
                "attempt": row[9],
                "run_id": row[10],
                "context": context,
            }
        )
    return out


def print_recent_error_logs(limit: int = 20) -> None:
    rows = fetch_recent_error_logs(limit)
    if not rows:
        print("No hay errores detallados registrados.")
        return
    print("id    ts                  source       op               code retry att run_id         error")
    for row in rows:
        ts_txt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"]))
        code = row["status_code"] if row["status_code"] is not None else "—"
        retry = "yes" if row["retryable"] else "no"
        att = row["attempt"] if row["attempt"] is not None else "—"
        run_id = row["run_id"] or "—"
        print(
            f"{row['id']:<5} {ts_txt:<19} {row['source']:<12} {row['operation']:<16} "
            f"{str(code):<4} {retry:<5} {str(att):<3} {run_id:<14} {row['error_type']}: {row['error_message']}"
        )
        if row["target"]:
            print(f"  target: {row['target']}")
        if row["context"]:
            print(f"  context: {json.dumps(row['context'], ensure_ascii=False)}")


def annualized_pct(hourly_rate: float) -> float:
    return hourly_rate * 8760 * 100


def pct_to_bps(x: float) -> float:
    return x * 10000


def fmt_pct(x: Optional[float], digits: int = 2) -> str:
    if x is None or math.isnan(x):
        return "—"
    return f"{x:+.{digits}f}%"


def fmt_bps(x: Optional[float], digits: int = 2) -> str:
    if x is None or math.isnan(x):
        return "—"
    return f"{x:+.{digits}f} bps"


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def estimated_roundtrip_cost_bps(costs: Dict[str, Any], execution_cfg: Dict[str, Any]) -> float:
    return (
        float(costs["hyperliquid_entry_bps"])
        + float(costs["hyperliquid_exit_bps"])
        + float(costs["lighter_entry_bps"])
        + float(costs["lighter_exit_bps"])
        + 2 * float(costs["estimated_exit_slippage_per_leg_bps"])
        + float(execution_cfg.get("entry_buffer_bps", 0.0))
    )


def trade_for_spread(spread: float) -> str:
    if spread > 0:
        return "Long Lighter / Short HL"
    if spread < 0:
        return "Long HL / Short Lighter"
    return "Neutral"


def direction_label(spread: float) -> str:
    if spread > 0:
        return "HL pays longs more often"
    if spread < 0:
        return "Lighter pays longs more often"
    return "Neutral"


@dataclass
class Signal:
    symbol: str
    samples: int
    current_hl_rate: Optional[float]
    current_lt_rate: Optional[float]
    current_spread: Optional[float]
    avg_spread: float
    avg_abs_spread: float
    gross_apy: float
    signed_apy: float
    consistency_pct: float
    longest_streak_hours: int
    max_drawdown_bps: float
    trade: str
    long_venue: str
    short_venue: str
    expected_cost_pct_hold: float
    expected_gross_pct_hold: float
    expected_net_pct_hold: float
    net_est_apy: float
    passes: bool
    break_even_hours: Optional[float] = None
    hl_entry_slippage_bps: Optional[float] = None
    lt_entry_slippage_bps: Optional[float] = None
    total_cost_bps: Optional[float] = None
    liquidity_ok: Optional[bool] = None
    hl_liquidity_ok: Optional[bool] = None
    lt_liquidity_ok: Optional[bool] = None
    hl_liquidity_fail: Optional[str] = None
    lt_liquidity_fail: Optional[str] = None
    used_fresh_books: Optional[bool] = None


class MarketData:
    def __init__(self, cache: Optional[Dict[str, Any]] = None, cfg: Optional[Dict[str, Any]] = None) -> None:
        self._orderbook_cache: Dict[tuple, Dict[str, Any]] = {}
        self.cache = cache if cache is not None else {"histories": {}, "orderbooks": {}, "scans": {}}
        self.cfg = cfg if cfg is not None else {}
        # Uses module-level CACHE_LOCK for thread safety (shared with save_scan_cache)

    def _history_ttl(self) -> int:
        return int(self.cfg.get("scan", {}).get("history_cache_ttl_sec", 300))

    def _orderbook_ttl(self) -> int:
        return int(self.cfg.get("scan", {}).get("orderbook_cache_ttl_sec", 15))

    def _stale_history_ttl(self) -> int:
        return int(self.cfg.get("scan", {}).get("history_stale_if_error_ttl_sec", 21600))

    def _stale_orderbook_ttl(self) -> int:
        return int(self.cfg.get("scan", {}).get("orderbook_stale_if_error_ttl_sec", 180))

    def _cached_entry(self, bucket: str, key: str) -> Optional[Dict[str, Any]]:
        with CACHE_LOCK:
            return self.cache.get(bucket, {}).get(key)

    def _cached_value(self, bucket: str, key: str, ttl: int) -> Optional[Any]:
        entry = self._cached_entry(bucket, key)
        if not entry:
            return None
        if time.time() - float(entry.get("ts", 0)) > ttl:
            return None
        return entry.get("data")

    def _stale_value(self, bucket: str, key: str, max_age_sec: int) -> Optional[Any]:
        entry = self._cached_entry(bucket, key)
        if not entry:
            return None
        if time.time() - float(entry.get("ts", 0)) > max_age_sec:
            return None
        return entry.get("data")

    def _store_cache(self, bucket: str, key: str, value: Any) -> None:
        with CACHE_LOCK:
            self.cache.setdefault(bucket, {})[key] = {"ts": time.time(), "data": value}

    def fetch_hl_universe(self) -> List[str]:
        data = http_post_json(HL_API, {"type": "meta"})
        return sorted({normalize_symbol(x.get("name")) for x in data.get("universe", []) if x.get("name")})

    def fetch_lt_markets(self) -> Dict[str, int]:
        data = http_get_json(f"{LT_BASE}/orderBooks")
        out = {}
        for item in data.get("order_books", []):
            sym = normalize_symbol(item.get("symbol"))
            if sym:
                out[sym] = int(item["market_id"])
        return out

    def fetch_lt_current(self) -> Dict[str, float]:
        data = http_get_json(f"{LT_BASE}/funding-rates")
        out = {}
        for row in data.get("funding_rates", []):
            if row.get("exchange") != "lighter":
                continue
            out[normalize_symbol(row.get("symbol"))] = float(row["rate"])
        return out

    def fetch_hl_history(self, symbol: str, hours: int) -> List[Dict[str, float]]:
        return self.fetch_hl_history_cached(symbol, hours)

    def fetch_hl_history_cached(self, symbol: str, hours: int) -> List[Dict[str, float]]:
        cache_key = f"hl:{normalize_symbol(symbol)}:{hours}"
        cached = self._cached_value("histories", cache_key, self._history_ttl())
        if cached is not None:
            return cached
        try:
            return self.fetch_hl_history_fresh(symbol, hours, store_cache=True)
        except Exception as exc:
            if should_retry(exc):
                stale = self._stale_value("histories", cache_key, self._stale_history_ttl())
                if stale is not None:
                    return stale
            raise

    def fetch_hl_history_fresh(self, symbol: str, hours: int, store_cache: bool = False) -> List[Dict[str, float]]:
        start_ms = int((time.time() - hours * 3600) * 1000)
        data = http_post_json(HL_API, {"type": "fundingHistory", "coin": hl_symbol(symbol), "startTime": start_ms})
        out = [{"t": int(row["time"]), "v": float(row["fundingRate"])} for row in data]
        if store_cache:
            cache_key = f"hl:{normalize_symbol(symbol)}:{hours}"
            self._store_cache("histories", cache_key, out)
        return out

    def fetch_lt_history(self, market_id: int, hours: int) -> List[Dict[str, float]]:
        return self.fetch_lt_history_cached(market_id, hours)

    def fetch_lt_history_cached(self, market_id: int, hours: int) -> List[Dict[str, float]]:
        cache_key = f"lt:{market_id}:{hours}"
        cached = self._cached_value("histories", cache_key, self._history_ttl())
        if cached is not None:
            return cached
        try:
            return self.fetch_lt_history_fresh(market_id, hours, store_cache=True)
        except Exception as exc:
            if should_retry(exc):
                stale = self._stale_value("histories", cache_key, self._stale_history_ttl())
                if stale is not None:
                    return stale
            raise

    def fetch_lt_history_fresh(self, market_id: int, hours: int, store_cache: bool = False) -> List[Dict[str, float]]:
        end_ts_ms = int(time.time() * 1000)
        out = []
        window_hours = hours
        while window_hours > 0:
            batch_hours = min(window_hours, 720)
            batch_end = end_ts_ms - (hours - window_hours) * 3600 * 1000
            batch_start = batch_end - batch_hours * 3600 * 1000
            params = parse.urlencode(
                {
                    "market_id": market_id,
                    "resolution": "1h",
                    "start_timestamp": batch_start,
                    "end_timestamp": batch_end,
                    "count_back": batch_hours,
                }
            )
            data = http_get_json(f"{LT_BASE}/fundings?{params}")
            for row in data.get("fundings", []):
                raw_rate = float(row.get("funding_rate") or row.get("rate") or row.get("fundingRate") or 0.0)
                direction = str(row.get("direction", "")).lower()
                # VERIFIED (O2): direction="short" → longs pay (sign=-1). Cross-checked
                #   against HL over 168h for TRUMP(97%), XMR(100%), STABLE(89%), BERA(83%).
                #   Mismatches in SOL/ENA are due to independent rate dynamics, not sign inversion.
                sign = -1 if direction == "short" else 1
                out.append({"t": int(row.get("timestamp", 0)) * 1000, "v": (raw_rate / 100.0) * sign})
            window_hours -= batch_hours
        dedup = {row["t"]: row for row in out if row["t"]}
        final = [dedup[t] for t in sorted(dedup)]
        if store_cache:
            cache_key = f"lt:{market_id}:{hours}"
            self._store_cache("histories", cache_key, final)
        return final

    def fetch_hl_orderbook(self, symbol: str) -> Dict[str, Any]:
        return self.fetch_hl_orderbook_cached(symbol)

    def fetch_hl_orderbook_cached(self, symbol: str) -> Dict[str, Any]:
        cache_key = ("hl", normalize_symbol(symbol))
        with ORDERBOOK_LOCK:
            cached = self._orderbook_cache.get(cache_key)
        if cached:
            return cached
        disk_key = f"hl:{normalize_symbol(symbol)}"
        disk_cached = self._cached_value("orderbooks", disk_key, self._orderbook_ttl())
        if disk_cached is not None:
            with ORDERBOOK_LOCK:
                self._orderbook_cache[cache_key] = disk_cached
            return disk_cached
        try:
            data = http_post_json(HL_API, {"type": "l2Book", "coin": hl_symbol(symbol)})
        except Exception as exc:
            if should_retry(exc):
                stale = self._stale_value("orderbooks", disk_key, self._stale_orderbook_ttl())
                if stale is not None:
                    self._orderbook_cache[cache_key] = stale
                    return stale
            raise
        levels = data.get("levels", [[], []])
        out = {
            "bids": [{"price": float(row["px"]), "size": float(row["sz"])} for row in levels[0]],
            "asks": [{"price": float(row["px"]), "size": float(row["sz"])} for row in levels[1]],
        }
        self._orderbook_cache[cache_key] = out
        self._store_cache("orderbooks", disk_key, out)
        return out

    def fetch_hl_orderbook_fresh(self, symbol: str, store_cache: bool = False) -> Dict[str, Any]:
        data = http_post_json(HL_API, {"type": "l2Book", "coin": hl_symbol(symbol)}, retries=1, timeout=8.0)
        levels = data.get("levels", [[], []])
        out = {
            "bids": [{"price": float(row["px"]), "size": float(row["sz"])} for row in levels[0]],
            "asks": [{"price": float(row["px"]), "size": float(row["sz"])} for row in levels[1]],
        }
        if store_cache:
            cache_key = ("hl", normalize_symbol(symbol))
            self._orderbook_cache[cache_key] = out
            self._store_cache("orderbooks", f"hl:{normalize_symbol(symbol)}", out)
        return out

    def fetch_lt_orderbook(self, market_id: int, limit: int = 50) -> Dict[str, Any]:
        return self.fetch_lt_orderbook_cached(market_id, limit=limit)

    def fetch_lt_orderbook_cached(self, market_id: int, limit: int = 50) -> Dict[str, Any]:
        cache_key = ("lt", market_id, limit)
        cached = self._orderbook_cache.get(cache_key)
        if cached:
            return cached
        disk_key = f"lt:{market_id}:{limit}"
        disk_cached = self._cached_value("orderbooks", disk_key, self._orderbook_ttl())
        if disk_cached is not None:
            self._orderbook_cache[cache_key] = disk_cached
            return disk_cached
        params = parse.urlencode({"market_id": market_id, "limit": limit})
        try:
            data = http_get_json(f"{LT_BASE}/orderBookOrders?{params}")
        except Exception as exc:
            if should_retry(exc):
                stale = self._stale_value("orderbooks", disk_key, self._stale_orderbook_ttl())
                if stale is not None:
                    self._orderbook_cache[cache_key] = stale
                    return stale
            raise
        out = {
            "bids": [{"price": float(row["price"]), "size": float(row["remaining_base_amount"])} for row in data.get("bids", [])],
            "asks": [{"price": float(row["price"]), "size": float(row["remaining_base_amount"])} for row in data.get("asks", [])],
        }
        self._orderbook_cache[cache_key] = out
        self._store_cache("orderbooks", disk_key, out)
        return out

    def fetch_lt_orderbook_fresh(self, market_id: int, limit: int = 50, store_cache: bool = False) -> Dict[str, Any]:
        params = parse.urlencode({"market_id": market_id, "limit": limit})
        data = http_get_json(f"{LT_BASE}/orderBookOrders?{params}", retries=1, timeout=8.0)
        out = {
            "bids": [{"price": float(row["price"]), "size": float(row["remaining_base_amount"])} for row in data.get("bids", [])],
            "asks": [{"price": float(row["price"]), "size": float(row["remaining_base_amount"])} for row in data.get("asks", [])],
        }
        if store_cache:
            cache_key = ("lt", market_id, limit)
            self._orderbook_cache[cache_key] = out
            self._store_cache("orderbooks", f"lt:{market_id}:{limit}", out)
        return out


def pair_series(hl: List[Dict[str, float]], lt: List[Dict[str, float]], max_gap_ms: int = 600_000) -> List[Dict[str, float]]:
    hl_sorted = sorted((row for row in hl if row.get("t") is not None), key=lambda row: row["t"])
    lt_sorted = sorted((row for row in lt if row.get("t") is not None), key=lambda row: row["t"])
    lt_times = [row["t"] for row in lt_sorted]
    pairs = []
    used_lt_indexes = set()
    for h in hl_sorted:
        idx = bisect.bisect_left(lt_times, h["t"])
        candidates = []
        if idx < len(lt_sorted):
            candidates.append(idx)
        if idx > 0:
            candidates.append(idx - 1)
        best_idx = None
        best_gap = None
        for cand in candidates:
            if cand in used_lt_indexes:
                continue
            gap = abs(lt_sorted[cand]["t"] - h["t"])
            if best_gap is None or gap < best_gap:
                best_idx = cand
                best_gap = gap
        if best_idx is not None and best_gap is not None and best_gap <= max_gap_ms:
            used_lt_indexes.add(best_idx)
            pairs.append({"t": h["t"], "h": h["v"], "l": lt_sorted[best_idx]["v"]})
    return pairs


def calc_vwap_slippage_bps(levels: List[Dict[str, float]], side: str, notional_usd: float) -> Optional[float]:
    if not levels or notional_usd <= 0:
        return None
    remaining_quote = notional_usd
    filled_quote = 0.0
    filled_base = 0.0
    best_price = float(levels[0]["price"])
    for level in levels:
        price = float(level["price"])
        size = float(level["size"])
        if price <= 0 or size <= 0:
            continue
        level_quote = price * size
        take_quote = min(remaining_quote, level_quote)
        take_base = take_quote / price
        filled_quote += take_quote
        filled_base += take_base
        remaining_quote -= take_quote
        if remaining_quote <= 1e-9:
            break
    if filled_quote + 1e-9 < notional_usd or filled_base <= 0:
        return None
    avg_price = filled_quote / filled_base
    if side == "buy":
        return (avg_price - best_price) / best_price * 10000.0
    return (best_price - avg_price) / best_price * 10000.0


def build_pair_details(
    hl_hist: List[Dict[str, float]],
    lt_hist: List[Dict[str, float]],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    max_gap_minutes = int(cfg.get("scan", {}).get("pair_max_gap_minutes", 10))
    pairs = pair_series(hl_hist, lt_hist, max_gap_ms=max_gap_minutes * 60_000)
    if not pairs:
        return {"pairs": [], "stats": None}
    spreads = [p["h"] - p["l"] for p in pairs]
    avg = sum(spreads) / len(spreads)
    avg_abs = sum(abs(x) for x in spreads) / len(spreads)
    pos = sum(1 for x in spreads if x > 0)
    neg = sum(1 for x in spreads if x < 0)
    consistency = ((pos if avg >= 0 else neg) / len(spreads) * 100.0) if spreads else 0.0
    strategy_sign = 1.0 if avg > 0 else -1.0 if avg < 0 else 0.0
    carry_spreads = [s * strategy_sign for s in spreads] if strategy_sign else [0.0 for _ in spreads]
    pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    best = 0
    streak_dir: Optional[str] = None
    best_dir = "neutral"
    cum_pnl = []
    for i, spread in enumerate(spreads):
        sign = "hl" if spread > 1e-8 else "lt" if spread < -1e-8 else "flat"
        if sign == streak_dir and sign != "flat":
            streak += 1
        elif sign == "flat":
            streak = 0
            streak_dir = None
        else:
            streak = 1
            streak_dir = sign
        if streak > best:
            best = streak
            best_dir = sign
        pnl += carry_spreads[i]
        cum_pnl.append(pnl)
        peak = max(peak, pnl)
        max_dd = max(max_dd, peak - pnl)
    return {
        "pairs": pairs,
        "stats": {
            "pairs": len(pairs),
            "avg": avg,
            "avg_abs": avg_abs,
            "consistency_pct": consistency,
            "direction": "hl" if avg >= 0 else "lt",
            "cum_pnl": cum_pnl,
            "max_drawdown_bps": pct_to_bps(max_dd),
            "longest_streak_hours": best,
            "streak_direction": best_dir,
        },
    }


def calc_signal(symbol: str, hl_hist: List[Dict[str, float]], lt_hist: List[Dict[str, float]], hold_hours: int, cfg: Dict[str, Any]) -> Optional[Signal]:
    max_gap_minutes = int(cfg.get("scan", {}).get("pair_max_gap_minutes", 10))
    pairs = pair_series(hl_hist, lt_hist, max_gap_ms=max_gap_minutes * 60_000)
    if not pairs:
        return None

    spreads = [p["h"] - p["l"] for p in pairs]
    avg = sum(spreads) / len(spreads)
    avg_abs = sum(abs(x) for x in spreads) / len(spreads)
    strategy_sign = 1.0 if avg > 0 else -1.0 if avg < 0 else 0.0
    carry_spreads = [s * strategy_sign for s in spreads] if strategy_sign else [0.0 for _ in spreads]
    pos = sum(1 for x in spreads if x > 0)
    neg = sum(1 for x in spreads if x < 0)
    consistency = ((pos if avg >= 0 else neg) / len(spreads) * 100.0) if spreads else 0.0

    streak = 0
    best = 0
    streak_dir = None
    pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for i, s in enumerate(spreads):
        sign = "pos" if s > 1e-8 else "neg" if s < -1e-8 else "flat"
        if sign == streak_dir and sign != "flat":
            streak += 1
        elif sign == "flat":
            streak = 0
            streak_dir = None
        else:
            streak = 1
            streak_dir = sign
        best = max(best, streak)
        pnl += carry_spreads[i]
        peak = max(peak, pnl)
        max_dd = max(max_dd, peak - pnl)

    last = pairs[-1]
    current_spread = last["h"] - last["l"]
    trade = trade_for_spread(avg)
    long_venue = "Lighter" if avg > 0 else "Hyperliquid" if avg < 0 else "None"
    short_venue = "Hyperliquid" if avg > 0 else "Lighter" if avg < 0 else "None"

    costs = cfg["costs"]
    total_cost_bps = estimated_roundtrip_cost_bps(costs, cfg["execution"])
    cost_pct = total_cost_bps / 10000.0
    expected_gross_pct_hold = abs(avg) * hold_hours
    expected_net_pct_hold = expected_gross_pct_hold - cost_pct
    
    # Proyección Anualizada: Asumimos que entramos una vez y mantenemos (Hold largo)
    # Por lo tanto, el APY Neto es el APY Bruto menos el costo total que pagamos una sola vez.
    signed_apy = annualized_pct(avg)
    carry_apy = abs(signed_apy)
    net_est_apy = carry_apy - (cost_pct * 100.0)
    
    break_even_hours = (cost_pct / abs(avg)) if abs(avg) > 1e-9 else None

    rules = cfg["scan"]
    signed_apy = annualized_pct(avg)
    carry_apy = abs(signed_apy)
    passes = all(
        [
            carry_apy >= rules["min_gross_apy"],
            expected_net_pct_hold * (8760.0 / hold_hours) >= rules["min_net_apy"],
            consistency >= rules["min_consistency_pct"],
            pct_to_bps(max_dd) <= rules["max_drawdown_bps"],
            len(pairs) >= rules["min_samples"],
        ]
    )

    return Signal(
        symbol=symbol,
        samples=len(pairs),
        current_hl_rate=last["h"],
        current_lt_rate=last["l"],
        current_spread=current_spread,
        avg_spread=avg,
        avg_abs_spread=avg_abs,
        gross_apy=carry_apy,
        signed_apy=signed_apy,
        consistency_pct=consistency,
        longest_streak_hours=best,
        max_drawdown_bps=pct_to_bps(max_dd),
        trade=trade,
        long_venue=long_venue,
        short_venue=short_venue,
        expected_cost_pct_hold=cost_pct * 100.0,
        expected_gross_pct_hold=expected_gross_pct_hold * 100.0,
        expected_net_pct_hold=expected_net_pct_hold * 100.0,
        net_est_apy=net_est_apy,
        passes=passes,
        break_even_hours=break_even_hours,
    )


def apply_liquidity_checks(
    signal: Signal,
    market: MarketData,
    symbol: str,
    market_id: int,
    cfg: Dict[str, Any],
    use_fresh_books: bool = False,
) -> Signal:
    notional = float(cfg["execution"]["default_notional_usd"])
    max_slippage_bps = float(cfg["execution"]["max_slippage_bps"])
    if use_fresh_books:
        hl_orderbook = market.fetch_hl_orderbook_fresh(symbol)
        lt_orderbook = market.fetch_lt_orderbook_fresh(market_id)
    else:
        hl_orderbook = market.fetch_hl_orderbook_cached(symbol)
        lt_orderbook = market.fetch_lt_orderbook_cached(market_id)

    if signal.long_venue == "Lighter":
        lt_slippage = calc_vwap_slippage_bps(lt_orderbook["asks"], "buy", notional)
        hl_slippage = calc_vwap_slippage_bps(hl_orderbook["bids"], "sell", notional)
    elif signal.long_venue == "Hyperliquid":
        hl_slippage = calc_vwap_slippage_bps(hl_orderbook["asks"], "buy", notional)
        lt_slippage = calc_vwap_slippage_bps(lt_orderbook["bids"], "sell", notional)
    else:
        hl_slippage = 0.0
        lt_slippage = 0.0

    liquidity_ok = hl_slippage is not None and lt_slippage is not None
    if not liquidity_ok:
        return replace(
            signal,
            liquidity_ok=False,
            hl_liquidity_ok=hl_slippage is not None,
            lt_liquidity_ok=lt_slippage is not None,
            hl_liquidity_fail=None if hl_slippage is not None else "insufficient_depth_or_missing_book",
            lt_liquidity_fail=None if lt_slippage is not None else "insufficient_depth_or_missing_book",
            used_fresh_books=use_fresh_books,
            passes=False,
        )

    # Entry slippage comes from fresh/cached books. Exit slippage remains an estimate.
    total_cost_bps = (
        float(cfg["costs"]["hyperliquid_entry_bps"])
        + float(cfg["costs"]["hyperliquid_exit_bps"])
        + float(cfg["costs"]["lighter_entry_bps"])
        + float(cfg["costs"]["lighter_exit_bps"])
        + float(hl_slippage)
        + float(lt_slippage)
        + 2 * float(cfg["costs"]["estimated_exit_slippage_per_leg_bps"])
        + float(cfg["execution"].get("entry_buffer_bps", 0.0))
    )
    cost_pct = total_cost_bps / 10000.0
    expected_gross_pct_hold = signal.expected_gross_pct_hold / 100.0
    expected_net_pct_hold = expected_gross_pct_hold - cost_pct

    # Proyección Anualizada Realista: Bruto Anual - Costo Roundtrip único
    net_est_apy = signal.gross_apy - (cost_pct * 100.0)

    hl_liq_ok = hl_slippage is not None and hl_slippage <= max_slippage_bps
    lt_liq_ok = lt_slippage is not None and lt_slippage <= max_slippage_bps
    
    passes = (
        signal.gross_apy >= cfg["scan"]["min_gross_apy"]
        and expected_net_pct_hold * (8760.0 / cfg["scan"]["hold_hours"]) >= cfg["scan"]["min_net_apy"]
        and signal.consistency_pct >= cfg["scan"]["min_consistency_pct"]
        and signal.max_drawdown_bps <= cfg["scan"]["max_drawdown_bps"]
        and signal.samples >= cfg["scan"]["min_samples"]
        and hl_liq_ok
        and lt_liq_ok
    )

    return replace(
        signal,
        hl_entry_slippage_bps=hl_slippage,
        lt_entry_slippage_bps=lt_slippage,
        total_cost_bps=total_cost_bps,
        expected_cost_pct_hold=cost_pct * 100.0,
        expected_gross_pct_hold=expected_gross_pct_hold * 100.0,
        expected_net_pct_hold=expected_net_pct_hold * 100.0,
        net_est_apy=net_est_apy,
        liquidity_ok=hl_liq_ok and lt_liq_ok,
        hl_liquidity_ok=hl_liq_ok,
        lt_liquidity_ok=lt_liq_ok,
        used_fresh_books=use_fresh_books,
        passes=passes,
    )

class Bot:
    def __init__(self, cfg: Dict[str, Any], env: Dict[str, str]):
        self.cfg = cfg
        self.env = env
        self.scan_cache = load_scan_cache()
        self.market = MarketData(self.scan_cache, cfg)
        self.lt_markets: Optional[Dict[str, int]] = None
        self._common_assets: Optional[List[str]] = None
        self.last_scan_errors: List[str] = []

    def ensure_lt_markets(self) -> Dict[str, int]:
        if self.lt_markets is None:
            self.lt_markets = self.market.fetch_lt_markets()
        return self.lt_markets

    def common_assets(self) -> List[str]:
        if self._common_assets is None:
            hl = set(self.market.fetch_hl_universe())
            lt = set(self.ensure_lt_markets().keys())
            self._common_assets = sorted(hl & lt)
        return self._common_assets

    def resolve_hold_hours(self, hours: int) -> int:
        return max(1, int(hours or self.cfg["scan"]["hold_hours"]))

    def inspect(self, symbol: str, hours: int) -> Optional[Signal]:
        symbol = normalize_symbol(symbol)
        lt_markets = self.ensure_lt_markets()
        market_id = lt_markets.get(symbol) or lt_markets.get(lt_symbol(symbol))
        if market_id is None:
            raise SystemExit(f"No encontré market_id de Lighter para {symbol}")
        hl_hist = self.market.fetch_hl_history(symbol, hours)
        lt_hist = self.market.fetch_lt_history(market_id, hours)
        if not hl_hist:
            raise SystemExit(f"Hyperliquid no devolvió historial para {symbol}")
        if not lt_hist:
            raise SystemExit(f"Lighter no devolvió historial para {symbol}")
        sig = calc_signal(symbol, hl_hist, lt_hist, self.resolve_hold_hours(hours), self.cfg)
        if not sig:
            return None
        return apply_liquidity_checks(sig, self.market, symbol, market_id, self.cfg)

    def get_enriched_status(self) -> List[Dict[str, Any]]:
        state = load_state()
        positions = state.get("positions", [])
        enriched = []
        for pos in positions:
            hedged = pos.get("hedged_notional_usd", pos.get("notional_usd", 0.0))
            unhedged = pos.get("unhedged_notional_usd", 0.0)
            elapsed_hours = max(0.0, (time.time() - pos["opened_at"]) / 3600.0)

            hours_to_fetch = int(elapsed_hours) + 2
            symbol = pos["symbol"]
            
            try:
                market_id = self.ensure_lt_markets().get(symbol) or self.ensure_lt_markets().get(lt_symbol(symbol))
                hl_hist = self.market.fetch_hl_history_cached(symbol, hours_to_fetch)
                lt_hist = self.market.fetch_lt_history_cached(market_id, hours_to_fetch) if market_id else []
                pairs = pair_series(hl_hist, lt_hist)
                
                opened_at_ms = pos["opened_at"] * 1000
                realized_funding_pct = 0.0
                if pos["trade"].startswith("Long Lighter"):
                    trade_dir = 1.0
                elif pos["trade"].startswith("Long HL"):
                    trade_dir = -1.0
                else:
                    trade_dir = 0.0
                
                for p in pairs:
                    if p["t"] >= opened_at_ms:
                        realized_funding_pct += (p["h"] - p["l"]) * trade_dir
                
                funding_now_usd = realized_funding_pct * hedged
            except Exception:
                funding_now_usd = 0.0

            entry_cost_usd = pos.get("entry_cost_usd", 0.0)
            adverse_drift_usd = pos.get("adverse_drift_usd", 0.0)
            expected_now_usd = funding_now_usd - entry_cost_usd - adverse_drift_usd
            
            enriched.append({
                **pos,
                "elapsed_hours": elapsed_hours,
                "realtime_funding_pnl_usd": funding_now_usd,
                "realtime_net_pnl_usd": expected_now_usd,
                "realtime_entry_cost_usd": entry_cost_usd,
                "realtime_adverse_drift_usd": adverse_drift_usd,
                "realtime_hedged_usd": hedged,
                "realtime_unhedged_usd": unhedged
            })
        return enriched

    def pre_trade_check(self, symbol: str, hours: int) -> Dict[str, Any]:
        symbol = normalize_symbol(symbol)
        lt_markets = self.ensure_lt_markets()
        market_id = lt_markets.get(symbol) or lt_markets.get(lt_symbol(symbol))
        if market_id is None:
            raise SystemExit(f"No encontré market_id de Lighter para {symbol}")
        hl_hist = self.market.fetch_hl_history_cached(symbol, hours)
        lt_hist = self.market.fetch_lt_history_cached(market_id, hours)
        if not hl_hist or not lt_hist:
            raise SystemExit(f"No hay historial suficiente para {symbol}")
        hold_hours = self.resolve_hold_hours(hours)
        sig = calc_signal(symbol, hl_hist, lt_hist, hold_hours, self.cfg)
        if not sig:
            raise SystemExit(f"No pude calcular señal para {symbol}")
        sig = apply_liquidity_checks(sig, self.market, symbol, market_id, self.cfg, use_fresh_books=True)
        backend_health = http_get_json("http://127.0.0.1:8787/health", retries=0, timeout=5.0)
        go = bool(backend_health.get("ok")) and bool(sig.passes) and bool(sig.liquidity_ok)
        return {
            "symbol": symbol,
            "go": go,
            "trade": sig.trade,
            "net_est_apy": sig.net_est_apy,
            "expected_net_pct_hold": sig.expected_net_pct_hold,
            "hl_entry_slippage_bps": sig.hl_entry_slippage_bps,
            "lt_entry_slippage_bps": sig.lt_entry_slippage_bps,
            "total_cost_bps": sig.total_cost_bps,
            "used_fresh_books": sig.used_fresh_books,
            "backend_health_ok": bool(backend_health.get("ok")),
            "liquidity_ok": sig.liquidity_ok,
            "hl_liquidity_fail": sig.hl_liquidity_fail,
            "lt_liquidity_fail": sig.lt_liquidity_fail,
            "passes": sig.passes,
            "analysis_hours": hours,
            "hold_hours": hold_hours,
        }

    def prepare_trade(self, symbol: str, hours: int, notional_usd: float, run_id: Optional[str] = None) -> Dict[str, Any]:
        check = self.pre_trade_check(symbol, hours)
        if not check["go"]:
            raise SystemExit(f"No se puede preparar trade para {symbol}. Pre-trade check falló.")
            
        limits = self.cfg.get("risk", {})
        max_notional = float(limits.get("max_notional_per_asset_usd", 2000.0))
        if notional_usd > max_notional:
            raise SystemExit(f"Notional {notional_usd} excede max_notional_per_asset_usd ({max_notional})")
            
        plan_id = str(uuid.uuid4())
        plan_json = json.dumps({
            "symbol": symbol,
            "notional_usd": notional_usd,
            "trade": check["trade"],
            "max_slippage_abort_bps": float(limits.get("max_slippage_abort_bps", 10.0)),
            "plan_expiry_sec": int(limits.get("plan_expiry_sec", 300))
        })
        
        conn = get_db()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO trade_plans(id, symbol, trade, notional_usd, plan_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (plan_id, symbol, check["trade"], notional_usd, plan_json, "pending", time.time())
                )
            return {"plan_id": plan_id, "symbol": symbol, "trade": check["trade"], "notional_usd": notional_usd}
        finally:
            conn.close()

    def execute_trade(self, plan_id: str, run_id: Optional[str] = None) -> Dict[str, Any]:
        # TODO (O1): Consider re-validating the funding spread before executing,
        #   as the plan may have been created minutes ago and conditions could have changed.
        if self.cfg.get("risk", {}).get("kill_switch_active", False):
            raise SystemExit("Kill switch is ACTIVE. Ejecución rechazada.")

        conn = get_db()
        try:
            row = conn.execute("SELECT plan_json, status, created_at FROM trade_plans WHERE id = ?", (plan_id,)).fetchone()
            if not row:
                raise SystemExit(f"Plan {plan_id} no encontrado.")

            plan_raw, status, created_at = row
            if status != "pending":
                raise SystemExit(f"Plan {plan_id} no está en estado pending (actual: {status})")

            plan = json.loads(plan_raw)
            expiry = plan.get("plan_expiry_sec", 300)
            if time.time() - float(created_at) > expiry:
                with conn:
                    conn.execute("UPDATE trade_plans SET status = 'expired' WHERE id = ?", (plan_id,))
                raise SystemExit(f"Plan {plan_id} expirado.")

            symbol = plan["symbol"]
            trade = plan["trade"]
            notional_usd = plan["notional_usd"]
            max_slippage = plan.get("max_slippage_abort_bps", 10.0)

            if "Long HL" in trade:
                leg1_venue, leg1_side = "Hyperliquid", "Buy"
                leg2_venue, leg2_side = "Lighter", "Sell"
            elif "Long Lighter" in trade:
                leg1_venue, leg1_side = "Lighter", "Buy"
                leg2_venue, leg2_side = "Hyperliquid", "Sell"
            else:
                raise SystemExit(f"Dirección de trade inválida para ejecución real: {trade}")

            # O1: Re-validate fresh L2Book
            try:
                check = self.pre_trade_check(symbol, int(self.cfg.get("scan", {}).get("hours", 168)))
                if not check["go"]:
                    print("⚠️ Spread abort: pre-trade en vivo falló.", check)
                    final_status = "aborted_pre_execution"
                    with conn:
                        conn.execute("UPDATE trade_plans SET status = ? WHERE id = ?", (final_status, plan_id))
                    return {"plan_id": plan_id, "status": final_status, "results": {"abort_reason": check}}
            except Exception as e:
                print("⚠️ No se pudo validar pre-trade en vivo, asumiendo falla:", e)
                raise SystemExit("Pre-trade check failed with exception.")

            results: Dict[str, Any] = {}

            adapter1 = get_adapter(leg1_venue, self.env, self.cfg, mode="real")
            print(f"Ejecutando pata 1: {leg1_venue} {leg1_side} {notional_usd} USD")
            ok1, res1 = adapter1.place_market_order(symbol, leg1_side, notional_usd, max_slippage, plan_id=plan_id)
            results["leg1"] = {"success": ok1, "venue": leg1_venue, "side": leg1_side, "response": res1}

            if ok1:
                print(f"Pata 1 OK. Ejecutando pata 2: {leg2_venue} {leg2_side} {notional_usd} USD")
                adapter2 = get_adapter(leg2_venue, self.env, self.cfg, mode="real")
                ok2, res2 = adapter2.place_market_order(symbol, leg2_side, notional_usd, max_slippage, plan_id=plan_id)
                results["leg2"] = {"success": ok2, "venue": leg2_venue, "side": leg2_side, "response": res2}

                if ok2:
                    final_status = "ok"
                else:
                    # ⚠️ PARTIAL FILL: leg1 filled but leg2 failed — naked exposure!
                    print(f"⚠️ PARTIAL FILL: {leg1_venue} {leg1_side} llenó pero {leg2_venue} {leg2_side} falló.")
                    print(f"⚠️ Intentando cerrar pata 1 a mercado en {leg1_venue}...")
                    
                    if "webhook_url" in self.cfg.get("alerts", {}) and self.cfg["alerts"]["webhook_url"]:
                        try:
                            http_post_json(self.cfg["alerts"]["webhook_url"], {"text": f"🚨 URGENTE: PARTIAL FILL en bot de arbitraje: {symbol} - {leg2_venue} {leg2_side} FALLÓ luego de llenar {leg1_venue}!"})
                        except Exception:
                            pass
                            
                    try:
                        opposite_side = "Sell" if leg1_side == "Buy" else "Buy"
                        cancel_ok, cancel_res = adapter1.place_market_order(symbol, opposite_side, notional_usd, 999.0, plan_id=plan_id + "-unwind")
                        results["unwind_attempt"] = {"success": cancel_ok, "response": cancel_res}
                        print(f"   Unwind leg1 (Market {opposite_side}): {'OK' if cancel_ok else 'FAILED'}")
                    except Exception as cancel_exc:
                        results["unwind_attempt"] = {"success": False, "error": str(cancel_exc)}
                        print(f"   Unwind leg1 exception (market order close failed): {cancel_exc}")

                    log_structured_event(
                        "ALERT_PARTIAL_FILL",
                        run_id=run_id,
                        status="error",
                        event_input={"plan_id": plan_id, "symbol": symbol},
                        result={"unwind_attempt": results.get("unwind_attempt")},
                        error=str(res2),
                        meta={
                            "leg1_venue": leg1_venue,
                            "leg1_side": leg1_side,
                            "leg2_venue": leg2_venue,
                            "leg2_side": leg2_side,
                        },
                    )
                    final_status = "partial_unwind_attempted"
            else:
                print(f"Pata 1 falló. No se ejecutará la pata 2.")
                final_status = "failed"

            plan_db_status = "executed" if final_status == "ok" else final_status
            with conn:
                conn.execute("UPDATE trade_plans SET status = ? WHERE id = ?", (plan_db_status, plan_id))
                conn.execute(
                    "INSERT INTO trade_executions(id, plan_id, result_json, status, executed_at) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), plan_id, json.dumps(results), final_status, time.time())
                )

            return {"plan_id": plan_id, "status": final_status, "results": results}
        finally:
            conn.close()

    def inspect_payload(self, symbol: str, hours: int) -> Dict[str, Any]:
        symbol = normalize_symbol(symbol)
        lt_markets = self.ensure_lt_markets()
        market_id = lt_markets.get(symbol) or lt_markets.get(lt_symbol(symbol))
        if market_id is None:
            raise SystemExit(f"No encontré market_id de Lighter para {symbol}")
        hl_hist = self.market.fetch_hl_history_cached(symbol, hours)
        lt_hist = self.market.fetch_lt_history_cached(market_id, hours)
        sig = calc_signal(symbol, hl_hist, lt_hist, self.cfg["scan"]["hold_hours"], self.cfg)
        if sig is None:
            raise SystemExit(f"No pude calcular señal para {symbol}")
        sig = apply_liquidity_checks(sig, self.market, symbol, market_id, self.cfg)
        pair_details = build_pair_details(hl_hist, lt_hist, self.cfg)
        return {
            "symbol": symbol,
            "hours": hours,
            "market_id": market_id,
            "signal": asdict(sig),
            "hl_history": hl_hist,
            "lt_history": lt_hist,
            "pairs": pair_details["pairs"],
            "pair_stats": pair_details["stats"],
        }

    def _scan_historical_candidate(self, symbol: str, hours: int) -> Optional[tuple]:
        market_id = self.ensure_lt_markets().get(symbol)
        if market_id is None:
            return None
        hl_hist = self.market.fetch_hl_history_cached(symbol, hours)
        lt_hist = self.market.fetch_lt_history_cached(market_id, hours)
        if not hl_hist or not lt_hist:
            return None
        sig = calc_signal(symbol, hl_hist, lt_hist, self.cfg["scan"]["hold_hours"], self.cfg)
        if not sig:
            return None
        return (symbol, market_id, sig)

    def _scan_cache_key(self, hours: int, top: int) -> str:
        cache_cfg = {
            "scan": {
                "hold_hours": self.cfg["scan"]["hold_hours"],
                "min_gross_apy": self.cfg["scan"]["min_gross_apy"],
                "min_net_apy": self.cfg["scan"]["min_net_apy"],
                "min_consistency_pct": self.cfg["scan"]["min_consistency_pct"],
                "max_drawdown_bps": self.cfg["scan"]["max_drawdown_bps"],
                "min_samples": self.cfg["scan"]["min_samples"],
            },
            "execution": {
                "default_notional_usd": self.cfg["execution"]["default_notional_usd"],
                "max_slippage_bps": self.cfg["execution"]["max_slippage_bps"],
                "entry_buffer_bps": self.cfg["execution"].get("entry_buffer_bps", 0.0),
            },
            "costs": self.cfg["costs"],
        }
        fingerprint = hashlib.sha1(json.dumps(cache_cfg, sort_keys=True).encode()).hexdigest()[:12]
        return f"{hours}:{top}:{fingerprint}"

    def _load_incremental_scan(self, hours: int, top: int) -> Optional[List[Signal]]:
        ttl = int(self.cfg.get("scan", {}).get("scan_result_ttl_sec", 60))
        entry = self.scan_cache.get("scans", {}).get(self._scan_cache_key(hours, top))
        if not entry:
            return None
        if time.time() - float(entry.get("ts", 0)) > ttl:
            return None
        data = entry.get("signals", [])
        if not data:
            return None
        return [Signal(**item) for item in data]

    def _store_incremental_scan(self, hours: int, top: int, signals: List[Signal]) -> None:
        if not signals:
            return
        self.scan_cache.setdefault("scans", {})[self._scan_cache_key(hours, top)] = {
            "ts": time.time(),
            "signals": [asdict(sig) for sig in signals],
        }
        save_scan_cache(self.scan_cache)

    def scan(self, hours: int, top: int) -> List[Signal]:
        cached_scan = self._load_incremental_scan(hours, top)
        if cached_scan is not None:
            self.last_scan_errors = []
            return cached_scan
        historical_candidates = []
        error_counts: Dict[str, int] = {}
        scan_workers = max(1, int(self.cfg.get("scan", {}).get("scan_workers", 6)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=scan_workers) as executor:
            future_map = {
                executor.submit(self._scan_historical_candidate, symbol, hours): symbol for symbol in self.common_assets()
            }
            for future in concurrent.futures.as_completed(future_map):
                try:
                    candidate = future.result()
                except Exception as exc:
                    log_error_detail(
                        source="scan",
                        operation="historical_candidate",
                        error_obj=exc,
                        target=future_map[future],
                        context={"hours": hours, "top": top, "stage": "history"},
                    )
                    key = f"{type(exc).__name__}: {exc}"
                    error_counts[key] = error_counts.get(key, 0) + 1
                    continue
                if candidate:
                    historical_candidates.append(candidate)
        historical_candidates.sort(key=lambda item: (item[2].net_est_apy, abs(item[2].signed_apy), item[2].consistency_pct), reverse=True)
        shortlist_multiplier = max(1, int(self.cfg.get("scan", {}).get("liquidity_shortlist_multiplier", 3)))
        shortlist = historical_candidates[:top * shortlist_multiplier]
        signals = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(scan_workers, len(shortlist) or 1)) as executor:
            future_map = {
                executor.submit(apply_liquidity_checks, sig, self.market, symbol, market_id, self.cfg): symbol
                for symbol, market_id, sig in shortlist
            }
            for future in concurrent.futures.as_completed(future_map):
                try:
                    sig = future.result()
                except Exception as exc:
                    log_error_detail(
                        source="scan",
                        operation="liquidity_check",
                        error_obj=exc,
                        target=future_map[future],
                        context={"hours": hours, "top": top, "stage": "orderbook"},
                    )
                    key = f"{type(exc).__name__}: {exc}"
                    error_counts[key] = error_counts.get(key, 0) + 1
                    continue
                signals.append(sig)
        final = sorted(signals, key=lambda s: (s.net_est_apy, abs(s.signed_apy), s.consistency_pct), reverse=True)[:top]
        self.last_scan_errors = [f"{count}x {msg}" for msg, count in sorted(error_counts.items(), key=lambda item: item[1], reverse=True)[:10]]
        self._store_incremental_scan(hours, top, final)
        return final


def print_signal(sig: Signal) -> None:
    print(f"Activo: {sig.symbol}")
    print(f"Trade: {sig.trade}")
    print(f"Current HL 1h: {fmt_pct(sig.current_hl_rate * 100 if sig.current_hl_rate is not None else None, 4)}")
    print(f"Current LT 1h: {fmt_pct(sig.current_lt_rate * 100 if sig.current_lt_rate is not None else None, 4)}")
    print(f"Spread now: {fmt_bps(sig.current_spread * 10000 if sig.current_spread is not None else None, 2)}")
    print(f"Carry APY: {fmt_pct(sig.gross_apy, 2)}")
    print(f"Signed APY: {fmt_pct(sig.signed_apy, 2)}")
    print(f"Abs spread APY: {fmt_pct(annualized_pct(sig.avg_abs_spread), 2)}")
    print(f"Consistency: {sig.consistency_pct:.1f}%")
    print(f"Longest streak: {sig.longest_streak_hours}h")
    print(f"Max drawdown: {sig.max_drawdown_bps:.1f} bps")
    print(f"Expected gross over hold: {fmt_pct(sig.expected_gross_pct_hold, 2)}")
    print(f"Expected cost over hold: {fmt_pct(sig.expected_cost_pct_hold, 2)}")
    print(f"Expected net over hold: {fmt_pct(sig.expected_net_pct_hold, 2)}")
    print(f"Net est APY: {fmt_pct(sig.net_est_apy, 2)}")
    if sig.hl_entry_slippage_bps is not None:
        print(f"HL entry slippage est: {sig.hl_entry_slippage_bps:.2f} bps")
    if sig.lt_entry_slippage_bps is not None:
        print(f"LT entry slippage est: {sig.lt_entry_slippage_bps:.2f} bps")
    if sig.total_cost_bps is not None:
        print(f"Total est roundtrip cost: {sig.total_cost_bps:.2f} bps")
    if sig.liquidity_ok is not None:
        print(f"Liquidity check: {'ok' if sig.liquidity_ok else 'failed'}")
    if sig.hl_liquidity_fail:
        print(f"HL liquidity fail: {sig.hl_liquidity_fail}")
    if sig.lt_liquidity_fail:
        print(f"LT liquidity fail: {sig.lt_liquidity_fail}")
    print(f"Passes rules: {'yes' if sig.passes else 'no'}")


def command_scan(args: argparse.Namespace, bot: Bot) -> None:
    started_at = time.time()
    results = bot.scan(args.hours, args.top)
    log_structured_event(
        "scan",
        run_id=args.run_id,
        started_at=started_at,
        status="degraded" if bot.last_scan_errors else "ok",
        event_input={"hours": args.hours, "top": args.top},
        result={"result_count": len(results), "top_symbols": [sig.symbol for sig in results]},
        warnings=bot.last_scan_errors,
    )
    if not results:
        print("No encontré señales válidas.")
        return
    print("symbol  trade                      carryAPY signedAPY netAPY   hlSlip ltSlip consist  mdd    samples")
    for sig in results:
        print(
            f"{sig.symbol:<7} {sig.trade[:25]:<25} "
            f"{sig.gross_apy:>8.1f}% {sig.signed_apy:>9.1f}% {sig.net_est_apy:>7.1f}% "
            f"{(sig.hl_entry_slippage_bps if sig.hl_entry_slippage_bps is not None else float('nan')):>6.2f} "
            f"{(sig.lt_entry_slippage_bps if sig.lt_entry_slippage_bps is not None else float('nan')):>6.2f} "
            f"{sig.consistency_pct:>7.1f}% {sig.max_drawdown_bps:>6.1f} {sig.samples:>8}"
        )
    if bot.last_scan_errors:
        print("")
        print("Scan warnings:")
        for msg in bot.last_scan_errors:
            print(f"- {msg}")


def command_inspect(args: argparse.Namespace, bot: Bot) -> None:
    started_at = time.time()
    sig = bot.inspect(args.symbol, args.hours)
    log_structured_event(
        "inspect",
        run_id=args.run_id,
        started_at=started_at,
        event_input={"symbol": normalize_symbol(args.symbol), "hours": args.hours},
        result={"found": bool(sig), "symbol": sig.symbol if sig else normalize_symbol(args.symbol)},
    )
    if sig:
        print_signal(sig)


def command_status(_: argparse.Namespace, bot: Bot) -> None:
    started_at = time.time()
    positions = bot.get_enriched_status()
    log_structured_event(
        "status",
        run_id=getattr(_, "run_id", None),
        started_at=started_at,
        event_input={},
        result={"open_positions": len(positions)},
    )
    if not positions:
        print("No hay posiciones paper abiertas.")
        return
    for pos in positions:
        print(
            f"{pos['symbol']}: {pos['trade']} | notional={pos['notional_usd']:.2f} | "
            f"hedged={pos['realtime_hedged_usd']:.2f} | unhedged={pos['realtime_unhedged_usd']:.2f} | "
            f"opened_at_hours={pos['elapsed_hours']:.1f}h | expected_now={pos['realtime_net_pnl_usd']:+.2f} USD | "
            f"funding={pos['realtime_funding_pnl_usd']:+.2f} cost={pos['realtime_entry_cost_usd']:+.2f} drift={pos['realtime_adverse_drift_usd']:+.2f}"
        )


def simulate_paper_execution(sig: Signal, notional_usd: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    if sig.long_venue not in {"Lighter", "Hyperliquid"} or sig.short_venue not in {"Lighter", "Hyperliquid"}:
        raise SystemExit("La señal no tiene una dirección operable. No se puede abrir una posición neutral.")
    leg_delay_sec = int(cfg["execution"].get("max_leg_delay_sec", 8))
    maker_cutoff_bps = float(cfg["execution"].get("paper_maker_cutoff_bps", 0.5))
    fill_penalty = float(cfg["execution"].get("paper_fill_penalty_per_bps", 0.08))
    drift_per_sec_bps = float(cfg["execution"].get("paper_adverse_drift_bps_per_sec", 0.05))
    repost_slippage_bps = float(cfg["execution"].get("paper_repost_slippage_bps", 0.8))
    max_retries = int(cfg["execution"].get("paper_max_retries_per_leg", 2))
    maker_fill_bonus = float(cfg["execution"].get("paper_maker_fill_bonus", 0.08))
    taker_fee_bps = {
        "Hyperliquid": float(cfg["costs"].get("hyperliquid_entry_bps", 0.0)),
        "Lighter": float(cfg["costs"].get("lighter_entry_bps", 0.0)),
    }
    maker_fee_bps = {
        "Hyperliquid": float(cfg["costs"].get("hyperliquid_maker_entry_bps", 0.0)),
        "Lighter": float(cfg["costs"].get("lighter_maker_entry_bps", 0.0)),
    }

    def leg_fill_ratio(slippage_bps: float) -> float:
        if slippage_bps <= 0:
            return 1.0
        return clamp(1.0 - slippage_bps * fill_penalty, 0.35, 1.0)

    def execute_leg(venue: str, side: str, requested_notional_usd: float, base_slippage_bps: float, delay_sec: int) -> Dict[str, Any]:
        attempts = []
        remaining = requested_notional_usd
        filled = 0.0
        current_slippage = base_slippage_bps
        total_fee_usd = 0.0
        for attempt in range(max_retries + 1):
            maker_like = current_slippage <= maker_cutoff_bps
            fill_ratio = leg_fill_ratio(current_slippage)
            if maker_like:
                fill_ratio = clamp(fill_ratio + maker_fill_bonus, 0.0, 1.0)
            filled_now = remaining * fill_ratio
            fee_bps = maker_fee_bps[venue] if maker_like else taker_fee_bps[venue]
            fee_usd = filled_now * fee_bps / 10000.0
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "maker_like": maker_like,
                    "slippage_bps": current_slippage,
                    "requested_notional_usd": remaining,
                    "fill_ratio": fill_ratio,
                    "filled_notional_usd": filled_now,
                    "fee_bps": fee_bps,
                    "fee_usd": fee_usd,
                    "delay_sec": delay_sec if attempt == 0 else 1,
                }
            )
            filled += filled_now
            total_fee_usd += fee_usd
            remaining = max(0.0, requested_notional_usd - filled)
            if remaining <= requested_notional_usd * 0.02:
                break
            current_slippage += repost_slippage_bps
        return {
            "venue": venue,
            "side": side,
            "attempts": attempts,
            "requested_notional_usd": requested_notional_usd,
            "filled_notional_usd": filled,
            "remaining_notional_usd": remaining,
            "slippage_bps": attempts[-1]["slippage_bps"],
            "maker_like": all(a["maker_like"] for a in attempts),
            "effective_fee_usd": total_fee_usd,
            "total_delay_sec": sum(a["delay_sec"] for a in attempts),
            "attempt_count": len(attempts),
        }

    if sig.long_venue == "Lighter":
        first_leg = execute_leg("Lighter", "buy", notional_usd, float(sig.lt_entry_slippage_bps or 0.0), 0)
        second_leg = execute_leg("Hyperliquid", "sell", first_leg["filled_notional_usd"], float(sig.hl_entry_slippage_bps or 0.0), leg_delay_sec)
    else:
        first_leg = execute_leg("Hyperliquid", "buy", notional_usd, float(sig.hl_entry_slippage_bps or 0.0), 0)
        second_leg = execute_leg("Lighter", "sell", first_leg["filled_notional_usd"], float(sig.lt_entry_slippage_bps or 0.0), leg_delay_sec)

    hedged_notional = min(first_leg["filled_notional_usd"], second_leg["filled_notional_usd"])
    gross_filled = max(first_leg["filled_notional_usd"], second_leg["filled_notional_usd"])
    unhedged_notional = max(0.0, gross_filled - hedged_notional)
    effective_delay_sec = second_leg["total_delay_sec"]
    adverse_drift_bps = unhedged_notional and (effective_delay_sec * drift_per_sec_bps) or 0.0
    adverse_drift_usd = unhedged_notional * adverse_drift_bps / 10000.0
    execution_fee_usd = first_leg["effective_fee_usd"] + second_leg["effective_fee_usd"]
    exit_fee_usd = hedged_notional * (
        float(cfg["costs"].get("hyperliquid_exit_bps", 0.0)) + float(cfg["costs"].get("lighter_exit_bps", 0.0))
    ) / 10000.0
    buffer_usd = hedged_notional * float(cfg["execution"].get("entry_buffer_bps", 0.0)) / 10000.0
    estimated_total_cost_usd = execution_fee_usd + exit_fee_usd + buffer_usd
    expected_funding_pnl_usd = hedged_notional * (sig.expected_gross_pct_hold / 100.0)
    expected_hold_pnl_usd = expected_funding_pnl_usd - estimated_total_cost_usd - adverse_drift_usd

    return {
        "legs": [first_leg, second_leg],
        "hedged_notional_usd": hedged_notional,
        "unhedged_notional_usd": unhedged_notional,
        "adverse_drift_bps": adverse_drift_bps,
        "adverse_drift_usd": adverse_drift_usd,
        "execution_fee_usd": execution_fee_usd,
        "estimated_exit_fee_usd": exit_fee_usd,
        "buffer_usd": buffer_usd,
        "entry_cost_usd": estimated_total_cost_usd,
        "expected_funding_pnl_usd": expected_funding_pnl_usd,
        "expected_hold_pnl_usd": expected_hold_pnl_usd,
        "entry_liquidity_ok": bool(sig.liquidity_ok),
        "used_fresh_books": bool(sig.used_fresh_books),
        "max_leg_delay_sec": leg_delay_sec,
        "effective_leg_delay_sec": effective_delay_sec,
        "maker_like_entry": bool(first_leg["maker_like"] and second_leg["maker_like"]),
    }


def command_paper_open(args: argparse.Namespace, bot: Bot, cfg: Dict[str, Any]) -> None:
    started_at = time.time()
    if args.notional > cfg["execution"]["max_notional_usd"]:
        raise SystemExit("Supera el max_notional_usd configurado.")

    pretrade = bot.pre_trade_check(args.symbol, args.hours)
    sig = bot.inspect(args.symbol, args.hours)
    if not sig:
        raise SystemExit("No pude generar señal para ese activo.")
    if not sig.passes and not args.force:
        raise SystemExit("La señal no pasa las reglas. Usá --force si querés guardarla igual.")
    if not pretrade["backend_health_ok"]:
        raise SystemExit("Backend privado no saludable. No abro ni siquiera en paper semirrealista.")

    fresh_sig = apply_liquidity_checks(
        sig,
        bot.market,
        sig.symbol,
        bot.ensure_lt_markets().get(sig.symbol) or bot.ensure_lt_markets().get(lt_symbol(sig.symbol)),
        cfg,
        use_fresh_books=True,
    )
    execution = simulate_paper_execution(fresh_sig, args.notional, cfg)

    with STATE_LOCK:
        state = load_state()
        positions = state.get("positions", [])
        if len(positions) >= cfg["risk"]["max_open_positions"]:
            raise SystemExit("Límite de posiciones abiertas alcanzado.")
        total_notional = sum(p["notional_usd"] for p in positions)
        if total_notional + args.notional > cfg["risk"]["max_total_notional_usd"]:
            raise SystemExit("Supera el límite de notional total.")

        position = {
            "symbol": fresh_sig.symbol,
            "trade": fresh_sig.trade,
            "long_venue": fresh_sig.long_venue,
            "short_venue": fresh_sig.short_venue,
            "notional_usd": args.notional,
            "opened_at": int(time.time()),
            "hours_basis": args.hours,
            "hold_hours": bot.resolve_hold_hours(args.hours),
            "entry_hl_rate": fresh_sig.current_hl_rate,
            "entry_lt_rate": fresh_sig.current_lt_rate,
            "entry_spread": fresh_sig.current_spread,
            "expected_net_pct_hold": fresh_sig.expected_net_pct_hold,
            "expected_cost_pct_hold": fresh_sig.expected_cost_pct_hold,
            "hl_entry_slippage_bps": fresh_sig.hl_entry_slippage_bps,
            "lt_entry_slippage_bps": fresh_sig.lt_entry_slippage_bps,
            "hedged_notional_usd": execution["hedged_notional_usd"],
            "unhedged_notional_usd": execution["unhedged_notional_usd"],
            "adverse_drift_bps": execution["adverse_drift_bps"],
            "adverse_drift_usd": execution["adverse_drift_usd"],
            "execution_fee_usd": execution["execution_fee_usd"],
            "estimated_exit_fee_usd": execution["estimated_exit_fee_usd"],
            "buffer_usd": execution["buffer_usd"],
            "entry_cost_usd": execution["entry_cost_usd"],
            "expected_funding_pnl_usd": execution["expected_funding_pnl_usd"],
            "expected_hold_pnl_usd": execution["expected_hold_pnl_usd"],
            "entry_liquidity_ok": execution["entry_liquidity_ok"],
            "used_fresh_books": execution["used_fresh_books"],
            "max_leg_delay_sec": execution["max_leg_delay_sec"],
            "effective_leg_delay_sec": execution["effective_leg_delay_sec"],
            "maker_like_entry": execution["maker_like_entry"],
            "legs": execution["legs"],
            "forced": bool(args.force),
        }
        positions.append(position)
        state["positions"] = positions
        save_state(state)
    log_structured_event(
        "paper_open",
        run_id=args.run_id,
        started_at=started_at,
        status="degraded" if position.get("unhedged_notional_usd", 0) > 0 else "ok",
        event_input={"symbol": args.symbol, "hours": args.hours, "notional": args.notional, "force": bool(args.force)},
        result=position,
        warnings=["paper_open_left_unhedged_notional"] if position.get("unhedged_notional_usd", 0) > 0 else [],
    )
    print("Posición paper abierta:")
    print(json.dumps(position, indent=2))


def command_paper_close(args: argparse.Namespace, bot: Bot) -> None:
    started_at = time.time()
    with STATE_LOCK:
        state = load_state()
        positions = state.get("positions", [])
        idx = next((i for i, p in enumerate(positions) if p["symbol"] == normalize_symbol(args.symbol)), None)
        if idx is None:
            raise SystemExit("No encontré esa posición paper.")
        pos = positions[idx]
    sig = bot.inspect(pos["symbol"], args.hours)
    realized_spread_move_bps = None
    if sig and sig.current_spread is not None and pos.get("entry_spread") is not None:
        realized_spread_move_bps = pct_to_bps(sig.current_spread - pos["entry_spread"])
    elapsed_hours = max(0.0, (time.time() - pos["opened_at"]) / 3600.0)
    funded_fraction = min(1.0, elapsed_hours / max(float(pos.get("hold_hours", 24)), 1e-9))
    expected_funding_pnl_usd = float(pos.get("expected_funding_pnl_usd", 0.0)) * funded_fraction
    execution_cost_usd = float(pos.get("entry_cost_usd", 0.0))
    adverse_drift_usd = float(pos.get("adverse_drift_usd", 0.0))
    paper_realized_pnl_usd = expected_funding_pnl_usd - execution_cost_usd - adverse_drift_usd
    with STATE_LOCK:
        state = load_state()
        positions = state.get("positions", [])
        idx = next((i for i, p in enumerate(positions) if p["symbol"] == normalize_symbol(args.symbol)), None)
        if idx is None:
            raise SystemExit("La posición ya no está abierta.")
        closed = positions.pop(idx)
        state["positions"] = positions
        save_state(state)
    result_payload = {
        "symbol": closed["symbol"],
        "closed_at": int(time.time()),
        "position": closed,
        "realized_spread_move_bps": realized_spread_move_bps,
        "elapsed_hours": elapsed_hours,
        "paper_funding_pnl_usd": expected_funding_pnl_usd,
        "paper_execution_cost_usd": execution_cost_usd,
        "paper_adverse_drift_usd": adverse_drift_usd,
        "paper_realized_pnl_usd": paper_realized_pnl_usd,
    }
    log_structured_event(
        "paper_close",
        run_id=args.run_id,
        started_at=started_at,
        event_input={"symbol": args.symbol, "hours": args.hours},
        result=result_payload,
    )
    print("Posición paper cerrada:")
    print(json.dumps(closed, indent=2))
    if realized_spread_move_bps is not None:
        print(f"Movimiento de spread desde la entrada: {realized_spread_move_bps:+.2f} bps")
    print(f"Funding paper acumulado: {expected_funding_pnl_usd:+.2f} USD")
    print(f"Costos de ejecución estimados: {execution_cost_usd:+.2f} USD")
    print(f"Drift adverso estimado: {adverse_drift_usd:+.2f} USD")
    print(f"PnL paper estimado al cierre: {paper_realized_pnl_usd:+.2f} USD")


def command_pre_trade_check(args: argparse.Namespace, bot: Bot) -> None:
    started_at = time.time()
    result = bot.pre_trade_check(args.symbol, args.hours)
    log_structured_event(
        "pre_trade_check",
        run_id=args.run_id,
        started_at=started_at,
        status="ok" if result.get("go") else "degraded",
        event_input={"symbol": args.symbol, "hours": args.hours},
        result=result,
        warnings=[] if result.get("go") else ["pre_trade_check_go_false"],
    )
    print(json.dumps(result, indent=2))


def timed_check(label: str, fn: Any) -> Dict[str, Any]:
    started = time.time()
    try:
        result = fn()
        elapsed_ms = (time.time() - started) * 1000.0
        return {"label": label, "ok": True, "latency_ms": elapsed_ms, "detail": result}
    except Exception as exc:
        elapsed_ms = (time.time() - started) * 1000.0
        return {"label": label, "ok": False, "latency_ms": elapsed_ms, "detail": str(exc)}


def command_doctor(bot: Bot, env: Dict[str, str]) -> None:
    started_at = time.time()
    checks = []
    checks.append(
        timed_check(
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
    checks.append(timed_check("hl_public_meta", lambda: {"assets": len(bot.market.fetch_hl_universe())}))
    checks.append(timed_check("lt_public_markets", lambda: {"markets": len(bot.market.fetch_lt_markets())}))
    account_address = env.get("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    if account_address:
        checks.append(
            timed_check(
                "hl_private_user_state",
                lambda: {"has_state": bool(http_post_json(HL_API, {"type": "clearinghouseState", "user": account_address}))},
            )
        )
    backend_url = "http://127.0.0.1:8787/health"
    checks.append(timed_check("private_backend_health", lambda: http_get_json(backend_url, retries=0, timeout=5.0)))
    overall_ok = all(check["ok"] for check in checks)
    failed_labels = [check["label"] for check in checks if not check["ok"]]
    log_structured_event(
        "doctor",
        run_id=getattr(bot, "run_id", None),
        started_at=started_at,
        status="ok" if overall_ok else "error",
        event_input={},
        result={"ok": overall_ok, "checks": checks},
        warnings=failed_labels,
    )
    print(f"Doctor overall: {'ok' if overall_ok else 'fail'}")
    for check in checks:
        status = "ok" if check["ok"] else "fail"
        print(f"- {check['label']}: {status} ({check['latency_ms']:.1f} ms)")
        print(f"  {json.dumps(check['detail'], ensure_ascii=True)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Funding arbitrage bot MVP (paper only)")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan")
    scan.add_argument("--hours", type=int, default=168)
    scan.add_argument("--top", type=int, default=12)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("symbol")
    inspect.add_argument("--hours", type=int, default=168)

    paper_open = sub.add_parser("paper-open")
    paper_open.add_argument("symbol")
    paper_open.add_argument("--hours", type=int, default=168)
    paper_open.add_argument("--notional", type=float, default=1000.0)
    paper_open.add_argument("--force", action="store_true")

    paper_close = sub.add_parser("paper-close")
    paper_close.add_argument("symbol")
    paper_close.add_argument("--hours", type=int, default=24)

    pre_trade = sub.add_parser("pre-trade-check")
    pre_trade.add_argument("symbol")
    pre_trade.add_argument("--hours", type=int, default=24)

    prep_trade = sub.add_parser("prepare-trade")
    prep_trade.add_argument("symbol")
    prep_trade.add_argument("--hours", type=int, default=24)
    prep_trade.add_argument("--notional", type=float, default=1000.0)

    exec_trade = sub.add_parser("execute-trade")
    exec_trade.add_argument("plan_id")
    exec_trade.add_argument("--confirm", action="store_true")

    sub.add_parser("status")
    sub.add_parser("env-check")
    sub.add_parser("doctor")
    recent_events = sub.add_parser("recent-events")
    recent_events.add_argument("--limit", type=int, default=20)
    recent_runs = sub.add_parser("recent-runs")
    recent_runs.add_argument("--limit", type=int, default=10)
    recent_errors = sub.add_parser("recent-errors")
    recent_errors.add_argument("--limit", type=int, default=20)
    return parser

def command_prepare_trade(args: argparse.Namespace, bot: Bot) -> None:
    started_at = time.time()
    res = bot.prepare_trade(args.symbol, args.hours, args.notional, run_id=args.run_id)
    print(json.dumps(res, indent=2))
    log_structured_event(
        "prepare_trade",
        run_id=args.run_id,
        started_at=started_at,
        event_input={"symbol": args.symbol, "hours": args.hours, "notional": args.notional},
        result=res,
    )

def command_execute_trade(args: argparse.Namespace, bot: Bot) -> None:
    if not args.confirm:
        print("Debe pasar la flag --confirm para ejecutar operaciones con riesgo real.")
        sys.exit(1)
    started_at = time.time()
    res = bot.execute_trade(args.plan_id, run_id=args.run_id)
    print(json.dumps(res, indent=2))
    log_structured_event(
        "execute_trade",
        run_id=args.run_id,
        started_at=started_at,
        status="ok" if res.get("status") == "ok" else "degraded",
        event_input={"plan_id": args.plan_id},
        result=res,
        warnings=[] if res.get("status") == "ok" else [f"execute_trade_status_{res.get('status')}"],
    )

def command_env_check(env: Dict[str, str]) -> None:
    started_at = time.time()
    hl_addr = env.get("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    hl_pk = env.get("HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY", "")
    hl_agent = env.get("HYPERLIQUID_AGENT_WALLET_ADDRESS", "")
    lt_pub = env.get("LIGHTER_API_PUBLIC_KEY", "")
    lt_pk = env.get("LIGHTER_API_PRIVATE_KEY", "")
    lt_acc = env.get("LIGHTER_ACCOUNT_INDEX", "")
    lt_key = env.get("LIGHTER_API_KEY_INDEX", "")

    print("Entorno cargado:")
    print(f"- Hyperliquid account address: {'ok' if hl_addr else 'missing'}")
    print(f"- Hyperliquid agent wallet address: {'ok' if hl_agent else 'missing'}")
    print(f"- Hyperliquid agent wallet private key: {'ok' if hl_pk else 'missing'}")
    print(f"- Lighter API public key: {'ok' if lt_pub else 'missing'}")
    print(f"- Lighter API private key: {'ok' if lt_pk else 'missing'}")
    print(f"- Lighter account index: {'ok' if lt_acc else 'missing'}")
    print(f"- Lighter API key index: {'ok' if lt_key else 'missing'}")
    print("")
    if (hl_addr or hl_agent) and not hl_pk:
        print("Hyperliquid: hay direcciones cargadas, pero falta private key para firmar órdenes.")
    if lt_pk and (not lt_acc or not lt_key):
        print("Lighter: hay clave cargada, pero faltan account index y/o API key index para auth completa.")
    log_structured_event(
        "env_check",
        started_at=started_at,
        event_input={},
        result={
            "hyperliquid_ready": bool(hl_addr and hl_agent and hl_pk),
            "lighter_ready": bool(lt_pub and lt_pk and lt_acc and lt_key),
        },
    )


def command_recent_events(args: argparse.Namespace) -> None:
    print_recent_events(args.limit)


def command_recent_runs(args: argparse.Namespace) -> None:
    print_recent_runs(args.limit)


def command_recent_errors(args: argparse.Namespace) -> None:
    print_recent_error_logs(args.limit)


def main() -> int:
    ensure_db_initialized()
    cfg = load_config()
    env = load_env()
    parser = build_parser()
    args = parser.parse_args()
    args.run_id = new_run_id()
    bot: Optional[Bot] = None

    try:
        if args.command == "scan":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_scan(args, bot)
        elif args.command == "inspect":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_inspect(args, bot)
        elif args.command == "paper-open":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_paper_open(args, bot, cfg)
        elif args.command == "paper-close":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_paper_close(args, bot)
        elif args.command == "pre-trade-check":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_pre_trade_check(args, bot)
        elif args.command == "prepare-trade":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_prepare_trade(args, bot)
        elif args.command == "execute-trade":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_execute_trade(args, bot)
        elif args.command == "status":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_status(args, bot)
        elif args.command == "env-check":
            command_env_check(env)
        elif args.command == "doctor":
            bot = Bot(cfg, env)
            bot.run_id = args.run_id
            command_doctor(bot, env)
        elif args.command == "recent-events":
            command_recent_events(args)
        elif args.command == "recent-runs":
            command_recent_runs(args)
        elif args.command == "recent-errors":
            command_recent_errors(args)
        return 0
    except SystemExit as exc:
        if args.command not in {"recent-events", "recent-runs", "recent-errors"}:
            event_input = {k: v for k, v in vars(args).items() if k != "run_id"}
            log_structured_event(
                args.command.replace("-", "_"),
                run_id=args.run_id,
                status="error",
                event_input=event_input,
                error=str(exc),
            )
        raise
    except Exception as exc:
        event_input = {k: v for k, v in vars(args).items() if k != "run_id"}
        log_error_detail(
            source="main",
            operation=args.command.replace("-", "_"),
            error_obj=exc,
            run_id=args.run_id,
            context={"args": event_input},
        )
        log_structured_event(
            args.command.replace("-", "_"),
            run_id=args.run_id,
            status="error",
            event_input=event_input,
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    sys.exit(main())
