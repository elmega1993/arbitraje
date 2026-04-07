#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

import lighter
from eth_account import Account
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from hyperliquid.info import Info

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
HL_BASE_URL = "https://api.hyperliquid.xyz"
LT_BASE_URL = "https://mainnet.zklighter.elliot.ai"


def load_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            env[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())
    for key in (
        "HYPERLIQUID_ACCOUNT_ADDRESS",
        "HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY",
        "HYPERLIQUID_AGENT_WALLET_ADDRESS",
        "LIGHTER_API_PUBLIC_KEY",
        "LIGHTER_API_PRIVATE_KEY",
        "LIGHTER_ACCOUNT_INDEX",
        "LIGHTER_API_KEY_INDEX",
        "LIGHTER_L1_ADDRESS",
        "LIGHTER_WALLET_ADDRESS",
    ):
        if os.getenv(key):
            env[key] = os.getenv(key, "")
    return env


class PrivateClients:
    def __init__(self, env: Dict[str, str]):
        self.env = env
        self.hl_info = Info(base_url=HL_BASE_URL, skip_ws=True)

    def hl_summary(self) -> Dict[str, Any]:
        account_address = self.env["HYPERLIQUID_ACCOUNT_ADDRESS"]
        agent_private_key = self.env["HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY"]
        agent_wallet = Account.from_key(agent_private_key)
        state = self.hl_info.user_state(account_address)
        open_orders = self.hl_info.open_orders(account_address)
        fills = self.hl_info.user_fills(account_address)
        return {
            "account_address": account_address,
            "agent_wallet_address": agent_wallet.address,
            "margin_summary": state.get("marginSummary", {}),
            "cross_margin_summary": state.get("crossMarginSummary", {}),
            "withdrawable": state.get("withdrawable"),
            "asset_positions": state.get("assetPositions", []),
            "open_orders": open_orders,
            "recent_fills_count": len(fills),
            "recent_fills_preview": fills[:10],
        }

    async def lt_summary(self) -> Dict[str, Any]:
        account_index = int(self.env["LIGHTER_ACCOUNT_INDEX"])
        api_key_index = int(self.env["LIGHTER_API_KEY_INDEX"])
        api_private_key = self.env["LIGHTER_API_PRIVATE_KEY"]
        l1_address = self.env.get("LIGHTER_L1_ADDRESS") or self.env.get("LIGHTER_WALLET_ADDRESS") or ""

        client = lighter.ApiClient(lighter.Configuration(host=LT_BASE_URL))
        account_api = lighter.AccountApi(client)
        signer = lighter.SignerClient(
            url=LT_BASE_URL,
            api_private_keys={api_key_index: api_private_key},
            account_index=account_index,
        )
        try:
            account = await account_api.account(by="index", value=str(account_index))
            sub_accounts = None
            if l1_address:
                sub_accounts = await account_api.accounts_by_l1_address(l1_address=l1_address)
            token, err = signer.create_auth_token_with_expiry(deadline=3600, api_key_index=api_key_index)
            now = int(time.time())
            pnl = await account_api.pnl(
                by="index",
                value=str(account_index),
                resolution="1d",
                start_timestamp=now - 7 * 86400,
                end_timestamp=now,
                count_back=7,
                auth=token,
            )
            funding = await account_api.position_funding(
                account_index=account_index,
                limit=10,
                auth=token,
            )
            return {
                "account_index": account_index,
                "api_key_index": api_key_index,
                "auth_token_ready": err is None and bool(token),
                "account": json.loads(account.model_dump_json()),
                "sub_accounts": json.loads(sub_accounts.model_dump_json()) if sub_accounts else None,
                "pnl_7d": json.loads(pnl.model_dump_json()),
                "position_funding": json.loads(funding.model_dump_json()),
            }
        finally:
            await client.close()
            await signer.close()


env = load_env()
app = FastAPI(title="Funding Arb Private Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:8787", "http://localhost:8787"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
clients = PrivateClients(env)


@app.get("/health")
async def health() -> Dict[str, Any]:
    required = {
        "hyperliquid_account_address": bool(env.get("HYPERLIQUID_ACCOUNT_ADDRESS")),
        "hyperliquid_agent_wallet_address": bool(env.get("HYPERLIQUID_AGENT_WALLET_ADDRESS")),
        "hyperliquid_agent_wallet_private_key": bool(env.get("HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY")),
        "lighter_api_public_key": bool(env.get("LIGHTER_API_PUBLIC_KEY")),
        "lighter_api_private_key": bool(env.get("LIGHTER_API_PRIVATE_KEY")),
        "lighter_account_index": bool(env.get("LIGHTER_ACCOUNT_INDEX")),
        "lighter_api_key_index": bool(env.get("LIGHTER_API_KEY_INDEX")),
    }
    checks: Dict[str, Any] = {"env": required}
    ok = all(required.values())
    return {"ok": ok, "checks": checks}


@app.get("/api/private/hyperliquid")
async def hyperliquid_account() -> Dict[str, Any]:
    return clients.hl_summary()


@app.get("/api/private/lighter")
async def lighter_account() -> Dict[str, Any]:
    return await clients.lt_summary()


@app.get("/api/private/summary")
async def private_summary() -> Dict[str, Any]:
    lighter_data = await clients.lt_summary()
    hyperliquid_data = clients.hl_summary()
    return {
        "hyperliquid": hyperliquid_data,
        "lighter": lighter_data,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787)
