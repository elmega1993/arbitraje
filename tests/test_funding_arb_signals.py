import pytest

from funding_arb_bot import build_pair_details, calc_signal, pair_series


BASE_CFG = {
    "scan": {
        "pair_max_gap_minutes": 10,
        "min_gross_apy": 0.0,
        "min_net_apy": 0.0,
        "min_consistency_pct": 0.0,
        "max_drawdown_bps": 10_000.0,
        "min_samples": 1,
        "hold_hours": 24,
    },
    "costs": {
        "hyperliquid_entry_bps": 1.0,
        "hyperliquid_exit_bps": 1.0,
        "lighter_entry_bps": 1.0,
        "lighter_exit_bps": 1.0,
        "estimated_exit_slippage_per_leg_bps": 0.5,
    },
    "execution": {
        "entry_buffer_bps": 1.0,
        "default_notional_usd": 1000.0,
        "max_slippage_bps": 20.0,
    },
}


def clone_cfg() -> dict:
    return {
        "scan": dict(BASE_CFG["scan"]),
        "costs": dict(BASE_CFG["costs"]),
        "execution": dict(BASE_CFG["execution"]),
    }


def make_history(values: list[float], *, start: int = 1_000, step: int = 3_600_000) -> list[dict[str, float]]:
    return [{"t": start + i * step, "v": value} for i, value in enumerate(values)]


def test_pair_series_matches_nearest_without_reusing_counterpart() -> None:
    hl_hist = [
        {"t": 1_000, "v": 0.0300},
        {"t": 2_000, "v": 0.0310},
    ]
    lt_hist = [
        {"t": 900, "v": 0.0100},
        {"t": 1_100, "v": 0.0200},
        {"t": 2_100, "v": 0.0150},
    ]

    pairs = pair_series(hl_hist, lt_hist, max_gap_ms=250)

    assert pairs == [
        {"t": 1_000, "h": 0.0300, "l": 0.0200},
        {"t": 2_000, "h": 0.0310, "l": 0.0150},
    ]


def test_pair_series_drops_points_outside_gap_limit() -> None:
    hl_hist = [{"t": 10_000, "v": 0.02}]
    lt_hist = [{"t": 11_000, "v": 0.01}]

    assert pair_series(hl_hist, lt_hist, max_gap_ms=500) == []


def test_build_pair_details_reports_consistency_drawdown_and_streaks() -> None:
    cfg = clone_cfg()
    hl_hist = make_history([0.03, 0.03, 0.01])
    lt_hist = make_history([0.01, 0.01, 0.02])

    result = build_pair_details(hl_hist, lt_hist, cfg)
    stats = result["stats"]

    assert len(result["pairs"]) == 3
    assert stats["pairs"] == 3
    assert stats["avg"] == pytest.approx(0.01)
    assert stats["avg_abs"] == pytest.approx(0.0166666667)
    assert stats["consistency_pct"] == pytest.approx(66.6666667)
    assert stats["direction"] == "hl"
    assert stats["longest_streak_hours"] == 2
    assert stats["streak_direction"] == "hl"
    assert stats["cum_pnl"] == pytest.approx([0.02, 0.04, 0.03])
    assert stats["max_drawdown_bps"] == pytest.approx(100.0)


def test_calc_signal_positive_spread_sets_expected_trade_and_passes() -> None:
    cfg = clone_cfg()
    hl_hist = make_history([0.03, 0.03, 0.01])
    lt_hist = make_history([0.01, 0.01, 0.02])

    signal = calc_signal("BTC", hl_hist, lt_hist, hold_hours=24, cfg=cfg)

    assert signal is not None
    assert signal.trade == "Long Lighter / Short HL"
    assert signal.long_venue == "Lighter"
    assert signal.short_venue == "Hyperliquid"
    assert signal.samples == 3
    assert signal.avg_spread == pytest.approx(0.01)
    assert signal.current_spread == pytest.approx(-0.01)
    assert signal.consistency_pct == pytest.approx(66.6666667)
    assert signal.max_drawdown_bps == pytest.approx(100.0)
    assert signal.expected_cost_pct_hold == pytest.approx(0.06)
    assert signal.expected_gross_pct_hold == pytest.approx(24.0)
    assert signal.expected_net_pct_hold == pytest.approx(23.94)
    assert signal.break_even_hours == pytest.approx(0.06)
    assert signal.passes is True


def test_calc_signal_negative_spread_reverses_trade_direction() -> None:
    cfg = clone_cfg()
    hl_hist = make_history([0.01, 0.01, 0.01])
    lt_hist = make_history([0.03, 0.02, 0.02])

    signal = calc_signal("ETH", hl_hist, lt_hist, hold_hours=24, cfg=cfg)

    assert signal is not None
    assert signal.trade == "Long HL / Short Lighter"
    assert signal.long_venue == "Hyperliquid"
    assert signal.short_venue == "Lighter"
    assert signal.avg_spread == pytest.approx(-0.0133333333)
    assert signal.gross_apy > 0


def test_calc_signal_returns_none_when_series_cannot_be_paired() -> None:
    cfg = clone_cfg()
    cfg["scan"]["pair_max_gap_minutes"] = 0
    hl_hist = [{"t": 1_000, "v": 0.03}]
    lt_hist = [{"t": 2_000, "v": 0.01}]

    assert calc_signal("SOL", hl_hist, lt_hist, hold_hours=24, cfg=cfg) is None


def test_calc_signal_marks_signal_as_failing_when_rules_are_not_met() -> None:
    cfg = clone_cfg()
    cfg["scan"]["min_consistency_pct"] = 90.0
    cfg["scan"]["min_samples"] = 4
    hl_hist = make_history([0.03, 0.03, 0.01])
    lt_hist = make_history([0.01, 0.01, 0.02])

    signal = calc_signal("XRP", hl_hist, lt_hist, hold_hours=24, cfg=cfg)

    assert signal is not None
    assert signal.passes is False
