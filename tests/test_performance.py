"""db.performance_stats 지표 계산 검증."""
import db


def _seed_trades():
    conn = db._connect()
    conn.execute("DELETE FROM trades")
    rows = [
        # ts, ticker, side, price, volume, krw_amount, fee, realized_pnl, dry_run, raw
        ("2026-01-01T00:00:00", "KRW-BTC", "buy", 100.0, 1.0, 100.0, 0.05, 0.0, 1, "{}"),
        ("2026-01-02T00:00:00", "KRW-BTC", "sell", 200.0, 1.0, 200.0, 0.10, 100.0, 1, "{}"),
        ("2026-01-03T00:00:00", "KRW-BTC", "sell", 300.0, 1.0, 300.0, 0.15, 200.0, 1, "{}"),
        ("2026-01-04T00:00:00", "KRW-BTC", "sell", 50.0, 1.0, 50.0, 0.02, -50.0, 1, "{}"),
    ]
    conn.executemany(
        "INSERT INTO trades (ts, ticker, side, price, volume, krw_amount, fee, realized_pnl, dry_run, raw_result)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def test_performance_stats_metrics():
    _seed_trades()
    s = db.performance_stats()
    assert s["closed_trades"] == 3
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert round(s["win_rate"], 2) == round(2 / 3 * 100, 2)
    assert s["total_realized_pnl"] == 250.0
    assert s["best_trade"] == 200.0
    assert s["worst_trade"] == -50.0
    # 누적곡선 [100, 300, 250] → 최대낙폭 -50
    assert s["max_drawdown"] == -50.0
    # profit factor = 300 / 50 = 6
    assert s["profit_factor"] == 6.0
    assert s["buys"] == 1 and s["sells"] == 3


def test_performance_stats_empty():
    conn = db._connect()
    conn.execute("DELETE FROM trades")
    s = db.performance_stats()
    assert s["closed_trades"] == 0
    assert s["win_rate"] == 0.0
    assert s["total_realized_pnl"] == 0.0
    assert s["max_drawdown"] == 0.0
