"""Recurring worker, pause, and single-cycle coordination tests."""
from __future__ import annotations

import threading
import time

import config
import main
from state import store


def _stub_runtime(monkeypatch):
    monkeypatch.setattr(config, "validate", lambda: None)
    monkeypatch.setattr(config, "INTERVAL_SECONDS", 1)
    monkeypatch.setattr(main, "UpbitBroker", lambda: object())
    monkeypatch.setattr(main, "DecisionAgent", lambda: object())
    monkeypatch.setattr(main, "RiskManager", lambda: object())


def test_trading_loop_repeats_until_stopped(monkeypatch):
    _stub_runtime(monkeypatch)
    stop = threading.Event()
    calls = []

    def fake_run_once(*_args):
        calls.append(time.monotonic())
        if len(calls) == 2:
            stop.set()

    monkeypatch.setattr(main, "run_once", fake_run_once)
    store.set_paused(False)
    main.trading_loop(stop)

    assert len(calls) == 2
    assert store.snapshot()["loop_running"] is False


def test_pause_blocks_cycle_until_resume(monkeypatch):
    _stub_runtime(monkeypatch)
    stop = threading.Event()
    called = threading.Event()

    def fake_run_once(*_args):
        called.set()
        stop.set()

    monkeypatch.setattr(main, "run_once", fake_run_once)
    store.set_paused(True)
    worker = threading.Thread(target=main.trading_loop, args=(stop,), daemon=True)
    worker.start()
    time.sleep(0.15)
    assert not called.is_set()

    store.set_paused(False)
    worker.join(timeout=2)
    assert called.is_set()
    assert not worker.is_alive()


def test_cycle_reservation_prevents_overlap():
    assert main.reserve_cycle() is True
    try:
        assert main.reserve_cycle() is False
    finally:
        main.release_cycle_reservation()
