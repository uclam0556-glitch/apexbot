"""
APEX v5.1 — Trading Dashboard API
FastAPI backend serving the world-class trading dashboard.
Reads from TimescaleDB and rs_matrix_engine.
"""
import os
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import structlog
from database.timescaledb import get_pool, factory_reset_db

logger = structlog.get_logger(__name__)

def create_app() -> FastAPI:
    app = FastAPI(title="APEX Dashboard", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/download-db")
    async def download_db():
        """TimescaleDB cannot be easily downloaded as a file. Returns 404."""
        return JSONResponse(status_code=404, content={"error": "Database is TimescaleDB (PostgreSQL). Direct download not supported."})

    CLOSED_OUTCOMES = (
        'WON', 'LOST', 'WON_BREAKEVEN', 'TIMEOUT', 'BREAKEVEN',
        'TIMEOUT_SMALL_WIN', 'TIMEOUT_SMALL_LOSS', 'TIMEOUT_BREAKEVEN',
        'CLOSED_WON', 'CLOSED_LOST', 'CLOSED_BREAKEVEN', 'MOMENTUM_DECAY', 'TIME_STOP'
    )
    OPEN_OUTCOMES = ('OPEN', 'PARTIAL_TP', 'BREAKEVEN', 'TRAILING')

    @app.get("/api/stats")
    async def get_stats():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT outcome, COALESCE(pnl_pct, 0.0) as pnl FROM shadow_trades WHERE outcome = ANY($1::text[])",
                    list(CLOSED_OUTCOMES)
                )
                open_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM shadow_trades WHERE outcome = ANY($1::text[])",
                    list(OPEN_OUTCOMES)
                )

            rows = [dict(r) for r in rows]
            total = len(rows)
            if total == 0:
                return {"total": 0, "open": open_count, "won": 0, "lost": 0, "small_win": 0, "small_loss": 0, "breakeven": 0,
                        "win_rate": 0, "pnl_sum": 0.0, "best_trade": 0.0,
                        "worst_trade": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "regime_stats": {}}

            # Real PnL math from shadow_trades.pnl_pct (populated by ExitEngine v2 / tracker)
            wins = [r for r in rows if r['pnl'] > 0.1]
            losses = [r for r in rows if r['pnl'] < -0.1]
            flats = [r for r in rows if -0.1 <= r['pnl'] <= 0.1]
            pnls = [r['pnl'] for r in rows]

            decided = len(wins) + len(losses)
            win_rate = round(len(wins) / decided * 100, 1) if decided > 0 else 0

            return {
                "total": total, "open": open_count, "won": len(wins), "small_win": 0,
                "breakeven": len(flats), "small_loss": 0, "lost": len(losses),
                "win_rate": win_rate,
                "pnl_sum": round(sum(pnls), 2),
                "best_trade": round(max(pnls), 2) if pnls else 0.0,
                "worst_trade": round(min(pnls), 2) if pnls else 0.0,
                "avg_win": round(sum(r['pnl'] for r in wins) / len(wins), 2) if wins else 0.0,
                "avg_loss": round(sum(r['pnl'] for r in losses) / len(losses), 2) if losses else 0.0,
                "regime_stats": {}
            }
        except Exception as e:
            logger.error(f"Stats error: {e}")
            return {"total": 0, "open": 0, "won": 0, "lost": 0,
                    "win_rate": 0, "pnl_sum": 0, "best_trade": 0,
                    "worst_trade": 0, "avg_win": 0, "avg_loss": 0, "regime_stats": {}}

    @app.get("/api/equity-curve")
    async def get_equity_curve():
        """Cumulative realized PnL (%) over resolved trades, by resolution time."""
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT resolved_at, COALESCE(pnl_pct, 0.0) as pnl
                    FROM shadow_trades
                    WHERE resolved_at IS NOT NULL AND outcome = ANY($1::text[])
                    ORDER BY resolved_at ASC
                    LIMIT 5000
                    """,
                    list(CLOSED_OUTCOMES)
                )
            curve = []
            cum = 0.0
            for r in rows:
                cum += r['pnl']
                curve.append({"date": r['resolved_at'].strftime("%m-%d %H:%M"), "pnl": round(cum, 2)})
            return curve if curve else [{"date": "Start", "pnl": 0.0}]
        except Exception as e:
            logger.error(f"Equity curve error: {e}")
            return [{"date": "Start", "pnl": 0.0}]

    @app.get("/api/trades")
    async def get_trades(limit: int = 500, filter_type: str = "ALL"):
        try:
            pool = await get_pool()
            query = "SELECT * FROM signals"
            params = []
            if filter_type == "WON":
                query += " WHERE status IN ('WON', 'WON_BREAKEVEN', 'CLOSED_WON')"
            elif filter_type == "LOST":
                query += " WHERE status IN ('LOST', 'CLOSED_LOST')"
            elif filter_type == "OPEN":
                query += " WHERE status IN ('OPEN', 'BREAKEVEN', 'PARTIAL_TP', 'TRAILING')"
            elif filter_type == "CLOSED":
                query += " WHERE status NOT IN ('OPEN', 'BREAKEVEN', 'PARTIAL_TP', 'TRAILING', 'WAITING', 'WAITING_STRUCTURE')"
            
            query += " ORDER BY created_at DESC LIMIT $1"
            params.append(limit)

            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Trades error: {e}")
            return []

    @app.get("/api/open-trades")
    async def get_open_trades():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT s.id, s.symbol, s.direction, s.strategy, s.entry_price, s.sl_price as stop_loss,
                           s.tp1_price as take_profit_1, s.v7_score_raw as score, 0 as position_usd,
                           s.created_at as opened_at, s.status,
                           st.unrealized_pnl_pct, st.realized_pnl_pct, st.mfe_pct, st.mae_pct,
                           st.partial_exit_count, st.remaining_size_pct, st.breakeven_activated, st.trailing_stop_price
                    FROM signals s
                    LEFT JOIN shadow_trades st ON st.signal_id = s.id
                    WHERE s.status IN ('OPEN', 'BREAKEVEN', 'PARTIAL_TP', 'TRAILING') AND s.is_shadow = FALSE
                    ORDER BY s.created_at DESC
                """)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Open trades error: {e}")
            return []

    @app.get("/api/limit-orders")
    async def get_limit_orders():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM pullback_watchlist WHERE status = 'WAITING' ORDER BY created_at DESC")
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Limit orders error: {e}")
            return []

    @app.get("/api/shadow-trades")
    async def get_shadow_trades(limit: int = 100):
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                # We join with signals to get the block_reason and v7_score_raw
                query = """
                    SELECT st.id, st.symbol, s.direction, s.strategy, s.entry_price, s.sl_price as stop_loss, s.tp1_price as take_profit_1,
                           s.block_reason as primary_block_reason, '[]' as all_block_reasons, s.v7_score_raw as v7_score, 
                           COALESCE(st.outcome, 'TRACKING') as status,
                           st.mfe_pct, st.mae_pct, st.created_at, st.resolved_at
                    FROM shadow_trades st
                    JOIN signals s ON st.signal_id = s.id
                    ORDER BY st.created_at DESC
                    LIMIT $1
                """
                rows = await conn.fetch(query, limit)
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Shadow trades error: {e}")
            return []

    @app.get("/api/export-shadow-csv")
    async def export_shadow_csv():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                query = """
                    SELECT st.id, st.symbol, s.direction, s.strategy, s.entry_price, s.sl_price as stop_loss, s.tp1_price as take_profit_1,
                           s.block_reason, s.v7_score_raw as v7_score, COALESCE(st.outcome, 'TRACKING') as status,
                           st.mfe_pct, st.mae_pct, st.created_at, st.resolved_at
                    FROM shadow_trades st
                    JOIN signals s ON st.signal_id = s.id
                    ORDER BY st.created_at DESC
                    LIMIT 50000
                """
                rows = await conn.fetch(query)
                
            if not rows:
                return Response(content="No data", media_type="text/plain")
                
            import csv
            import io
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=dict(rows[0]).keys())
            writer.writeheader()
            for r in rows:
                writer.writerow(dict(r))
                
            response = Response(content=output.getvalue(), media_type="text/csv")
            response.headers["Content-Disposition"] = "attachment; filename=shadow_trades_database.csv"
            return response
        except Exception as e:
            logger.error(f"CSV Export error: {e}")
            return Response(content=f"Error exporting CSV: {e}", status_code=500)

    @app.get("/api/query-diagnostics")
    async def query_diagnostics():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                q1 = await conn.fetch('''
                    SELECT s.symbol, s.created_at, s.strategy, s.regime, s.breadth_pct, s.v7_score_raw, s.mtf_score, s.session_tag,
                           st.mfe_pct, st.mae_pct, st.bars_to_outcome, EXTRACT(HOUR FROM s.created_at) as hour_utc, EXTRACT(DOW FROM s.created_at) as day_of_week
                    FROM signals s JOIN shadow_trades st ON s.id = st.signal_id
                    WHERE s.block_reason LIKE '%MTF Gate%' AND st.outcome = 'WON'
                    ORDER BY st.mfe_pct DESC LIMIT 100;
                ''')
                
                q2 = await conn.fetch('''
                    SELECT s.regime, s.session_tag, s.strategy, ROUND(AVG(s.v7_score_raw)::numeric, 1) as avg_v7,
                           ROUND(AVG(s.mtf_score)::numeric, 2) as avg_mtf, ROUND(AVG(s.breadth_pct)::numeric, 1) as avg_breadth,
                           ROUND(AVG(st.mfe_pct)::numeric, 2) as avg_mfe, COUNT(*) as count
                    FROM signals s JOIN shadow_trades st ON s.id = st.signal_id
                    WHERE st.outcome = 'WON' AND s.status = 'REJECTED_BY_FILTER'
                    GROUP BY s.regime, s.session_tag, s.strategy HAVING COUNT(*) >= 5 ORDER BY avg_mfe DESC;
                ''')
                
                q3 = await conn.fetch('''
                    SELECT ROUND(s.mtf_score::numeric, 1) as mtf_bucket, COUNT(*) as total_blocked,
                           COUNT(*) FILTER (WHERE st.outcome = 'WON') as tp_missed, COUNT(*) FILTER (WHERE st.outcome = 'LOST') as sl_saved,
                           ROUND(COUNT(*) FILTER (WHERE st.outcome = 'WON')::numeric / NULLIF(COUNT(*) FILTER (WHERE st.outcome IN ('WON','LOST')), 0) * 100, 1) as win_rate_pct
                    FROM signals s JOIN shadow_trades st ON s.id = st.signal_id
                    WHERE s.block_reason LIKE '%MTF Gate%' AND st.outcome IS NOT NULL
                    GROUP BY mtf_bucket ORDER BY mtf_bucket DESC;
                ''')
                
                q4 = await conn.fetch('''
                    SELECT EXTRACT(HOUR FROM s.created_at) as hour_utc, COUNT(*) FILTER (WHERE st.outcome = 'WON') as wins,
                           COUNT(*) FILTER (WHERE st.outcome = 'LOST') as losses, ROUND(AVG(st.mfe_pct) FILTER (WHERE st.outcome = 'WON')::numeric, 2) as avg_mfe_wins,
                           ROUND(COUNT(*) FILTER (WHERE st.outcome = 'WON')::numeric / NULLIF(COUNT(*) FILTER (WHERE st.outcome IN ('WON','LOST')), 0) * 100, 1) as win_rate_pct
                    FROM signals s JOIN shadow_trades st ON s.id = st.signal_id
                    WHERE st.outcome IS NOT NULL
                    GROUP BY hour_utc ORDER BY win_rate_pct DESC NULLS LAST;
                ''')
                
                q5 = await conn.fetch('''
                    SELECT outcome as status, COUNT(*) as count, MIN(created_at) as oldest, MAX(created_at) as newest,
                           COUNT(*) FILTER (WHERE mfe_pct IS NULL) as no_mfe, COUNT(*) FILTER (WHERE mfe_pct IS NOT NULL) as has_mfe
                    FROM shadow_trades GROUP BY outcome;
                ''')
                
                q6 = await conn.fetch('''
                    SELECT CASE WHEN s.v7_score_raw < 30 THEN '0-30' WHEN s.v7_score_raw < 40 THEN '30-40' WHEN s.v7_score_raw < 45 THEN '40-45'
                                WHEN s.v7_score_raw < 48 THEN '45-48' WHEN s.v7_score_raw < 52 THEN '48-52' WHEN s.v7_score_raw < 60 THEN '52-60' ELSE '60+' END as v7_bucket,
                           COUNT(*) as total, COUNT(*) FILTER (WHERE st.outcome = 'WON') as wins, COUNT(*) FILTER (WHERE st.outcome = 'LOST') as losses,
                           ROUND(AVG(st.mfe_pct)::numeric, 2) as avg_mfe
                    FROM signals s JOIN shadow_trades st ON s.id = st.signal_id
                    WHERE st.outcome IS NOT NULL GROUP BY v7_bucket ORDER BY v7_bucket;
                ''')
                
                breadth = await conn.fetchval('''SELECT breadth_pct FROM signals ORDER BY created_at DESC LIMIT 1;''')

            return {
                "q1_missed_tp": [dict(r) for r in q1],
                "q2_good_setup": [dict(r) for r in q2],
                "q3_mtf_distribution": [dict(r) for r in q3],
                "q4_hour_analysis": [dict(r) for r in q4],
                "q5_shadow_monitor_diag": [dict(r) for r in q5],
                "q6_v7_threshold": [dict(r) for r in q6],
                "current_breadth": breadth
            }
        except Exception as e:
            logger.error(f"Diagnostics error: {e}")
            return {"error": str(e)}

    @app.get("/api/shadow-stats")
    async def get_shadow_stats():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                query = """
                    SELECT 
                        s.block_reason as primary_block_reason,
                        COUNT(*) as total,
                        SUM(CASE WHEN st.outcome = 'WON' THEN 1 ELSE 0 END) as won,
                        SUM(CASE WHEN st.outcome = 'LOST' THEN 1 ELSE 0 END) as lost,
                        SUM(CASE WHEN st.outcome = 'TIMEOUT' THEN 1 ELSE 0 END) as timeout,
                        SUM(CASE WHEN st.outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
                        SUM(CASE WHEN st.outcome = 'WON_BREAKEVEN' THEN 1 ELSE 0 END) as won_breakeven,
                        SUM(CASE WHEN st.outcome IN ('TIMEOUT_SMALL_WIN','TIMEOUT_SMALL_LOSS','TIMEOUT_BREAKEVEN') THEN 1 ELSE 0 END) as timeout_variants,
                        SUM(CASE WHEN st.outcome = 'OPEN' THEN 1 ELSE 0 END) as tracking
                    FROM shadow_trades st
                    JOIN signals s ON st.signal_id = s.id
                    GROUP BY s.block_reason
                    ORDER BY total DESC
                """
                stats_rows = await conn.fetch(query)
            return [dict(r) for r in stats_rows]
        except Exception as e:
            logger.error(f"Shadow stats error: {e}")
            return []

    @app.get("/api/rs-matrix")
    async def get_rs_matrix():
        try:
            from services.intelligence.rs_matrix import rs_matrix_engine
            return {
                "top": rs_matrix_engine.get_top_n(8),
                "btc_change": rs_matrix_engine.btc_change,
                "last_updated": rs_matrix_engine.last_updated.strftime("%H:%M UTC") if rs_matrix_engine.last_updated else "—",
            }
        except Exception as e:
            return {"top": [], "btc_change": 0, "last_updated": "—"}

    @app.get("/api/live-prices")
    async def get_live_prices(response: Response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        from shared.state import global_state
        from services.intelligence.rs_matrix import rs_matrix_engine
        
        ws_prices = getattr(global_state, 'live_prices', {})
        result = {}
        for item in rs_matrix_engine.matrix:
            sym = item['symbol']
            ws_data = ws_prices.get(sym, {})
            result[sym] = {"price": ws_data.get('price') or item.get('price', 0.0), "change": item.get('change_24h', 0.0)}
            
        for sym, data in ws_prices.items():
            if sym not in result:
                result[sym] = {"price": data.get('price', 0.0), "change": 0.0}
        return result

    @app.get("/api/system-status")
    async def get_system_status():
        try:
            from shared.state import global_state
            return {
                "regime": getattr(global_state, 'regime', 'UNKNOWN'),
                "current_symbol": getattr(global_state, 'current_symbol', '—'),
                "last_scan": getattr(global_state, 'last_scan_time', '—'),
                "is_paused": getattr(global_state, 'is_paused', False),
                "signals_today": getattr(global_state, 'signals_sent_today', 0),
            }
        except Exception:
            return {"regime": "UNKNOWN", "current_symbol": "—", "last_scan": "—", "is_paused": False, "signals_today": 0}

    @app.get("/api/features-stats")
    async def get_features_stats():
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                query = """
                    SELECT regime_at_entry as regime, COUNT(*) as total, SUM(CASE WHEN outcome IN ('WON', 'WON_BREAKEVEN') THEN 1 ELSE 0 END) as won
                    FROM shadow_trades 
                    WHERE outcome IN ('WON', 'LOST', 'WON_BREAKEVEN')
                    GROUP BY regime_at_entry
                """
                regime_rows = await conn.fetch(query)
                
            regime_stats = []
            for r in regime_rows:
                total = r['total']
                won = r['won']
                regime_stats.append({"regime": r['regime'], "total": total, "win_rate": round((won/total)*100, 1) if total > 0 else 0})
            return {"regime_stats": regime_stats}
        except Exception as e:
            logger.error(f"Features stats error: {e}")
            return {"regime_stats": []}

    @app.get("/", response_class=HTMLResponse)
    async def serve_dashboard():
        html_path = Path(__file__).parent / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Dashboard loading...</h1>")

    @app.get("/api/feature-store")
    async def api_feature_store(limit: int = 20):
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM signals ORDER BY created_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
        except Exception as e:
            return {"error": str(e)}

    def _check_admin(request) -> bool:
        """Destructive endpoints require APEX_ADMIN_TOKEN (was: anyone with the URL could wipe the DB)."""
        expected = os.getenv("APEX_ADMIN_TOKEN")
        if not expected:
            return False  # no token configured -> destructive API disabled entirely
        provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return provided == expected

    @app.post("/api/reset-shadow-stats")
    async def api_reset_shadow_stats(request: Request):
        if not _check_admin(request):
            return JSONResponse(status_code=403, content={"status": "error", "message": "Forbidden: set APEX_ADMIN_TOKEN and pass it as Bearer token."})
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM shadow_trades")
            return {"status": "success", "message": "Shadow trades statistics cleared."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @app.post("/api/factory-reset")
    async def api_factory_reset(request: Request):
        if not _check_admin(request):
            return JSONResponse(status_code=403, content={"status": "error", "message": "Forbidden: set APEX_ADMIN_TOKEN and pass it as Bearer token."})
        try:
            await factory_reset_db()
            return {"status": "success", "message": "Database wiped."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return app

