from urllib import error

import pytest

from funding_arb_bot import (
    classify_request_target,
    hl_symbol,
    is_rate_limited,
    lt_symbol,
    normalize_symbol,
    retry_sleep_seconds,
    should_retry,
)


def make_http_error(status_code: int, headers: dict[str, str] | None = None) -> error.HTTPError:
    return error.HTTPError(
        url="https://example.com",
        code=status_code,
        msg="boom",
        hdrs=headers or {},
        fp=None,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("btc", "BTC"),
        ("BTCUSDC", "BTC"),
        ("btc/usdt", "BTC"),
        (" pepe-perp ", "PEPE"),
        ("kshib", "SHIB"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_symbol(raw: str | None, expected: str) -> None:
    assert normalize_symbol(raw) == expected


def test_lt_symbol_uses_exchange_aliases() -> None:
    assert lt_symbol("PEPE") == "1000PEPE"
    assert lt_symbol("BTC") == "BTC"


def test_hl_symbol_uses_exchange_aliases() -> None:
    assert hl_symbol("PEPE") == "kPEPE"
    assert hl_symbol("BTC") == "BTC"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.hyperliquid.xyz/info", "hyperliquid"),
        ("https://mainnet.zklighter.elliot.ai/api/v1/order_book", "lighter"),
        ("https://example.com/health", "default"),
    ],
)
def test_classify_request_target(url: str, expected: str) -> None:
    assert classify_request_target(url) == expected


def test_should_retry_for_retryable_failures() -> None:
    assert should_retry(make_http_error(429)) is True
    assert should_retry(make_http_error(500)) is True
    assert should_retry(error.URLError("network down")) is True
    assert should_retry(TimeoutError("timeout")) is True


def test_should_retry_rejects_non_retryable_http_errors() -> None:
    assert should_retry(make_http_error(404)) is False


def test_is_rate_limited_only_for_429() -> None:
    assert is_rate_limited(make_http_error(429)) is True
    assert is_rate_limited(make_http_error(500)) is False
    assert is_rate_limited(error.URLError("network down")) is False


def test_retry_sleep_seconds_uses_retry_after_when_present() -> None:
    exc = make_http_error(429, {"Retry-After": "30"})
    assert retry_sleep_seconds(exc, attempt=0) == 20.0


def test_retry_sleep_seconds_uses_rate_limit_backoff_floor() -> None:
    exc = make_http_error(429)
    assert retry_sleep_seconds(exc, attempt=0) == pytest.approx(2.0)


def test_retry_sleep_seconds_uses_regular_backoff_for_non_rate_limits() -> None:
    exc = make_http_error(500)
    assert retry_sleep_seconds(exc, attempt=1) == pytest.approx(1.65)
