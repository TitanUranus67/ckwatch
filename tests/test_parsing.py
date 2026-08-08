import pytest

from app.parsing import (
    parse_hashrate,
    parse_log_line,
    parse_log_timestamp,
    parse_pool_status,
    parse_user_status,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("135M", 135e6),
        ("5.82T", 5.82e12),
        ("912G", 912e9),
        ("0", 0.0),
        ("34.2G", 34.2e9),
        ("7.77T", 7.77e12),
        ("1.05T", 1.05e12),
        ("4.32M", 4.32e6),
        (None, 0.0),
        ("garbage", 0.0),
        (123.0, 123.0),
    ],
)
def test_parse_hashrate(raw, expected):
    assert parse_hashrate(raw) == pytest.approx(expected)


def test_parse_pool_status(fixture_pool_status_text):
    pool = parse_pool_status(fixture_pool_status_text)
    assert pool["Users"] == 3
    assert pool["hashrate1d"] == "8.84T"
    assert pool["accepted"] == 50619919063
    assert pool["bestshare"] == 67040371318
    assert "SPS1h" in pool


def test_parse_user_status(fixture_user_text):
    user = parse_user_status(fixture_user_text)
    assert user["shares"] == 6986467080
    assert user["bestever"] == 6432394954
    assert len(user["worker"]) == 1
    assert user["worker"][0]["workername"] == "bitaxe-gamma01"


def test_parse_log_timestamp():
    # matches "lastupdate": 1772110306 on the [2026-02-26 12:51:46] pool line
    assert parse_log_timestamp("2026-02-26 12:52:45") == 1772110365


def test_parse_log_line_user():
    line = ('[2026-02-26 12:52:45.996] User bitaxe-gamma01:'
            '{"hashrate1m":"4.32M","shares":4067967080,"bestever":6432394954}')
    ev = parse_log_line(line)
    assert ev["kind"] == "user"
    assert ev["name"] == "bitaxe-gamma01"
    assert ev["ts"] == 1772110365
    assert ev["data"]["hashrate1m"] == "4.32M"


def test_parse_log_line_pool():
    line = ('[2026-02-26 12:51:46.992] Pool:'
            '{"runtime": 1, "lastupdate": 1772110306, "Users": 0}')
    ev = parse_log_line(line)
    assert ev["kind"] == "pool"
    assert ev["data"]["runtime"] == 1


def test_parse_log_line_block():
    line = ("[2026-03-05 10:15:22.001] Solved and confirmed block 881234"
            " by user bitaxe-gamma01 worker bitaxe-gamma01")
    ev = parse_log_line(line)
    assert ev["kind"] == "block"
    assert ev["ts"] is not None
    assert "881234" in ev["text"]


def test_parse_log_line_ignores_noise():
    assert parse_log_line("[2026-02-26 12:51:45.669] ckpool stratifier starting") is None
    assert parse_log_line("random garbage") is None
    assert parse_log_line("[2026-02-26 12:52:45.996] User bob:{bad json") is None
