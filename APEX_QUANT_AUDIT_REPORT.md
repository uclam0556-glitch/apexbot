# APEX FULL QUANT AUDIT — 2026-06-11

Аудит проведён по коду (152 py-файла, ~27k строк), экспорту продакшн-БД
(`shadow_trades_database.csv`, 10 741 сделка за 2026-06-07…06-08), `diagnostics_result.json`
и git-истории. Живой PostgreSQL находится на Railway и с этой машины недоступен
(`DATABASE_URL` локально не задан) — все цифры ниже взяты из последнего полного экспорта.

---

## 1. Executive Summary

**Система в текущем виде не торгует и не может торговать.** Это не вопрос качества
сигналов — это цепочка из 7 механических багов P0, из-за которых:

1. Execution-путь (LIMIT/MARKET) **мёртв** — каждый одобренный сигнал падает с
   `NameError` (две неопределённые переменные в main.py), исключение глотается.
2. Новый «institutional» ExitEngine **мёртв** — TypeError на первом же тике
   (читает dict как float), поэтому partial TP / break-even / trailing / momentum-decay
   **никогда не срабатывали**.
3. Закрытые сделки **навсегда остаются `OPEN` в `signals`** (рассинхрон outcome/status)
   → ExposureManager видит фантомные позиции → `Max slots reached (1/1)` → блокирует всё.
4. Pullback-трекер и missed-signals-трекер **закомментированы** — лимитные сетки
   создаются и никогда не исполняются.
5. Таблицы `filter_audit`, `missed_signals`, `system_health`, `smc_events`, `ohlcv`
   **никогда не пишутся** — аналитика фильтров, которой вы доверяете, физически не существует.

**Хорошая новость:** данные показывают, что edge у системы есть. Средний MFE закрытых
сделок +2.44% при MAE −1.56%. Проблема не в направлении, а в выходах: медианный TP1
стоит на +4.0…+5.2%, а медианный MFE — +2.4…+2.9%. До TP доезжают лишь 12% сделок,
а 84% сделок, показавших ≥1% профита, закрылись не в WON. Симуляция на этих же 7 991
закрытых сделках: простой partial-exit (50% на +1.5%, BE, runner до +3%) даёт
**+0.48%/сделку вместо фактических −0.44%** (по старой логике учёта). Это разница
между убыточной и прибыльной системой *на тех же самых сигналах*.

---

## 2. Current APEX Architecture (как есть)

```
main.py (монолит 2 649 строк) — ApexSystem
 ├─ run_trading_pipeline()        скан 30 монет каждые 5 мин (MEXC spot, ccxt)
 │   ├─ regime (HMM по BTC 1h)  ├─ breadth (50 монет vs SMA200 1d, кэш 1ч)
 │   ├─ ~15 фильтров/пенальти   ├─ V7 score 0-100 + dynamic gate
 │   ├─ APPROVED → create_shadow_trade (signals.is_shadow=FALSE, status='OPEN')
 │   ├─ BLOCKED  → shadow_trades_blocked (counterfactual)
 │   └─ execution path (RiskEngine SL/TP, Kelly, OrderRouter) — ❌ МЁРТВ (NameError)
 ├─ background_trade_tracker()    15s — ЕДИНСТВЕННЫЙ работающий exit-механизм
 ├─ ExitEngine (10s)              ❌ МЁРТВ (TypeError на live_prices)
 ├─ ShadowTradeMonitor            ❌ НИГДЕ НЕ ЗАПУСКАЕТСЯ
 ├─ background_pullback_tracker   ❌ ЗАКОММЕНТИРОВАН (main.py:2579)
 ├─ background_missed_signals     ❌ ЗАКОММЕНТИРОВАН (main.py:2578)
 ├─ FastAPI dashboard (port 8080) + aiogram Telegram bot
 └─ PostgreSQL (Railway): signals, shadow_trades, shadow_trades_blocked,
    pullback_watchlist (+ 5 таблиц-сирот, в которые никто не пишет)
```

Деплой: Railway (Procfile), `live_trading_enabled=false`, paper-режим, депозит $3 000.

---

## 3. What Analyses Exist Now

| Анализ | Где | Статус |
|---|---|---|
| Multi-TF trend (1d/4h/1h/15m/5m, weighted votes) | `services/engine/mtf_engine.py` | ✅ работает, используется как пенальти/бонус |
| HMM regime classifier (BTC) | `services/intelligence/ml_regime.py` | ✅ работает, переобучается на старте |
| Market breadth (% монет > «EMA200») | main.py:599 | ⚠️ работает, но это SMA, не EMA |
| RSI, ATR, EMA ribbon, VWAP, Bollinger, Fibonacci, RSI-divergence | `services/indicators/technical.py` + inline в main.py | ✅ |
| SMC: swing points, BOS/CHOCH, FVG, liquidity sweeps, volume nodes | `services/engine/smc_core.py` (903 строки) | ✅ считается; sweeps>40 трактуется как chop |
| CVD (по 5m свечам, прокси) | `services/intelligence/cvd_engine.py` | ⚠️ это candle-based прокси, не реальный orderflow |
| OFI / orderbook imbalance (стакан 20 уровней) | `services/intelligence/ofi_engine.py` | ✅ |
| Funding rate, OI, Long/Short ratio (Binance futures API) | `services/api/binance_futures.py` + funding/lsr engines | ✅ для скоринга шортов |
| RS-матрица (относительная сила, top-30 префильтр) | `services/intelligence/rs_matrix.py` | ✅ |
| Доминация BTC / ротация в альты (7d return diff) | main.py:633 | ✅ |
| Fear&Greed, BTC dominance (внешние API) | `services/indicators/market_data.py` | ✅ |
| Liquidation cascade detector | `services/engine/liquidation_detector.py` | ✅ |
| Confluence V4 (SHAP-веса по режимам) | `services/engine/confluence_v4.py` | ⚠️ Redis на Railway нет → всегда seeded-веса, «обучение» не работает |
| ML confidence / isotonic calibration | — | ❌ НЕ СУЩЕСТВУЕТ (mock, и именно его вызов роняет пайплайн) |
| Adversarial tester | `services/adversarial/tester.py` | ⚠️ вызывается с mock-стаканом и mock-спуфингом — декорация |
| Макрокалендарь blackout | `services/data/macro_calendar.py` | ✅ |

---

## 4. Signal Pipeline Map (фактический порядок, main.py:671–1990)

```
RS top-30 → cascade check → fetch 5 TF → DataValidator → cooldown → exhaustion(>5%/4h)
→ volume gate (<25% baseline) → CVD → OFI/spread → MTF score → DirectionSelector
→ StrategyRouter (regime→TREND/MEAN_REVERSION/—) → momentum exhaustion (hard/penalty)
→ absorption trap → data health → z-score gravity → SMC + indicators + market context
→ ConfluenceV4 (0-10) → ultra_score×10 = V7 → пенальти (overextension, chop, momentum,
  macro RSI, data health) + бонусы (MTF, dominance flow, A+ setup +35, intraday alpha +12)
→ dynamic gate (base 45-60 по режиму, ±breadth/p95) 
→ APPROVED: create_shadow_trade(proxy SL=1.5-2.2×ATR, TP1/2/3=1.5R/2.5R/4R) + Telegram
→ [дальше всё мёртвое: adversarial → slots → RiskEngine.calculate_sl_tp (structural TP)
   → Kelly → ❌ NameError isotonic_win_prob (main.py:1782) — сюда выполнение не доходит]
```

**Критическое следствие:** paper-сделки, по которым вы собираете статистику, живут на
**прокси-SL/TP из ATR**, а не на структурных SL/TP RiskEngine, которые вы считаете
рабочими. Вы тестируете не ту систему, которую построили.

## 5. Filter Pipeline Map + оценка каждого фильтра

Данные: 7 991 закрытая counterfactual-сделка (блокированные сигналы, отслеженные до исхода).
E[pnl] — что фильтр «спас» (отрицательное = правильно блокировал).

| Фильтр | Где | Блокировал | WON% | LOST% | E[pnl] | Вердикт |
|---|---|---|---|---|---|---|
| Momentum Exhaustion | main.py:1010 | 112 | 9.4% | 77.1% | **−1.19%** | 🏆 ЛУЧШИЙ. Оставить, можно ужесточить |
| Data Health / Validator | validator.py | 2 339 | 10.3% | 44.7% | −0.75% | ✅ Сильный. Оставить |
| V7 Score gate | dynamic_gate | 2 675 | 3.1% | 37.2% | −0.82% | ✅ Сильный. Оставить, калибровать порог |
| MTF Gate (старый hard) | удалён в v11 | 5 615 | 15.6% | 36.9% | −0.20% | ⚠️ Слабый/шумный — правильно, что заменили на пенальти. В bucket mtf=−1.0 win-rate блокированных был 50% |
| Cooldown 4h | timescaledb.py:451 | 0 | — | — | — | ❌ МЁРТВ: ищет status IN ('ACCEPTED','ENTERED'), а реальные статусы 'OPEN'/'BLOCKED' |
| Exhaustion >5%/4h | main.py:815 | n/a | — | — | — | ⚠️ Дублирует Momentum Exhaustion; блокирует до записи в БД — эффективность неизмерима |
| Volume gate <25% | main.py:826 | n/a | — | — | — | ⚠️ Блокирует молча (нет записи) — неизмерим |
| Session filter | main.py:820 | — | — | — | — | Закомментирован (ок) |
| Z-Score Gravity >3σ | main.py:1120 | редко | — | — | — | ⚠️ Дублирует overextension index (z>2 уже даёт +2 балла пенальти) |
| Absorption Trap | main.py:1051 | редко | — | — | — | ✅ Логика здравая, но funding с MEXC spot невалиден → почти всегда skip |
| Liquidation Cascade | main.py:689 | редко | — | — | — | ✅ Оставить |
| ExposureManager | exposure_manager.py | ВСЁ | — | — | — | ❌ P0-БАГ: фантомные OPEN-слоты (см. §9), плюс regime-строки 'BEAR_MARKET'/'DISTRIBUTION' никогда не матчатся (реальные: 'BEAR') |
| Correlation filter | core/correlation_filter.py | — | — | — | — | LEGACY, отключён; PortfolioRiskEngine создан, но в пайплайн не подключён |
| ML confidence | — | — | — | — | — | ❌ Mock, который роняет пайплайн |

**Главный вывод по фильтрам:** все измеримые фильтры блокировали поток с отрицательным
ожиданием — фильтры в целом РАБОТАЮТ. Проблема не в фильтрах, а в том, что (а) за ~2 суток
система не одобрила НИ ОДНОЙ сделки (в экспорте 0 approved из 10 741), (б) то, что
одобряется, умирает в мёртвом execution-пути, (в) выходы не забирают MFE.

---

## 6. Trade Lifecycle Map + точная причина «зависших сделок»

Сейчас за одну и ту же сделку конкурируют **три** механизма с разными правилами:

| | background_trade_tracker (main.py:275) | ExitEngine (services/trading/exit_engine.py) | ShadowTradeMonitor |
|---|---|---|---|
| Частота | 15s | 10s | 60s |
| Статус | ✅ работает (единственный) | ❌ TypeError → мёртв | ❌ не запускается нигде |
| ID | signal_id | shadow_trades.id | shadow_trades.id |
| TP | full close на TP1 (1.5R) | full close на TP1 | full close на TP1 |
| Partial | нет | 40% на MFE≥1% | нет |
| BE | нет | MFE≥0.8% | прокси be_hit |
| Trail | Chandelier: MFE≥2%, 1.5% от пика | MFE≥1.5%, 0.5% от пика | нет |
| Time stop | 120 мин если \|pnl\|≤1%; 6ч только MR/CAP | 6ч если pnl<0.5% | 1-6ч по стратегии |
| Запись закрытия | update_shadow_trade + update_signal_status ✅ | update_exit_engine_state — ❌ НЕ синхронизирует signals.status | update_shadow_trade_status ✅ |

**Почему сделки висели и прибыль не фиксировалась (точная цепочка):**

1. TP1 = 1.5R, где R = 1.5–2.2×ATR(1h) → медианный TP1 **+4.0…+5.2%** от входа.
2. Медианный MFE сделки — **+2.4…+2.9%**. До TP доезжает 12%.
3. Chandelier-трейл взводится только при MFE≥2.0% и тащится в 1.5% от пика —
   слишком поздно и слишком широко: сделка +1.5%…+1.9% не защищена вообще.
4. Smart-timeout 120 мин срабатывает только если |pnl|≤1.0%. Сделка, висящая на +1.3%,
   не закрывается НИКОГДА (для TREND нет hard-timeout).
5. ExitEngine, который должен был всё это чинить (partial/BE/trail 0.5%), мёртв с момента
   деплоя: `live_price = global_state.live_prices.get(sym)` возвращает
   `{'price': ..., 'timestamp': ...}` (ws_manager.py:43), а дальше `(live_price - entry)`
   → TypeError → исключение ловится на уровне всего цикла → ни одна сделка не обработана.

**Почему «Tracking 6 open paper trades» при «max slots 1/1»:**

- Каждый APPROVED сигнал = строка в `signals` со status='OPEN', is_shadow=FALSE.
- ExposureManager считает слоты по `signals WHERE status IN ('OPEN','BREAKEVEN')`.
- Когда сделку закрывал ExitEngine (до того как окончательно упал) — он писал
  `outcome` в shadow_trades, но **сync `signals.status` выполняется только если в
  state_updates есть ключ `"status"`, а ExitEngine передаёт `"outcome"`**
  (timescaledb.py:781) → signals.status='OPEN' навсегда.
- При breadth<25% или regime BEAR max_slots=1 → один фантом блокирует всю систему.
- 2 750 OPEN-сделок в экспорте с MFE=MAE=0 и возрастом до 30ч — это хвост того же бага
  (плюс неработающий монитор blocked-сделок).

---

## 7. Database Health Report

Схема (PostgreSQL/Timescale на Railway, DDL в `database/timescaledb.py`):

| Таблица | Пишется? | Читается? | Проблемы |
|---|---|---|---|
| `signals` | ✅ | ✅ | Перегружена ролями: и сигналы, и сделки, и фильтр-блоки (insert_filter_block_record пишет сюда, а НЕ в filter_audit). `session_tag` используется как контейнер для строкового signal_id (save_trade) — антипаттерн. Зависшие status='OPEN' |
| `shadow_trades` | ✅ | ✅ | 2 750 вечных OPEN; `pnl_pct` НЕ заполняется трекером (update_shadow_trade не пишет pnl_pct!) → circuit breaker питается нулями |
| `shadow_trades_blocked` | ✅ | ⚠️ | Резолвится только мёртвым ExitEngine/незапущенным монитором → в новой версии counterfactuals не закрываются |
| `pullback_watchlist` | ✅ (RiskEngine) | ⚠️ | Трекер закомментирован → items никогда не исполняются/не экспирятся (TTL фильтруется только в SELECT) |
| `ohlcv` | ❌ НИКОГДА | — | Таблица-сирота. Бэктест на собственных данных невозможен |
| `smc_events` | ❌ НИКОГДА | ✅ (!) | is_pullback_on_structure_cooldown читает пустую таблицу → фильтр всегда False |
| `filter_audit` | ❌ НИКОГДА | — | Сирота. Аудит фильтров живёт в signals.block_reason |
| `missed_signals` | ❌ НИКОГДА | ✅ | Сирота; tracker закомментирован |
| `system_health` | ❌ НИКОГДА | — | Сирота |
| `gate_calibration_log` | только ручной скрипт | — | Не интегрирован |

Прочее: дубликат try/except hypertable (timescaledb.py:74-80 — недостижимый второй except);
`UNIQUE(id, created_at)` на signals из-за hypertable — ок; индексы адекватны;
локальные `apex_lite.db`/SQLite остатки используются закомментированным кодом
(main.py:2094 читает pullback_watchlist из SQLite, тогда как данные в Postgres — рассинхрон хранилищ).

## 8. Telegram / Dashboard Sync Report

- **Telegram сигнал** (`send_signal` из main.py:1451): position_usd=30, risk_usd=15,
  rr=1.5, confidence="Mock (Shadow)" — **хардкод/мок**, не реальные значения RiskEngine.
- **Telegram результат сделки**: отправляется только из background_trade_tracker.
  ExitEngine-нотификации мертвы дважды: сам движок мёртв + `telegram_chat_id.get_secret_value()`
  на обычной строке → AttributeError → bot=None (exit_engine.py:21).
- **TP-ladder, partial, BE, trailing** в Telegram не приходят (механизмы не работают).
- **Dashboard `/api/stats`**: `pnl_sum=0.0`, `avg_win=0`, `avg_loss=0`, `best/worst=0`
  захардкожены (api.py:58-63) — **фейковые метрики**. win_rate считается только по WON/LOST,
  игнорируя 49% WON_BREAKEVEN-исходов с реальным PnL.
- **`/api/equity-curve`** — заглушка `[{"date":"Start","pnl":0}]`.
- **Рассинхрон**: dashboard `/api/open-trades` читает `signals.status` (фантомы),
  Telegram «Live Portfolio» — туда же; оба показывают сделки, которых нет.
- 🔴 **Security**: `POST /api/factory-reset` и `/api/reset-shadow-stats` без авторизации,
  CORS `*`, хост 0.0.0.0 — любой, кто знает URL Railway-приложения, может стереть всю БД.
  Telegram-токен лежит в `.env` (ок для local, но проверьте, что `.env` в .gitignore и
  ротируйте токен, если репозиторий когда-либо был публичным).

---

## 9. Top Critical Problems (приоритизированный список)

### Priority 0 — ломает торговлю

| # | Проблема | Где | Как проверить | Фикс | Эффект | Риск фикса |
|---|---|---|---|---|---|---|
| 1 | `isotonic_win_prob` не определена → NameError на каждом APPROVED → execution мёртв | main.py:1782 | grep; лог "Error processing {symbol}" после "PASSED V11 GATE" | заменить на заглушку 0.5 или удалить блок | оживает весь execution-путь | низкий |
| 2 | `health_data["market_allowed"]` не определена (есть `health_result`) | main.py:1854 | grep | `health_result.level != HARD_BLOCK` | то же | низкий |
| 3 | ExitEngine читает dict как float → TypeError → мёртв | exit_engine.py:54 | лог "[EXIT_ENGINE] Error in loop" | `global_state.live_prices.get(sym, {}).get('price')` | включаются partial/BE/trail/decay | низкий |
| 4 | Закрытие сделки не синхронизирует `signals.status` → фантомные слоты → «1/1» | timescaledb.py:776-782 | `SELECT id FROM signals WHERE status='OPEN' AND id IN (SELECT signal_id FROM shadow_trades WHERE outcome NOT IN ('OPEN'))` | в update_exit_engine_state синхронизировать signals.status при любом терминальном outcome; одноразовый SQL-фикс зависших | разблокируется вся торговля | низкий |
| 5 | Три конкурирующих exit-механизма с разными правилами | main.py / exit_engine.py / shadow_monitor.py | код | оставить ОДИН (ExitEngine), tracker свести к страховке, монитор — только для blocked | детерминированный lifecycle | средний — менять аккуратно |
| 6 | pullback/missed трекеры закомментированы | main.py:2578-2579 | код | включить (после фикса SQLite-рассинхрона) либо удалить путь LIMIT целиком | лимитки исполняются; missed-аналитика | средний |
| 7 | `update_shadow_trade` не пишет `pnl_pct` → circuit breaker и stats на нулях | timescaledb.py:346 | `SELECT count(*) FROM shadow_trades WHERE outcome='WON' AND pnl_pct IS NULL` | добавить параметр pnl_pct | честный PnL в БД | низкий |

### Priority 1 — сильно ухудшает результат

| # | Проблема | Фикс |
|---|---|---|
| 8 | TP1 = 1.5R ≈ +4–5% при медианном MFE +2.4% → 12% доезжаемость, MFE не конвертируется | Dynamic Exit Engine (см. §13) |
| 9 | Paper-сделки живут на proxy-ATR SL/TP, а не на структурных из RiskEngine | после фикса #1-2 брать SL/TP из calculate_sl_tp и для paper |
| 10 | Cooldown-фильтр мёртв (не те статусы) | status IN ('OPEN','BREAKEVEN','WON',...) или по created_at |
| 11 | ExposureManager: regime 'BEAR_MARKET'/'DISTRIBUTION' не матчится с 'BEAR' | привести к одному словарю Enum |
| 12 | smc_events пустая, но pullback-structure-cooldown её читает | писать события или убрать фильтр |
| 13 | Dashboard фейковые pnl_sum/avg_win/avg_loss; equity-curve заглушка | считать из shadow_trades.pnl_pct |
| 14 | Telegram-сигнал с mock position/risk/confidence | прокидывать реальные значения |
| 15 | Незащищённые /api/factory-reset, /api/reset-shadow-stats | удалить или Bearer-токен |
| 16 | dynamic_min_score из breadth-логики (main.py:654-660) сразу перезаписывается strategy_router (main.py:962) — ветка мертва, лог врёт («Lowering to 48.0», ставит 42) | удалить мёртвую ветку |

### Priority 2 — качество

- Дублирование пенальти: z-score/RSI/premium трижды входят в скоринг (gate, overextension index, TP-компрессия) — мультиколлинеарность вернулась, несмотря на «V9 fix».
- Breadth считается по SMA200, в логах называется EMA200.
- ConfluenceV4: без Redis всегда seeded-веса; «SHAP-обучение» не работает на Railway.
- Adversarial tester получает mock-стакан — либо подключить реальный, либо убрать (расход CPU).
- `Bot(token=...)` создаётся и закрывается на каждый сигнал — заведите один экземпляр.
- 575 строк дублированного breadth/cap-кода между main.py и risk_engine (promotion-логика скопирована).

### Priority 3 — косметика/долгосрочно

- main.py 2 649 строк — резать на модули (scanner, scoring, execution, tracking).
- bot_output.log 1MB в git, *.png, CSV — в .gitignore.
- 5 версий архитектурных MD-планов в корне — в /docs.
- Дубликат except-блоков, мёртвый `mock_ai_auditor`, `_MockSignal` классы внутри цикла.

---

## 10–11. Weak/Noisy vs Strong Filters

**Сильные (оставить/усилить):** Momentum Exhaustion (E=−1.19% у блокированных),
V7 Gate (−0.82%), DataValidator (−0.75%), Liquidation Cascade, Volume gate (логичен, но добавьте запись блоков для измеримости).

**Слабые/шумные:** старый MTF hard gate (−0.20%, уже заменён на пенальти — правильно);
Z-Score Gravity (дублирует overextension); Exhaustion-5%/4h (дублирует Momentum Exhaustion —
оставить один); Absorption Trap (питается невалидным funding со спота — перевести на
binance_fapi, который уже есть); Cooldown (мёртв); ExposureManager (сломан);
Adversarial (декоративен).

## 12. Missing Institutional Modules (что реально стоит добавлять и в каком порядке)

Уже есть, но не подключено: PortfolioRiskEngine, InstitutionalTCM, OrderRouter,
funding/LSR/OI (binance_fapi), CVD-прокси, volume nodes POC/HVN/LVN в smc_core.
**Сначала подключите то, что написано, потом пишите новое.**

Реально отсутствует (в порядке ценности):
1. **Persist OHLCV + полный event-sourcing сделок** — без этого нет ни бэктеста, ни learning loop.
2. **Real CVD / trade-tape** (aggTrades WS вместо свечного прокси).
3. **Volume profile** уже есть в smc_core — довести до TP-таргетинга (VAH/VAL/POC как цели).
4. **Volatility-regime position sizing** (Kelly уже есть, но питается константами 0.55/2.0/1.0 — кормить реальной статистикой из shadow_trades).
5. **Isotonic/Platt калибровка win-prob по v7-бакетам** — данных уже хватает (~8k исходов).
6. Liquidation heatmap / orderbook imbalance alerting — потом.

## 13. Exit Engine Fix Plan (главный источник денег)

Симуляция на 7 991 реальной закрытой сделке (консервативно: при неоднозначности SL побеждает):

| Стратегия выхода | E[pnl]/сделку |
|---|---|
| Текущая (TP 1.5R≈4-5%, chandelier 2%/1.5%) | **−0.44%** (учёт «как WON/LOST»), +0.40% с учётом реализованных трейлов |
| Фикс. TP +1.5% | +0.10% |
| **Фикс. TP +2.0%** | **+0.36%** |
| Фикс. TP +3.0% | +0.18% |
| **Partial 50%@+1.5% → BE → runner до +3%** | **+0.48%** |

Доезжаемость MFE: ≥1% — 73% сделок, ≥1.5% — 67%, ≥2% — 61%, ≥3% — 35%, ≥4% — 17%.

**План (один движок — починенный ExitEngine):**
1. Фикс P0 #3 (dict→float) и #4 (sync signals.status).
2. Partial TP1: 40-50% на +1.2…1.5% (или 0.8R) — фиксирует то, что сейчас сгорает.
3. BE после partial (как сейчас, MFE≥0.8% — ок).
4. Trailing: ATR-based (0.7–1.0×ATR15m от пика), а не фикс. 0.5% — 0.5% на альтах выбивает шумом.
5. TP-runner: ближайший структурный уровень (swing high / HVN / VAH из smc_core), кап +3…4% в SIDEWAYS/BEAR — RiskEngine это уже умеет, надо лишь прокинуть в paper-путь.
6. Time-stop для TREND: 8ч безусловный (сейчас TREND бессмертен при pnl>1%).
7. Momentum-decay: закрыть остаток, если 5m CVD развернулся и pnl>0 после partial.
8. При partial реально бук realized_pnl_pct (sized) — сейчас size_pct уменьшается, а PnL не букируется.

Ожидаемый эффект: по симуляции ≈ +0.9%/сделку разницы vs текущая логика учёта; даже
против «реализованных» +0.40% — это +20-30% к expectancy плюс резкое снижение дисперсии.

## 14. Risk Engine Fix Plan

1. Подключить готовый PortfolioRiskEngine как pre-trade gate (он написан и протестирован — tests/test_portfolio_risk_engine.py).
2. Kelly: заменить константы (0.55/2.0/1.0) на скользящую 30-дневную статистику из shadow_trades по бакетам v7.
3. ExposureManager: фикс regime-строк; слоты считать по living-таблице (shadow_trades.outcome), а не signals.status; max_slots=1 при BEAR — слишком жёстко для paper-стадии, разумно 3.
4. Circuit breaker: кормить реальным pnl_pct (после фикса #7).
5. SL: ваш структурный SL с капом 0.7–3% адекватен; ATR Stop Cap (main.py:1696) оставить.

## 15. Scoring Engine Improvement Plan

1. Один источник пенальти на фактор (сейчас RSI/z-score/premium учитываются до 3 раз).
2. Калибровка: посчитать монотонность win-rate по v7-бакетам на новых APPROVED данных (v7_edge_validation.py уже есть — автоматизировать в cron, писать в gate_calibration_log).
3. Порог: данные показывают, что за 2 суток APPROVED=0 — gate перетянут. Снизить base в SIDEWAYS с 50 до ~45 и дать системе торговать paper, иначе learning loop не на чем учиться.
4. A+ bonus +35 — это половина шкалы, фактически override gate. Снизить до +15 или требовать подтверждения 2 из 3 (CVD, OFI, Volume).
5. Перевести funding/OI-скоринг полностью на binance_fapi (готов) и выкинуть невалидный spot-funding.

## 16. Roadmap to Hedge-Fund-Level APEX

- **Неделя 1 (стабилизация):** P0 #1-7 + SQL-фикс зависших строк + защита dashboard. Результат: система реально торгует paper, lifecycle детерминирован.
- **Неделя 2 (exit-альфа):** Dynamic Exit Engine (§13), реальные данные в Telegram/Dashboard, pnl_pct везде.
- **Неделя 3-4 (данные):** писать ohlcv/smc_events/system_health; Missed-signals + filter-efficiency на реальных таблицах; авто-recalibration gate.
- **Месяц 2 (edge):** isotonic win-prob, performance by regime/setup, symbol-specific thresholds, real CVD, volume-profile TP-таргетинг, подключение PortfolioRiskEngine.
- **Месяц 3:** бэктестер на собственном ohlcv, walk-forward, и только потом live-execution через OrderRouter.

## 17. Exact Code Files That Need Changes

| Файл | Что менять |
|---|---|
| `main.py:1782` | isotonic_win_prob → 0.5 заглушка |
| `main.py:1854` | health_data → health_result.level |
| `main.py:2578-2579` | включить/удалить трекеры |
| `main.py:275-486` | свести tracker к страховке после включения ExitEngine |
| `services/trading/exit_engine.py:54` | .get('price'); :21 chat_id без get_secret_value; partial booking; ATR-trail |
| `database/timescaledb.py:346` | + pnl_pct; :776-782 безусловный sync терминальных статусов; :451 cooldown-статусы |
| `services/engine/exposure_manager.py:48` | regime-строки; источник слотов |
| `dashboard/api.py:32-68` | реальные pnl/avg; :359-375 удалить/защитить reset-endpoints |
| `services/notifications/telegram_ui.py` | реальные значения в карточке сигнала |

## 18. SQL / Migrations Needed

```sql
-- 1) Одноразовая очистка фантомов (БЕЗ удаления данных):
UPDATE signals s SET status = st.outcome
FROM shadow_trades st
WHERE st.signal_id = s.id
  AND s.status IN ('OPEN','BREAKEVEN')
  AND st.outcome NOT IN ('OPEN','PARTIAL_TP','BREAKEVEN','TRAILING');

-- 2) Закрыть протухшие OPEN-сделки старше 48ч как EXPIRED_UNTRACKED:
UPDATE shadow_trades SET outcome='EXPIRED_UNTRACKED', resolved_at=NOW()
WHERE outcome='OPEN' AND created_at < NOW() - INTERVAL '48 hours';
UPDATE signals SET status='EXPIRED_UNTRACKED'
WHERE status IN ('OPEN','BREAKEVEN') AND created_at < NOW() - INTERVAL '48 hours';

-- 3) Индексы под exit-engine выборку:
CREATE INDEX IF NOT EXISTS idx_st_open ON shadow_trades (outcome) WHERE outcome IN ('OPEN','PARTIAL_TP','BREAKEVEN','TRAILING');
CREATE INDEX IF NOT EXISTS idx_signals_open ON signals (status) WHERE status IN ('OPEN','BREAKEVEN');
```

## 19. Testing Plan

1. Unit: exit-логика как чистая функция `decide_exit(trade_state, candle) -> action` + 20 кейсов (TP/SL same candle, partial→BE→trail, decay, time-stop).
2. Replay-тест: прогнать 7 991 закрытую сделку из CSV через новый exit-движок, сверить E[pnl] с симуляцией (+0.4…0.5%).
3. Integration: docker-compose Postgres → полный цикл approve→open→partial→close → проверка sync signals.status==shadow_trades.outcome (инвариант).
4. Канарейка: 48ч paper с новым движком, инвариант-чекер в cron: `SELECT count(*) FROM signals s JOIN shadow_trades st ON st.signal_id=s.id WHERE s.status='OPEN' AND st.outcome<>'OPEN'` == 0.

## 20. Final Priority Checklist

- [ ] P0-1: main.py:1782 isotonic_win_prob
- [ ] P0-2: main.py:1854 health_data
- [ ] P0-3: exit_engine.py live_prices dict
- [ ] P0-4: sync signals.status при закрытии + SQL-очистка фантомов
- [ ] P0-5: один exit-механизм
- [ ] P0-6: судьба pullback/missed трекеров
- [ ] P0-7: pnl_pct в update_shadow_trade
- [ ] P1: Dynamic Exit (partial 40-50%@1.5% + BE + ATR-trail + структурный TP + time-stop 8h)
- [ ] P1: cooldown, ExposureManager regime, dashboard реальные метрики, защита reset-endpoints
- [ ] P2: дедупликация пенальти, калибровка gate, реальный funding, запись ohlcv/событий
