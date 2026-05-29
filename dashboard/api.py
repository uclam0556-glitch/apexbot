"""
APEX v5.1 — Trading Dashboard API
FastAPI backend serving the world-class trading dashboard.
Reads from apex_lite.db and rs_matrix_engine.
"""
import os
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiosqlite
import structlog

logger = structlog.get_logger(__name__)

DB_PATH = os.getenv("SQLITE_DB_PATH", "apex_lite.db")


def create_app() -> FastAPI:
    app = FastAPI(title="APEX Dashboard", docs_url=None, redoc_url=None)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ──────────────────────────────────────────────────────────────
    # API ENDPOINTS
    # ──────────────────────────────────────────────────────────────

    @app.get("/api/stats")
    async def get_stats():
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM trades WHERE status IN ('WON', 'LOST', 'WON_BREAKEVEN', 'TIMEOUT')") as cur:
                    rows = await cur.fetchall()
                async with db.execute("SELECT COUNT(*) FROM trades WHERE status IN ('OPEN', 'BREAKEVEN')") as cur:
                    open_count = (await cur.fetchone())[0]

            rows = [dict(r) for r in rows]
            total = len(rows)
            if total == 0:
                return {"total": 0, "open": open_count, "won": 0, "lost": 0,
                        "win_rate": 0, "pnl_sum": 0.0, "best_trade": 0.0,
                        "worst_trade": 0.0, "avg_win": 0.0, "avg_loss": 0.0}

            won = [r for r in rows if r['status'] in ('WON', 'WON_BREAKEVEN') or (r['status'] == 'TIMEOUT' and r['pnl_pct'] and r['pnl_pct'] > 0)]
            lost = [r for r in rows if r['status'] == 'LOST' or (r['status'] == 'TIMEOUT' and r['pnl_pct'] and r['pnl_pct'] <= 0)]
            pnl_vals = [r['pnl_pct'] for r in rows if r['pnl_pct'] is not None]

            return {
                "total": total,
                "open": open_count,
                "won": len(won),
                "lost": len(lost),
                "win_rate": round(len(won) / total * 100, 1) if total > 0 else 0,
                "pnl_sum": round(sum(pnl_vals), 2),
                "best_trade": round(max(pnl_vals), 2) if pnl_vals else 0,
                "worst_trade": round(min(pnl_vals), 2) if pnl_vals else 0,
                "avg_win": round(sum(r['pnl_pct'] for r in won if r['pnl_pct']) / len(won), 2) if won else 0,
                "avg_loss": round(sum(r['pnl_pct'] for r in lost if r['pnl_pct']) / len(lost), 2) if lost else 0,
            }
        except Exception as e:
            logger.error(f"Stats error: {e}")
            return {"total": 0, "open": 0, "won": 0, "lost": 0,
                    "win_rate": 0, "pnl_sum": 0, "best_trade": 0,
                    "worst_trade": 0, "avg_win": 0, "avg_loss": 0}

    @app.get("/api/equity-curve")
    async def get_equity_curve():
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT closed_at, pnl_pct FROM trades WHERE status IN ('WON', 'LOST', 'WON_BREAKEVEN', 'TIMEOUT') AND pnl_pct IS NOT NULL ORDER BY closed_at ASC"
                ) as cur:
                    rows = await cur.fetchall()

            cumulative = 0.0
            curve = [{"date": "Start", "pnl": 0.0}]
            for row in rows:
                cumulative += row['pnl_pct']
                dt = row['closed_at']
                if dt:
                    try:
                        d = datetime.strptime(str(dt)[:16], "%Y-%m-%d %H:%M")
                        label = d.strftime("%d.%m %H:%M")
                    except Exception:
                        label = str(dt)[:10]
                else:
                    label = "?"
                curve.append({"date": label, "pnl": round(cumulative, 2)})
            return curve
        except Exception as e:
            logger.error(f"Equity curve error: {e}")
            return []

    @app.get("/api/trades")
    async def get_trades(limit: int = 30):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,)
                ) as cur:
                    rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Trades error: {e}")
            return []

    @app.get("/api/open-trades")
    async def get_open_trades():
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM trades WHERE status IN ('OPEN', 'BREAKEVEN') ORDER BY opened_at DESC"
                ) as cur:
                    rows = await cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Open trades error: {e}")
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
    async def get_live_prices():
        from shared.state import global_state
        from services.intelligence.rs_matrix import rs_matrix_engine
        
        ws_prices = getattr(global_state, 'live_prices', {})
        result = {}
        
        # Combine WS prices with RS Matrix (Binance 24h data)
        for item in rs_matrix_engine.matrix:
            sym = item['symbol']
            # If WS has a more recent price, use it, but keep the official Binance 24h change
            ws_data = ws_prices.get(sym, {})
            result[sym] = {
                "price": ws_data.get('price') or item.get('price', 0.0),
                "change": item.get('change_24h', 0.0)
            }
            
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
            return {"regime": "UNKNOWN", "current_symbol": "—",
                    "last_scan": "—", "is_paused": False, "signals_today": 0}

    @app.get("/api/features-stats")
    async def get_features_stats():
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                # Group by Regime
                async with db.execute('''
                    SELECT 
                        regime,
                        COUNT(*) as total,
                        SUM(CASE WHEN outcome = 'WON' THEN 1 ELSE 0 END) as won
                    FROM feature_store 
                    WHERE outcome != 'OPEN'
                    GROUP BY regime
                ''') as cur:
                    regime_rows = await cur.fetchall()
                    
                regime_stats = []
                for r in regime_rows:
                    total = r['total']
                    won = r['won']
                    regime_stats.append({
                        "regime": r['regime'],
                        "total": total,
                        "win_rate": round((won/total)*100, 1) if total > 0 else 0
                    })
                    
            return {"regime_stats": regime_stats}
        except Exception as e:
            logger.error(f"Features stats error: {e}")
            return {"regime_stats": []}

    # ──────────────────────────────────────────────────────────────
    # SERVE DASHBOARD HTML
    # ──────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def serve_dashboard():
        html_path = Path(__file__).parent / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Dashboard loading...</h1>")

    @app.get("/api/feature-store")
    async def api_feature_store(limit: int = 20):
        from shared.lite_db import get_recent_features
        try:
            records = await get_recent_features(limit)
            return [dict(r) for r in records]
        except Exception as e:
            return {"error": str(e)}

    return app
