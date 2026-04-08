# OKX Quant Module Scaffold

This workspace now includes the execution layer, scan layer, multi-strategy engines, and the AI learning / weekly review scaffold.

Included now:

- OKX forced market order flow
- TP / SL protection order flow
- leverage and margin based sizing flow
- market scan flow
- multi-strategy registry scaffold
- per-strategy runtime isolation and symbol lock
- detailed trade journal and strategy learning store
- weekly OpenAI review sync scaffold
- AI-generated strategy slot scaffold

Main files:

- `app.py`
- `dashboard_service.py`
- `okx_force_order.py`
- `okx_scanner.py`
- `strategy_registry.py`
- `strategy_trend_hma_std.py`
- `strategy_trend_hma_std_engine.py`
- `strategy_larry_breakout_cmo.py`
- `strategy_larry_breakout_cmo_engine.py`
- `strategy_bollinger_width_4h.py`
- `strategy_bollinger_width_4h_engine.py`
- `strategy_ma_breakout_4h.py`
- `strategy_ma_breakout_4h_engine.py`
- `strategy_burst_sma_channel_1h.py`
- `strategy_burst_sma_channel_1h_engine.py`
- `strategy_naked_k_reversal_1h.py`
- `strategy_naked_k_reversal_1h_engine.py`
- `strategy_mean_reversion_atr_2h_daily.py`
- `strategy_mean_reversion_atr_2h_daily_engine.py`
- `strategy_dual_sma_pullback_2h.py`
- `strategy_dual_sma_pullback_2h_engine.py`
- `strategy_ai_generated_meta.py`
- `strategy_ai_generated_meta_engine.py`
- `learning_store.py`
- `openai_learning_sync.py`
- `strategy_orchestrator.py`
- `strategy_runtime_state.py`

Install:

```bash
pip install -r requirements.txt
```

Environment variables:

```bash
OKX_API_KEY=...
OKX_SECRET=...
OKX_PASSWORD=...
OPENAI_API_KEY=...
OPENAI_WEEKLY_MODEL=gpt-4.1-mini
```

Dashboard and deployment:

- `app.py` is the web entry for the Chinese monitoring dashboard.
- `dashboard_service.py` prepares the balance, strategy status, AI learning summary, recent trades, and Chinese logs for the UI.
- The dashboard template is at `templates/index.html`.
- UI assets are at `static/css/dashboard.css`, `static/js/dashboard.js`, and `static/images/detective-bg.svg`.
- If you want to replace the default detective-style background with your own image, put your file at `static/images/conan-custom.jpg`.
- `zbpack.json` is included so Zeabur can use `app.py` as the Python entrypoint.
- `.gitignore` ignores local runtime folders such as `data/`, `state/`, and `logs/`.

Zeabur deploy flow:

1. Push this repository to GitHub.
2. In Zeabur, create a new service from GitHub.
3. Select this repository.
4. Set the environment variables:
   `OKX_API_KEY`
   `OKX_SECRET`
   `OKX_PASSWORD`
   `OPENAI_API_KEY`
   `OPENAI_WEEKLY_MODEL`
5. Deploy. Zeabur should use `app.py` through `zbpack.json`.

Dashboard UI shows:

- 交易所總餘額、可用資金、已用資金、單策略平均分配
- 各策略目前是否持倉 / 等待觸發 / 空倉
- 各策略目前商品、方向、進場價、現價
- 各策略目前 TP / SL
- 停損與停利是否有移動
- 各策略累積損益、持倉浮盈虧、手續費、交易筆數
- AI 學習回報、最近一週 OpenAI 建議、AI 策略格狀態
- 近期成交明細
- 中文服務日誌

SQLite learning database:

- Database file: `data/ai_learning.sqlite3`
- The learning layer still keeps local JSON compatibility, but it now syncs the same core data into SQLite for API access.
- Synced tables include:
  `open_trade_records`
  `closed_trades`
  `strategy_rollups`
  `symbol_stats`
  `weekly_reviews`
  `ai_strategy_profiles`
  `learning_meta`

Learning API endpoints:

- `GET /api/learning/overview`
- `GET /api/learning/trades`
  optional query: `strategy_id`, `limit`
- `GET /api/learning/rollups`
- `GET /api/learning/symbol-stats`
  optional query: `strategy_id`
- `GET /api/learning/reviews`
  optional query: `limit`
- `GET /api/learning/ai-profile`

Execution functions:

- `create_okx_exchange()` creates the OKX ccxt client.
- `set_symbol_leverage()` sets leverage before order placement.
- `compute_order_size()` converts equity, margin percent, leverage, and stop distance into quantity.
- `build_forced_order_plan()` builds the final order plan from account equity.
- `force_market_order()` sends the market order.
- `ensure_exchange_protection()` places TP / SL protection orders.
- `force_open_with_tp_sl()` opens first, then attaches TP / SL.
- `force_open_with_sl_only()` opens first, then attaches only stop loss protection.
- `force_close_position()` closes the position with `reduceOnly`.
- `replace_protection_orders()` cancels the old protection orders and rebuilds them.
- `replace_stop_loss_only()` cancels the old protection orders and rebuilds only the stop loss.
- `get_position_snapshot()` reads the exchange position for a symbol and side.

Scan functions:

- `fetch_scan_universe()` finds the top USDT swap symbols by volume.
- `build_symbol_snapshot()` fetches ticker and OHLCV for one symbol.
- `scan_market()` loops through the scan universe and builds snapshots.

Strategy registry:

- `strategy_registry.py` is the contract layer for future strategies.
- Each strategy should declare its own timezone, scan interval, required data, and decision inputs.
- This is where we will keep strategies separate so they do not mix data requirements.
- Each strategy can hold only one active position at a time. If a strategy already has an open position, new signals for that strategy are blocked until the position is cleared.
- Different strategies are not allowed to open the same symbol at the same time.
- A strategy-level pending entry also locks that symbol from every other strategy.
- Capital is split by total strategy count from total account equity, not by remaining balance after previous strategies open trades.
- If a strategy has an active position, it stops scanning the full market and only manages that held symbol.

AI learning system:

- Every new strategy position is recorded into `data/ai_learning_state.json` and every closed trade is appended to `data/trade_journal.jsonl`.
- The journal stores per-trade details such as strategy id, source strategy id, symbol, side, timeframe, leverage, used margin, fees, entry price, exit price, hold time, RR, PnL, entry indicator snapshot, and entry reason notes.
- Learning rollups are kept separately by strategy and by symbol.
- Manual strategies only store OpenAI suggestions; they do not auto-edit themselves.
- Once every enabled human strategy has at least 30 closed trades, the system creates the AI strategy slot `ai_generated_meta_v1`.
- The AI strategy can receive a weekly OpenAI patch and auto-apply it to its own profile only.
- Weekly sync is designed to run on Sunday UTC and sends a compact summary instead of raw trade-by-trade noise.

Learning files:

- `data/ai_learning_state.json`: structured learning state, rollups, open trade records, weekly review history, and AI strategy profile
- `data/trade_journal.jsonl`: append-only closed-trade journal
- `state/strategy_runtime_state.json`: active positions and pending entries

Implemented strategy 1:

- File: `strategy_trend_hma_std.py`
- Logic: 4H trend-following using HMA(9) and Standard Deviation(9)
- Long setup: latest closed 4H candle crosses above HMA and closes above HMA
- Short setup: latest closed 4H candle crosses below HMA and closes below HMA
- Entry: next 4H candle open
- SL: fixed from key candle stddev * 2
- TP: dynamic from latest closed candle stddev * 3, rebased from entry price
- Time filter: no new entries from Sunday 00:00 UTC to Monday 00:00 UTC
- Ranking: first by RR, then by learned win rate
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 1 execution engine:

- File: `strategy_trend_hma_std_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it will only manage that position
- If there is no active position, it scans, ranks, and opens only the best candidate
- After entry, it persists the active strategy position into `state/strategy_runtime_state.json`
- On each later cycle, it syncs the exchange position, clears the strategy lock if the trade is closed, and updates TP when a new 4H candle has closed

Implemented strategy 2:

- File: `strategy_larry_breakout_cmo.py`
- Logic: breakout trend strategy using reconstructed Larry-style breakout lines plus Chande Momentum Oscillator
- Timeframes: 2H and 4H
- Breakout lines:
  `PH = 2 * HLC3 - Low`
  `PL = 2 * HLC3 - High`
  `green line = highest(PH, 40) + one price tick`
  `red line = lowest(PL, 40) - one price tick`
- Long setup: current live price breaks the previous completed green line
- Short setup: current live price breaks the previous completed red line
- Entry: immediate market entry on live breakout approximation
- Initial SL: current opposite line
- Trailing exit: opposite line touch
- Momentum exit: ChandeMO length 30, next bar open style approximation
- Ranking: proxy RR first, then learned win rate
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 2 execution engine:

- File: `strategy_larry_breakout_cmo_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it will only manage that position
- If there is no active position, it scans 2H and 4H candidates, ranks them, and opens only the best candidate
- After entry, it persists the active strategy position into `state/strategy_runtime_state.json`
- On each later cycle, it syncs the exchange position, updates the stop line, checks line-touch exits, and checks ChandeMO momentum exits

Implemented strategy 3:

- File: `strategy_bollinger_width_4h.py`
- Logic: 4H Bollinger breakout with BBW filter
- Yellow band: length 25, stddev 2.5, close source
- Blue band: length 25, stddev 3.75, close source
- BBW filter: length 25, stddev 2.5, threshold `> 0.01`
- Long setup: closed candle close strictly above yellow upper band and BBW strictly above threshold
- Short setup: closed candle close strictly below yellow lower band and BBW strictly above threshold
- Entry: next 4H candle open only
- During holding:
  stop = previous closed yellow basis
  take = previous closed blue outer band
- Exit priority: stop first if the same candle touches both stop and take
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 3 execution engine:

- File: `strategy_bollinger_width_4h_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it will only manage that position
- If there is no active position, it scans 4H candidates, ranks them, and opens only the best candidate
- After entry, it persists the active strategy position into `state/strategy_runtime_state.json`
- On each later cycle, it syncs the exchange position, updates the stop from the previous closed yellow basis, and evaluates stop/take using the current candle high/low with stop-first priority

Implemented strategy 4:

- File: `strategy_ma_breakout_4h.py`
- Logic: 4H MA2 / MA30 environment filter with 16-bar breakout stop-entry
- Long preparation zone: current MA2 strictly below current MA30
- Short preparation zone: current MA2 strictly above current MA30
- Long trigger: previous 16 completed candles highest high
- Short trigger: previous 16 completed candles lowest low
- Entry workflow: the best setup is armed as a pending entry and then monitored on that single symbol until it either breaks out or the MA invalidates
- Pending cancel rule:
  long pending is canceled if current MA2 becomes strictly greater than current MA30 before breakout
  short pending is canceled if current MA2 becomes strictly smaller than current MA30 before breakout
- After entry: MA is ignored and only the opposite 16-bar breakout level controls exit
- Ranking: actionable triggered setups first, then RR proxy, then learned win rate
- Single-position rule: this strategy can hold only one active trade or one pending entry at a time

Strategy 4 execution engine:

- File: `strategy_ma_breakout_4h_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it only manages that symbol
- If the strategy has a pending entry, it stops scanning and only monitors that symbol
- If there is no active position and no pending entry, it scans 4H candidates, ranks them, and arms only the best setup
- If the pending trigger is touched, it immediately enters by market order and attaches a stop loss at the current opposite 16-bar breakout level
- On each later cycle, it syncs the exchange position and updates the stop from the latest opposite 16-bar breakout level

Implemented strategy 5:

- File: `strategy_burst_sma_channel_1h.py`
- Logic: 1H burst reversal with SMA60 direction plus 25/150/250 bar channels
- Trend filter:
  long bias when the latest closed SMA60 is higher than the prior closed SMA60
  short bias when the latest closed SMA60 is lower than the prior closed SMA60
- Long setup: latest closed candle closes below the prior 25-bar lowest low
- Short setup: latest closed candle closes above the prior 25-bar highest high
- Entry: next 1H candle open only
- Fixed stop:
  long uses the prior 150-bar lowest low
  short uses the prior 150-bar highest high
- Dynamic take:
  long references the prior 250-bar highest high
  short references the prior 250-bar lowest low
- TP execution:
  long exits at the next bar open after a closed candle breaks above the referenced 250-bar high
  short exits at the next bar open after a closed candle breaks below the referenced 250-bar low
- Ranking: RR first, then learned win rate
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 5 execution engine:

- File: `strategy_burst_sma_channel_1h_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it only manages that symbol
- If there is no active position, it scans 1H candidates, ranks them, and opens only the best candidate
- During holding, the fixed stop stays unchanged, while the dynamic TP reference updates from the latest prior 250-bar channel
- Stop uses live high/low touch logic; TP uses closed-candle breakout confirmation and exits on the next-open approximation

Implemented strategy 6:

- File: `strategy_naked_k_reversal_1h.py`
- Logic: 1H previous-day breakout plus 3-bar engulfing reversal
- Daily filter:
  long setup requires the signal candle low to break the previous UTC day low
  short setup requires the signal candle high to break the previous UTC day high
- Long engulfing:
  bar 1 bullish and close1 > high2
  bar 2 bearish and close2 < close3
  bar 3 bearish
- Short engulfing:
  bar 1 bearish and close1 < low2
  bar 2 bullish and close2 > close3
  bar 3 bullish
- Entry: next 1H candle open only
- Stop: fixed 6 percent from entry
- Profit activation: after price first reaches plus or minus 2 percent
- Profit exit:
  long exits on next open after 3 consecutive bearish closed candles
  short exits on next open after 3 consecutive bullish closed candles
- Reversal:
  after the position has crossed into a new UTC day, an opposite engulfing signal can close and reverse the trade on the next-open approximation
- Ranking: RR floor first, then learned win rate
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 6 execution engine:

- File: `strategy_naked_k_reversal_1h_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it only manages that symbol
- If there is no active position, it scans 1H candidates, ranks them, and opens only the best candidate
- Stop uses live high/low touch logic
- 2 percent profit only arms the trailing logic; it does not immediately close the position
- Opposite engulfing after day rollover can force a close-and-reverse sequence

Implemented strategy 7:

- File: `strategy_mean_reversion_atr_2h_daily.py`
- Logic: previous daily range anchor plus 2H three-bar reversal confirmation
- Large timeframe anchor:
  use the previous completed UTC daily high and low only
- Long setup:
  one of the latest three closed 2H bars has a wick or close touching the previous daily low
  the latest closed 2H candle closes above the previous 2H candle high
- Short setup:
  one of the latest three closed 2H bars has a wick or close touching the previous daily high
  the latest closed 2H candle closes below the previous 2H candle low
- Entry: next 2H candle open only
- Stop:
  long uses key candle low minus ATR(14, RMA) times 4
  short uses key candle high plus ATR(14, RMA) times 4
- Take profit: fixed 3R from the entry and stop distance
- Reversal filter: if an opposite valid signal appears while holding, the system closes and reverses on the next 2H open approximation
- Ranking: RR first, then learned win rate
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 7 execution engine:

- File: `strategy_mean_reversion_atr_2h_daily_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it only manages that symbol
- If there is no active position, it scans cross-timeframe candidates, ranks them, and opens only the best candidate
- Fixed TP and SL are attached at entry
- If an opposite signal appears first, the engine closes the old position and immediately opens the reverse position

Implemented strategy 8:

- File: `strategy_dual_sma_pullback_2h.py`
- Logic: 2H trend pullback using SMA13 and SMA59
- Trend:
  long-only when SMA13 > SMA59
  short-only when SMA13 < SMA59
- Long setup:
  latest closed candle close is below SMA13
  key candle close is still above SMA59 by more than 3.5 percent of the key candle close
- Short setup:
  latest closed candle close is above SMA13
  key candle close is still below SMA59 by more than 3.5 percent of the key candle close
- Entry: next 2H candle open only
- Stop: fixed at the key candle SMA59
- Take profit:
  long uses `(key close - SMA59) * 0.3 + key close`
  short uses `key close - (SMA59 - key close) * 0.3`
- Ranking: RR first, then learned win rate
- Single-position rule: this strategy can hold only one active trade at a time

Strategy 8 execution engine:

- File: `strategy_dual_sma_pullback_2h_engine.py`
- `run_cycle()` is the full loop for this strategy
- If the strategy already has an active position, it only manages that symbol
- If there is no active position, it scans 2H candidates, ranks them, and opens only the best candidate
- Fixed TP and SL are attached immediately on entry

Important execution note:

- Exchange OHLCV can get very close to TradingView indicator behavior, but it is not guaranteed to be perfectly identical if TradingView uses a different symbol feed, session setting, or broker source.
- Because of that, signals are calculated only from fully closed candles to reduce mismatch and false entries.
- Strategy 2 uses live intrabar breakout approximation. This is executable, but it is still an approximation of TradingView stop-entry behavior, not an exact tick-by-tick replay.
- Strategy 2 adaptive parameter is intentionally kept neutral at `0` because the full original main indicator source was not provided. I did not invent a fake formula for it.
- Strategy 4 also uses live intrabar breakout approximation, but it is implemented as a local pending-entry workflow. This makes the MA-invalidates-then-cancel rule enforceable without letting the system retroactively "regret" a breakout that already touched the trigger.
- Strategy 5 uses the standard TradingView SMA formula from the provided indicator file, but the phrase "SMA60 continuously rising/falling" is implemented as a strict bar-to-bar slope check on closed SMA values. If you later want a stricter multi-bar definition, it can be tightened without changing the rest of the strategy scaffold.
- Strategy 6 does not need a TradingView custom indicator file because it is pure candle-structure logic, but the exact meaning of "broke yesterday high/low" was ambiguous in prose. I implemented it with candle wick highs/lows instead of close-only so it better matches your touch-top / touch-bottom reversal description.
- Strategy 7 reconstructs ATR from the provided TradingView source with the default `ATR(14, RMA)` settings because you gave the official indicator file but did not request a different ATR length or smoothing option.
- Strategy 8 reuses the standard TradingView SMA formula from the same SMA indicator source, but it intentionally stays closed-candle and next-open based. That means it avoids future peeking, at the cost of not trying to simulate any intrabar discretionary pullback interpretation.
- Position sizing is capped by stop-distance risk and a conservative safe-leverage check so a wide stop does not consume the full allocated strategy capital and push the position toward liquidation.

Example order usage:

```python
from okx_force_order import create_okx_exchange, force_open_with_tp_sl

exchange = create_okx_exchange()

result = force_open_with_tp_sl(
    exchange=exchange,
    symbol="BTC-USDT-SWAP",
    side="buy",
    qty=None,
    stop_loss_price=82000,
    take_profit_price=86000,
    leverage=10,
    td_mode="cross",
    margin_pct=0.04,
    risk_pct=0.01,
)
```

Example scan usage:

```python
from okx_force_order import create_okx_exchange
from okx_scanner import scan_market

exchange = create_okx_exchange()
rows = scan_market(exchange=exchange, limit=20, timeframe="15m", candles=120)
```

Example strategy 1 full-cycle usage:

```python
from okx_force_order import create_okx_exchange
from strategy_trend_hma_std_engine import TrendStrategyConfig, run_cycle

exchange = create_okx_exchange()
config = TrendStrategyConfig(
    leverage=10,
    td_mode="cross",
    margin_pct=0.04,
    risk_pct=0.01,
    universe_limit=70,
)

result = run_cycle(exchange=exchange, config=config)
print(result)
```

Example strategy 2 full-cycle usage:

```python
from okx_force_order import create_okx_exchange
from strategy_larry_breakout_cmo_engine import LarryStrategyConfig, run_cycle

exchange = create_okx_exchange()
config = LarryStrategyConfig(
    leverage=10,
    td_mode="cross",
    margin_pct=0.04,
    risk_pct=0.01,
    universe_limit=70,
    length=40,
    momentum_length=30,
    adaptive=0.0,
    scaling_factor=0.1,
)

result = run_cycle(exchange=exchange, config=config)
print(result)
```

Example orchestrator usage:

```python
from okx_force_order import create_okx_exchange
from strategy_orchestrator import OrchestratorConfig, run_all_strategies

exchange = create_okx_exchange()
result = run_all_strategies(exchange=exchange, config=OrchestratorConfig())
print(result)
```

Weekly OpenAI review flow:

- `strategy_orchestrator.py` calls `run_weekly_ai_learning_cycle()` after the strategy cycles complete.
- On Sunday UTC, the system builds a 7-day compact summary from the closed-trade journal.
- That summary is sent through `openai_learning_sync.py`.
- OpenAI suggestions are stored in the weekly review record and are not auto-applied to manual strategies.
- If the AI-generated strategy profile is enabled and `auto_apply_suggestions` is true, its profile patch can be applied automatically.

Important runtime note:

- The AI learning and weekly OpenAI review code is implemented, but actual OpenAI requests still require a valid `OPENAI_API_KEY`, installed dependencies, and successful runtime verification in your environment.
- Live OKX execution still requires `ccxt`, exchange credentials, and real exchange-side testing.
