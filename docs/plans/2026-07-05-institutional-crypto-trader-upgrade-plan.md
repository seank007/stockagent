# Institutional Crypto Trader Upgrade Plan

> For Hermes: use subagent-driven-development skill to implement this plan task-by-task.

Goal: Turn stockagent into a stronger hybrid trading stack that combines deterministic quant ranking, event/news signals, and strict execution/risk controls for Upbit KRW spot markets.

Architecture: Keep final trade approval deterministic. Use quant_engine as the core scoring/risk layer, add structured event/news features as overlays, and let AI models extract semantic features only in bounded schemas. Execution, sizing, risk caps, and kill switches remain rule-based.

Tech Stack: Python, pyupbit, stockagent local DB, Hermes cron, structured JSON outputs, unittest.

---

## Current State Summary

Already implemented:
- KRW market-wide scan via trade_cli.py
- quant_engine with long_score / exit_score / regime / target_weight_pct / trim_pct / risk_flags
- RiskManager enforcement of eligible_buy, max_buy_krw, trim_pct
- Hermes trading prompt aligned to quant constraints
- main.py loop now injects quant_plan into decision/risk flow

Key gap areas:
- Event/news signals are not yet first-class structured features
- main.py still loops over config.TICKERS instead of a dynamic liquid universe
- No regime-aware universe shrink/expand policy
- No correlation clustering or portfolio-level risk budgeting beyond per-name caps
- No dedicated research pipeline for model/vendor comparison and semantic factor calibration

---

## Task 1: Add failing tests for structured event/news overlay

Objective: Define the contract for event/news features before implementation.

Files:
- Modify: tests/test_quant_engine.py
- Create: tests/test_event_overlay.py

Step 1: Write failing tests
- Add tests for:
  - listing_event increases event score only when source is official and pre-runup is not excessive
  - exploit/hack event blocks buy eligibility and raises exit pressure
  - large token unlock plus exchange inflow reduces priority
  - news_focus only activates on high-liquidity, high-novelty candidates

Step 2: Run test to verify failure
Run:
- python3 -m unittest tests/test_event_overlay.py
Expected:
- FAIL because event overlay module/functions do not exist yet

Step 3: Minimal implementation later in Task 2

Step 4: Re-run and pass after implementation

---

## Task 2: Create structured event overlay engine

Objective: Implement deterministic event/news scoring that can be fused into quant_engine.

Files:
- Create: event_overlay.py
- Modify: quant_engine.py

Step 1: Create event_overlay.py with pure functions
Required outputs per ticker:
- event_bias_score
- event_risk_score
- event_type
- source_confidence
- novelty_score
- time_decay_score
- event_summary

Step 2: Supported event taxonomy
Start with:
- listing
- delisting
- exploit
- withdrawal_pause
- token_unlock
- regulatory_negative
- regulatory_positive
- partnership_major

Step 3: Quant fusion rules
- positive official listing + low pre-runup => raise execution_priority
- exploit / withdrawal pause => hard negative overlay
- token unlock + exchange inflow => lower eligible_buy / raise exit_score
- high novelty, high source confidence, high liquidity => news_focus true

Step 4: Verification
Run:
- python3 -m unittest tests/test_event_overlay.py
- python3 -m unittest tests/test_quant_engine.py
Expected:
- PASS

---

## Task 3: Build liquid dynamic universe selection

Objective: Replace fixed-ticker loop with a dynamic liquid universe so the core runtime can act like the Hermes scan path.

Files:
- Modify: main.py
- Modify: config.py
- Optionally create: universe.py

Step 1: Add universe selector
Rules:
- start from KRW markets only
- keep top N by 24h traded value
- exclude obvious unusable/duplicate assets if needed
- always include currently held assets even if they fall out of top N

Step 2: Add config knobs
Suggested env variables:
- DYNAMIC_UNIVERSE_ENABLED=true
- UNIVERSE_TOP_N=40
- UNIVERSE_MIN_TRADE_VALUE_KRW=...

Step 3: Inject into main.py
- when enabled, main.py should evaluate selected liquid universe instead of config.TICKERS only

Step 4: Verification
Run:
- /Users/seankim/.venvs/stockagent/bin/python main.py
Expected:
- runtime logs show dynamic universe size and selected tickers

---

## Task 4: Add portfolio-level risk budgeting and correlation clustering

Objective: Prevent the engine from acting like multiple separate single-name traders.

Files:
- Modify: quant_engine.py
- Create: portfolio_risk.py
- Add tests in: tests/test_portfolio_risk.py

Step 1: Write failing tests
Cases:
- two highly correlated candidates should not both receive full weight
- a held overweight name should reduce room for same-cluster peers
- total target gross should shrink in risk-off regime

Step 2: Implement minimal clustering/risk budget logic
- simple rolling-return correlation proxy
- cluster cap
- portfolio gross cap
- risk-off gross multiplier

Step 3: Verify
Run:
- python3 -m unittest tests/test_portfolio_risk.py
Expected:
- PASS

---

## Task 5: Add market regime engine

Objective: Use broader market state to modulate aggressiveness.

Files:
- Create: regime_engine.py
- Modify: quant_engine.py
- Modify: main.py

Step 1: Regime inputs
- BTC trend
- ETH trend
- breadth of top liquid KRW names
- realized volatility / dispersion

Step 2: Regime outputs
- risk_on
- neutral
- risk_off

Step 3: Policy rules
- risk_on: allow broader buying, normal gross exposure
- neutral: tighter eligible_buy threshold
- risk_off: lower max_buy_krw globally, raise trim sensitivity, possibly pause new longs

Step 4: Verification
- Add unit tests for each regime state
- Confirm scan output includes market_regime

---

## Task 6: Add structured semantic AI layer

Objective: Use AI models for extraction, not direct free-form trading decisions.

Files:
- Modify: agent/decision.py
- Create: agent/semantic_features.py
- Add tests: tests/test_semantic_features.py

Step 1: Define strict schema outputs
Fields:
- event_type
- sentiment_direction
- novelty_score
- confidence
- affected_tickers
- horizon
- evidence_snippets

Step 2: Restrict model role
- AI cannot override risk limits
- AI can only emit structured features for later fusion

Step 3: Add provider comparison hooks
- record provider name and latency
- compare output consistency across providers in shadow mode

Step 4: Verification
- Unit tests for parser / schema fallback
- Simulated prompt test to ensure invalid outputs degrade safely

---

## Task 7: Add research loop for model/vendor comparison

Objective: Continuously learn from model performance without giving models uncontrolled authority.

Files:
- Create: research/model_eval_notes.md
- Create: scripts/eval_semantic_models.py
- Optionally add Hermes cron later

Step 1: Compare models on fixed tasks
- event extraction accuracy
- latency
- schema compliance
- hallucination rate

Step 2: Save structured results locally
- timestamp
- provider/model
- pass/fail by metric

Step 3: Use only the best semantic extractor for production
- keep others in shadow mode until proven better

---

## Task 8: Add execution realism and safeguards

Objective: Improve real PnL retention.

Files:
- Modify: scripts/trade_cli.py
- Modify: brokers/upbit.py
- Create tests where possible

Step 1: Add soft execution filters
- spread sanity filter if available
- skip extremely low-liquidity moments
- avoid oversizing relative to recent volume

Step 2: Add drift-band rebalance logic
- no trade if deviation from target is too small

Step 3: Verification
- scan output should show when a trade is blocked for execution reasons

---

## Task 9: Backtest and walk-forward the fused stack

Objective: Stop adding complexity without proving value.

Files:
- Modify: backtest.py
- Create: scripts/walkforward_quant_eval.py

Step 1: Add fused-feature backtest mode
- quant only
- quant + event overlay
- quant + event + regime

Step 2: Add evaluation metrics
- CAGR or total return
- MDD
- turnover
- hit rate
- average slippage assumption
- profit factor

Step 3: Verification
Run examples:
- python3 backtest.py KRW-BTC --interval minute60 --count 500
- python3 scripts/walkforward_quant_eval.py

---

## Task 10: Surface the new fields in dashboard/API

Objective: Make the operator able to see why the engine is acting.

Files:
- Modify: web.py
- Modify: state.py

Show:
- market_regime
- execution_priority
- news_focus
- event_summary
- target_weight_pct
- current_weight_pct
- trim_pct
- max_buy_krw
- risk_flags

Verification:
- confirm localhost dashboard shows these fields for live candidates

---

## Execution Order Recommendation

1. Event overlay tests + module
2. Dynamic liquid universe in main.py
3. Regime engine
4. Portfolio clustering/risk budgeting
5. Semantic AI feature extraction
6. Execution realism
7. Backtesting/walk-forward
8. Dashboard surfacing

---

## Non-Negotiable Safety Rules

- No AI model may override deterministic max_buy_krw or risk-off halt rules.
- No free-form natural-language model output may be used directly as an order instruction.
- Every new feature must have a failing test first.
- Every semantic feature must degrade safely to neutral/blocked if extraction fails.

---

## Verification Checklist

- [ ] Unit tests exist for every new engine component
- [ ] main.py can run with dynamic universe enabled
- [ ] scan output includes event/regime/priority fields
- [ ] RiskManager enforces quant/event caps even when AI says BUY
- [ ] Backtest supports fused strategy modes
- [ ] Dashboard shows operator-facing reasons for every action
