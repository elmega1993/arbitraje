import requests

BASE_URL = "http://127.0.0.1:8790"
DEFAULT_TIMEOUT = 15
SCAN_TIMEOUT = 45

def get_json(url, timeout=DEFAULT_TIMEOUT):
    resp = requests.get(f"{BASE_URL}{url}", timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def post_json(url, data, timeout=DEFAULT_TIMEOUT):
    resp = requests.post(f"{BASE_URL}{url}", json=data, timeout=timeout)
    return resp


def pick_prepare_symbol():
    res = get_json("/api/scan?hours=24&top=1", timeout=SCAN_TIMEOUT)
    results = res.get("results", [])
    if not results:
        raise AssertionError("No hay oportunidades para probar prepare-trade")
    return results[0]["symbol"]

def main():
    print("Running Smoke Tests...")
    original_kill_switch = False
    try:
        # 1. Health
        res = get_json("/health")
        assert res.get("ok") is True, "Healthcheck falló"
        print("✅ /health OK")

        # 2. Assets
        res = get_json("/api/assets")
        assert "assets" in res and len(res["assets"]) > 0, "No assets list"
        print("✅ /api/assets OK")

        # 3. Kill Switch GET (Default)
        res = get_json("/api/kill-switch")
        assert "active" in res, "No kill-switch flag"
        original_kill_switch = bool(res.get("active"))
        print(f"✅ /api/kill-switch status: {res.get('active')}")

        # 4. Toggle kill switch on
        res = post_json("/api/kill-switch", {"active": True})
        assert res.status_code == 200, "Falló activando kill switch"
        assert res.json().get("active") is True
        print("✅ /api/kill-switch ON OK")

        # 5. Prepare trade against a current passing opportunity.
        symbol = pick_prepare_symbol()
        res = post_json("/api/prepare-trade", {"symbol": symbol, "hours": 24, "notional": 1000.0})
        assert res.status_code == 200, f"/api/prepare-trade falló para {symbol}: {res.text}"
        plan_id = res.json()["data"]["plan_id"]
        print(f"✅ /api/prepare-trade OK para {symbol}. Plan ID: {plan_id}")

        # 6. Execute trade with kill switch enabled (Should fail)
        res_exec = post_json("/api/execute-trade", {"plan_id": plan_id, "confirm": True})
        assert res_exec.status_code == 400, "Ejecución debió fallar por kill switch"
        assert "Kill switch" in res_exec.json().get("detail", ""), "Mensaje equivocado de kill switch"
        print("✅ /api/execute-trade aborto por kill switch verificado.")
    finally:
        post_json("/api/kill-switch", {"active": original_kill_switch})

    print("✅ Smoke tests terminados.")

if __name__ == "__main__":
    main()
