import json
from pathlib import Path

from fastapi.testclient import TestClient

import funding_arb_server as server
from funding_arb_bot import Signal


def make_signal(symbol: str = "BTC") -> Signal:
    return Signal(
        symbol=symbol,
        samples=3,
        current_hl_rate=0.03,
        current_lt_rate=0.01,
        current_spread=0.02,
        avg_spread=0.02,
        avg_abs_spread=0.02,
        gross_apy=17520.0,
        signed_apy=17520.0,
        consistency_pct=100.0,
        longest_streak_hours=3,
        max_drawdown_bps=0.0,
        trade="Long Lighter / Short HL",
        long_venue="Lighter",
        short_venue="Hyperliquid",
        expected_cost_pct_hold=0.05,
        expected_gross_pct_hold=48.0,
        expected_net_pct_hold=47.95,
        net_est_apy=17519.95,
        passes=True,
    )


class FakeBot:
    def __init__(self) -> None:
        self.last_scan_errors = [{"symbol": "BAD", "error": "failed"}]

    def common_assets(self) -> list[str]:
        return ["BTC", "ETH"]

    def scan(self, hours: int, top: int) -> list[Signal]:
        assert hours == 24
        assert top == 2
        return [make_signal("BTC"), make_signal("ETH")]


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(server.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "funding-arb-server"}


def test_errors_endpoint_uses_fetch_recent_error_logs(monkeypatch) -> None:
    client = TestClient(server.app)

    def fake_fetch(limit: int) -> list[dict]:
        assert limit == 7
        return [{"id": 1, "error_type": "HTTPError"}]

    monkeypatch.setattr(server, "fetch_recent_error_logs", fake_fetch)

    response = client.get("/api/errors?limit=7")

    assert response.status_code == 200
    assert response.json() == {"errors": [{"id": 1, "error_type": "HTTPError"}]}


def test_scan_endpoint_serializes_signal_objects(monkeypatch) -> None:
    client = TestClient(server.app)
    monkeypatch.setattr(server, "bot", FakeBot())

    response = client.get("/api/scan?hours=24&top=2")

    assert response.status_code == 200
    payload = response.json()
    assert [row["symbol"] for row in payload["results"]] == ["BTC", "ETH"]
    assert payload["errors"] == [{"symbol": "BAD", "error": "failed"}]


def test_kill_switch_post_updates_cfg_and_writes_file(monkeypatch, tmp_path: Path) -> None:
    client = TestClient(server.app)
    tmp_config = tmp_path / "config.json"
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_config)
    monkeypatch.setattr(server, "cfg", {"risk": {"kill_switch_active": False}})

    response = client.post("/api/kill-switch", json={"active": True})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "active": True}
    assert server.cfg["risk"]["kill_switch_active"] is True
    assert json.loads(tmp_config.read_text())["risk"]["kill_switch_active"] is True


def test_paper_open_rejection_returns_400_and_logs_event(monkeypatch) -> None:
    client = TestClient(server.app)
    logged: list[dict] = []

    def fake_command_paper_open(*args, **kwargs) -> None:
        raise SystemExit("paper open blocked")

    def fake_log_structured_event(event_type: str, **payload) -> None:
        logged.append({"event_type": event_type, **payload})

    monkeypatch.setattr(server, "command_paper_open", fake_command_paper_open)
    monkeypatch.setattr(server, "log_structured_event", fake_log_structured_event)
    monkeypatch.setattr(server, "new_run_id", lambda: "run-test")

    response = client.post(
        "/api/paper/open",
        json={"symbol": "BTC", "hours": 24, "notional": 1000.0, "force": False},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "paper open blocked"
    assert logged[0]["event_type"] == "paper_open"
    assert logged[0]["status"] == "error"
    assert logged[0]["error"] == "paper open blocked"


def test_prepare_trade_success_returns_payload_and_logs_event(monkeypatch) -> None:
    client = TestClient(server.app)
    logged: list[dict] = []

    def fake_prepare_trade(symbol: str, hours: int, notional: float, run_id: str) -> dict:
        assert symbol == "BTC"
        assert hours == 24
        assert notional == 500.0
        assert run_id == "run-prepare"
        return {"plan_id": "plan-123", "status": "ok", "symbol": symbol}

    def fake_log_structured_event(event_type: str, **payload) -> None:
        logged.append({"event_type": event_type, **payload})

    monkeypatch.setattr(server.bot, "prepare_trade", fake_prepare_trade)
    monkeypatch.setattr(server, "log_structured_event", fake_log_structured_event)
    monkeypatch.setattr(server, "new_run_id", lambda: "run-prepare")

    response = client.post(
        "/api/prepare-trade",
        json={"symbol": "BTC", "hours": 24, "notional": 500.0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {"plan_id": "plan-123", "status": "ok", "symbol": "BTC"},
    }
    assert logged[0]["event_type"] == "prepare_trade"
    assert logged[0]["status"] == "ok"
    assert logged[0]["result"]["plan_id"] == "plan-123"


def test_prepare_trade_rejection_returns_400_and_logs_event(monkeypatch) -> None:
    client = TestClient(server.app)
    logged: list[dict] = []

    def fake_prepare_trade(symbol: str, hours: int, notional: float, run_id: str) -> dict:
        raise SystemExit("trade rejected")

    def fake_log_structured_event(event_type: str, **payload) -> None:
        logged.append({"event_type": event_type, **payload})

    monkeypatch.setattr(server.bot, "prepare_trade", fake_prepare_trade)
    monkeypatch.setattr(server, "log_structured_event", fake_log_structured_event)
    monkeypatch.setattr(server, "new_run_id", lambda: "run-prepare-reject")

    response = client.post(
        "/api/prepare-trade",
        json={"symbol": "BTC", "hours": 24, "notional": 500.0},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "trade rejected"
    assert logged[0]["event_type"] == "prepare_trade_rejected"
    assert logged[0]["status"] == "error"
    assert logged[0]["error"] == "trade rejected"


def test_execute_trade_requires_explicit_confirm_flag() -> None:
    client = TestClient(server.app)

    response = client.post("/api/execute-trade", json={"plan_id": "plan-123", "confirm": False})

    assert response.status_code == 400
    assert response.json()["detail"] == "Must confirm with confirm=True"


def test_execute_trade_success_logs_ok_status(monkeypatch) -> None:
    client = TestClient(server.app)
    logged: list[dict] = []

    def fake_execute_trade(plan_id: str, run_id: str) -> dict:
        assert plan_id == "plan-123"
        assert run_id == "run-execute"
        return {"status": "ok", "execution_id": "exec-1"}

    def fake_log_structured_event(event_type: str, **payload) -> None:
        logged.append({"event_type": event_type, **payload})

    monkeypatch.setattr(server.bot, "execute_trade", fake_execute_trade)
    monkeypatch.setattr(server, "log_structured_event", fake_log_structured_event)
    monkeypatch.setattr(server, "new_run_id", lambda: "run-execute")

    response = client.post("/api/execute-trade", json={"plan_id": "plan-123", "confirm": True})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "data": {"status": "ok", "execution_id": "exec-1"}}
    assert logged[0]["event_type"] == "execute_trade"
    assert logged[0]["status"] == "ok"
    assert logged[0]["warnings"] == []


def test_execute_trade_degraded_status_logs_warning(monkeypatch) -> None:
    client = TestClient(server.app)
    logged: list[dict] = []

    def fake_execute_trade(plan_id: str, run_id: str) -> dict:
        return {"status": "partial_fill", "execution_id": "exec-2"}

    def fake_log_structured_event(event_type: str, **payload) -> None:
        logged.append({"event_type": event_type, **payload})

    monkeypatch.setattr(server.bot, "execute_trade", fake_execute_trade)
    monkeypatch.setattr(server, "log_structured_event", fake_log_structured_event)
    monkeypatch.setattr(server, "new_run_id", lambda: "run-execute-degraded")

    response = client.post("/api/execute-trade", json={"plan_id": "plan-123", "confirm": True})

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "partial_fill"
    assert logged[0]["event_type"] == "execute_trade"
    assert logged[0]["status"] == "degraded"
    assert logged[0]["warnings"] == ["execute_trade_status_partial_fill"]


def test_execute_trade_rejection_returns_400_and_logs_event(monkeypatch) -> None:
    client = TestClient(server.app)
    logged: list[dict] = []

    def fake_execute_trade(plan_id: str, run_id: str) -> dict:
        raise SystemExit("kill switch active")

    def fake_log_structured_event(event_type: str, **payload) -> None:
        logged.append({"event_type": event_type, **payload})

    monkeypatch.setattr(server.bot, "execute_trade", fake_execute_trade)
    monkeypatch.setattr(server, "log_structured_event", fake_log_structured_event)
    monkeypatch.setattr(server, "new_run_id", lambda: "run-execute-reject")

    response = client.post("/api/execute-trade", json={"plan_id": "plan-123", "confirm": True})

    assert response.status_code == 400
    assert response.json()["detail"] == "kill switch active"
    assert logged[0]["event_type"] == "execute_trade_rejected"
    assert logged[0]["status"] == "error"
    assert logged[0]["error"] == "kill switch active"
