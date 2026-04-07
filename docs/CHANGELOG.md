# Recent Work Log

## Unified backend and UI
- built `funding_arb_server.py` to expose `scan`, `inspect`, `status`, `pre-trade-check`, `doctor` APIs backed by `funding_arb_bot.py` logic.
- point `funding-arb (1).html` at the server, have it fallback to direct fetch only when backend is down; added new hero card, operational scanner, and confidence/action helpers.
- documented config/credentials, tightened `config.json`, and ensured backend health touches real HL/Lighter endpoints while caching/scans live in `arb_bot.db`.

## Bot, state, and paper modeling
- migrated state/cache/event log to SQLite `arb_bot.db`, deprecated `paper_state.json` and `scan_cache.json`, added `doctor` and `pre-trade-check`, and logging for every key action.
- added structured error logging (SQLite + ndjson), automatic error-card on the dashboard, CLI `recent-errors`, and `/api/errors` so rate limits and HTTP/500s are visible from the UI.
- improved signal calculations: aligned series, corrected consistency direction, tracked liquidity details, added retries/backoff, realistic slippage/VWAP, cached histories/orderbooks, and made paper entries simulate legs with retries, drift, and separate funding/cost metrics.
- adjusted CLI, strategy, and execution assumptions to use the new SQL-backed cache, fresh books for pre-trade checks, and the shared config values.

## Operational issue: BTC Net APY perception
- Observed BTC showing `Net APY -42%` which looked like a delta reversal. Investigation revealed average carry ≈-0.00061%/h with a one-off entry cost of 0.13%, so the annualized net APY becomes -42% even though the per-window loss is only ~0.12%.
- The trade direction is correct (`Long HL / Short Lighter`); the number is simply the entry cost dominating a tiny carry. The confusion arises because we annualize a one-time cost and present it as APY.
- The relevant output lives in `funding- arb (1).html` hero/strategy cards and `funding_arb_bot.py`'s `Signal.net_est_apy`; this will need clearer labeling or a split between `Hold %` and `Entry Cost`.

## Next steps documented
- Continue aligning the UI to the server APIs, expose `status` + `pre-trade-check` results, and clean up the legacy frontend logic.
- Later work: refine `Net APY` messaging to separate hold profit from entry costs and expose `funding_arb_server.py` data through `private_backend.py` as needed.

## Architectural Stabilization & Core Protection (Recent Updates)
- Mitigated Lighter Exchange HTTP 429 (Rate Limit) errors:
  - Transferred `lt_public_markets` API payload logic across the Doctor checks to memory cache (`bot.ensure_lt_markets`) rather than live-pulling.
  - Adjusted `private_backend.py` `/health` route to verify environment presence instead of calling rate-limited `get_account()` continuously.
- Implemented global `Exception` handling via middleware on `funding_arb_server.py`, trapping unhandled traces and dumping them securely to `bot_app.log` for auditable traces and diagnostics (`tail -f bot_app.log`).
- Secured cross-domain wallet fetches: `loadBalances()` now transparently calls into Private Backend's REST space to compute real `lt_equity` and integrate into Glassmorphism UI.
- Consolidated legacy JSON documents and removed bloat; created `ARCHITECTURE.md` to supply Institutional-grade flow comprehension.
