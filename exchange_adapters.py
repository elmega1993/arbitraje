import hashlib
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

import lighter
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants


class BaseAdapter(ABC):
    @abstractmethod
    def __init__(self, env: Dict[str, str], cfg: Dict[str, Any]):
        pass

    @abstractmethod
    def place_market_order(self, symbol: str, side: str, size_usd: float, slippage_bps: float, plan_id: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Executes a market order with the specified slippage tolerance.
        Returns (success, fill_details)
        """
        pass

    @abstractmethod
    def cancel_all(self, symbol: str) -> bool:
        pass


HL_SYMBOL_ALIAS = {"PEPE": "kPEPE", "SHIB": "kSHIB", "BONK": "kBONK", "FLOKI": "kFLOKI"}

class HyperliquidAdapter(BaseAdapter):
    def __init__(self, env: Dict[str, str], cfg: Dict[str, Any]):
        self.env = env
        self.cfg = cfg
        # We need the agent wallet private key for Exchange
        private_key = env.get("HYPERLIQUID_AGENT_WALLET_PRIVATE_KEY", "")
        # HL SDK uses `Account.from_key` but it expects the standard format, which it handles internally
        if private_key:
            # Note: you need to use the actual secret key for the Exchange.
            wallet = Account.from_key(private_key)
            # Use mainnet API
            self.exchange = Exchange(wallet, constants.MAINNET_API_URL)
        else:
            self.exchange = None
        # Cache szDecimals per asset
        self._sz_decimals: Dict[str, int] = {}

    def _get_sz_decimals(self, symbol: str) -> int:
        """Fetch and cache szDecimals for the given asset from HL meta."""
        if symbol in self._sz_decimals:
            return self._sz_decimals[symbol]
        try:
            meta = self.exchange.info.meta()
            for u in meta.get("universe", []):
                name = u.get("name", "")
                self._sz_decimals[name] = int(u.get("szDecimals", 4))
            return self._sz_decimals.get(symbol, 4)
        except Exception:
            return 4

    def place_market_order(self, symbol: str, side: str, size_usd: float, slippage_bps: float, plan_id: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        if not self.exchange:
            return False, {"error": "Missing HL private key"}
            
        try:
            hl_sym = HL_SYMBOL_ALIAS.get(symbol.upper(), symbol)
            # Get szDecimals for proper rounding (H5 fix)
            sz_decimals = self._get_sz_decimals(hl_sym)

            l2 = self.exchange.info.l2_snapshot(hl_sym)
            if not l2 or not l2.get("levels"):
                return False, {"error": "Could not fetch L2 book for pricing"}
                
            # Estimate mid price
            bids = l2["levels"][0]
            asks = l2["levels"][1]
            if not bids or not asks:
                return False, {"error": "Empty L2 book"}
                
            best_bid = float(bids[0]["px"])
            best_ask = float(asks[0]["px"])
            mid = (best_bid + best_ask) / 2.0
            
            # Round to the asset's szDecimals (H5: was hardcoded to 4)
            sz = round(size_usd / mid, sz_decimals)
            is_buy = side.lower() == "buy" or side.lower() == "long"
            
            # Slippage is expected as a float (0.01 = 1%)
            slippage_pct = slippage_bps / 10000.0
            
            res = self.exchange.market_open(hl_sym, is_buy, sz, None, slippage_pct)
            
            if res and res.get("status") == "ok":
                statuses = res.get("response", {}).get("data", {}).get("statuses", [])
                if statuses and "filled" in statuses[0]:
                    filled_data = statuses[0]["filled"]
                    return True, {"filled_sz": float(filled_data["totalSz"]), "avg_px": float(filled_data["avgPx"])}
                else:
                    return False, {"error": "Order placed but not immediately filled", "raw": statuses}
            else:
                return False, {"error": "Order failed", "raw": res}
        except Exception as e:
            return False, {"error": str(e)}

    def cancel_all(self, symbol: str) -> bool:
        """Cancel all open orders for a symbol. (H2: fixed invalid positional arg)"""
        if not self.exchange:
            return False
        try:
            hl_sym = HL_SYMBOL_ALIAS.get(symbol.upper(), symbol)
            # HL SDK: use cancel_all_orders and filter by coin, or use the
            # open_orders + cancel approach. The simplest safe path:
            open_orders = self.exchange.info.open_orders(self.env.get("HYPERLIQUID_ACCOUNT_ADDRESS", ""))
            oids_to_cancel = [o["oid"] for o in open_orders if o.get("coin") == hl_sym]
            if not oids_to_cancel:
                return True  # nothing to cancel
            for oid in oids_to_cancel:
                self.exchange.cancel(hl_sym, oid)
            return True
        except Exception:
            return False


def _run_async_in_thread(coro):
    """Run an async coroutine from sync code, even when an event loop is already running (H3 fix)."""
    import asyncio
    result = [None]
    error = [None]

    def _run():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result[0] = loop.run_until_complete(coro)
            loop.close()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=30)
    if error[0]:
        raise error[0]
    return result[0]


class LighterAdapter(BaseAdapter):
    def __init__(self, env: Dict[str, str], cfg: Dict[str, Any]):
        self.env = env
        self.cfg = cfg
        self.api_key_index = int(env.get("LIGHTER_API_KEY_INDEX", 0))
        self.api_private_key = env.get("LIGHTER_API_PRIVATE_KEY", "")
        self.account_index = int(env.get("LIGHTER_ACCOUNT_INDEX", 0))
        self.base_url = "https://mainnet.zklighter.elliot.ai"
        
        if self.api_private_key:
            self.signer = lighter.SignerClient(
                url=self.base_url,
                api_private_keys={self.api_key_index: self.api_private_key},
                account_index=self.account_index
            )
            # Need an ApiClient to fetch current price
            self.client = lighter.ApiClient(lighter.Configuration(host=self.base_url))
            self.orderbook_api = lighter.OrderBookApi(self.client)
            self.market_api = lighter.MarketApi(self.client)
        else:
            self.signer = None
        # Cache market_id lookups
        self._market_id_cache: Dict[str, int] = {}

    def _get_market_id(self, symbol: str) -> Optional[int]:
        """Get market_id for a symbol. Uses cache + async-in-thread to avoid event loop conflict (H3 fix)."""
        if symbol in self._market_id_cache:
            return self._market_id_cache[symbol]
        try:
            if not hasattr(self, "_sz_decimals_cache"):
                self._sz_decimals_cache = {}
            markets_resp = _run_async_in_thread(self.market_api.markets())
            import json as _json
            import math
            markets = _json.loads(markets_resp.model_dump_json())
            for m in markets:
                msym = m.get("symbol", "")
                mid = m.get("market_id")
                # H5 / N5 fix: Derive sz decimals
                sz_dec = 4
                resol = m.get("sizeResolution") or m.get("stepSize")
                if resol:
                    sz_dec = max(0, int(-math.log10(float(resol))))
                else:
                    mult = float(m.get("baseMultiplier", 10000))
                    sz_dec = int(math.log10(mult)) if mult > 0 else 4
                    
                # Cache all market_ids and decimals for future lookups
                self._market_id_cache[msym] = mid
                self._sz_decimals_cache[msym] = sz_dec
                
                if msym == symbol or msym == f"{symbol}_USDC" or msym.replace("1000", "") == symbol:
                    self._market_id_cache[symbol] = mid
                    self._sz_decimals_cache[symbol] = sz_dec
                    
            return self._market_id_cache.get(symbol)
        except Exception:
            pass
        return None

    @staticmethod
    def _derive_client_order_index(plan_id: Optional[str] = None) -> int:
        """Derive a unique client_order_index from plan_id (H1 fix). Falls back to timestamp-based."""
        if plan_id:
            return int(hashlib.sha1(plan_id.encode()).hexdigest()[:8], 16) % 10**9
        return int(time.time() * 1000) % 10**9

    def place_market_order(self, symbol: str, side: str, size_usd: float, slippage_bps: float, plan_id: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        if not self.signer:
            return False, {"error": "Missing Lighter private key"}
        
        try:
            market_id = self._get_market_id(symbol)
            if market_id is None:
                return False, {"error": f"Market ID not found for {symbol}"}
                
            # Fetch L2 to calculate token size accurately (H3: uses thread-safe async)
            ob_resp = _run_async_in_thread(self.orderbook_api.orderbook(market_index=market_id))
            import json as _json
            ob = _json.loads(ob_resp.model_dump_json())
            
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if not bids or not asks:
                return False, {"error": "Empty orderbook"}
                
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            mid = (best_bid + best_ask) / 2.0
            
            is_ask = side.lower() == "sell" or side.lower() == "short"
            sz_dec = getattr(self, "_sz_decimals_cache", {}).get(symbol, 4)
            sz = round(size_usd / mid, sz_dec)
            base_amount_str = f"{sz:.{sz_dec}f}"
            
            slippage_pct = slippage_bps / 10000.0
            # H1 fix: derive unique client_order_index from plan_id
            client_order_index = self._derive_client_order_index(plan_id)
            
            order, res, err = self.signer.create_market_order_if_slippage(
                market_index=market_id,
                client_order_index=client_order_index,
                base_amount=base_amount_str,
                max_slippage=str(slippage_pct),
                is_ask=is_ask,
                reduce_only=False,
                api_key_index=self.api_key_index
            )
            
            if err:
                return False, {"error": err}
            
            # success
            return True, {"order_id": order.order_id if hasattr(order, 'order_id') else str(client_order_index)}
        except Exception as e:
            return False, {"error": str(e)}

    def cancel_all(self, symbol: str) -> bool:
        if not self.signer:
            return False
        try:
            market_id = self._get_market_id(symbol)
            if market_id is None: return False
            _, _, err = self.signer.cancel_all_orders(market_index=market_id, api_key_index=self.api_key_index)
            return err is None
        except Exception:
            return False


class PaperAdapter(BaseAdapter):
    def __init__(self, env: Dict[str, str], cfg: Dict[str, Any]):
        self.env = env
        self.cfg = cfg

    def place_market_order(self, symbol: str, side: str, size_usd: float, slippage_bps: float, plan_id: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        # Mock execution based on slippage
        import random
        # Just pretend we fill 100% at the requested size. 
        # (Slippage and pricing handled higher up in pre-trade check)
        time.sleep(random.uniform(0.1, 0.4))
        return True, {"filled_sz": None, "avg_px": None, "note": "Paper filled successfully"}

    def cancel_all(self, symbol: str) -> bool:
        return True


def get_adapter(venue: str, env: Dict[str, str], cfg: Dict[str, Any], mode: str = "paper") -> BaseAdapter:
    if mode == "paper":
        return PaperAdapter(env, cfg)
        
    venue_lower = venue.lower()
    if venue_lower in ("hl", "hyperliquid"):
        return HyperliquidAdapter(env, cfg)
    elif venue_lower in ("lt", "lighter"):
        return LighterAdapter(env, cfg)
    else:
        raise ValueError(f"Unknown venue {venue}")
