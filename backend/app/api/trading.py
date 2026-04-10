"""Trading API — portfolio, positions, signals, trade execution, analysis."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import verify_api_key

router = APIRouter(prefix="/api/trading", tags=["trading"], dependencies=[Depends(verify_api_key)])


class TradeRequest(BaseModel):
    symbol: str
    side: str  # buy / sell
    size: float
    order_type: str = "market"
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    signal_id: str | None = None


class SignalDecision(BaseModel):
    approved: bool
    reason: str | None = None


# ── Portfolio ────────────────────────────────────────────────────


@router.get("/portfolio")
async def get_portfolio():
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        return {"error": "Trading service not connected", "platforms_connected": 0}
    return await trading_service.get_portfolio_summary()


@router.get("/positions")
async def get_positions():
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        return []
    return await trading_service.get_positions()


@router.get("/accounts")
async def get_accounts():
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        return []
    return await trading_service.get_all_accounts()


# ── Trade Signals ────────────────────────────────────────────────


@router.get("/signals")
async def get_signals(status: str | None = None, limit: int = 20):
    from app.services.trading.trading_service import trading_service

    return await trading_service.get_signals(status=status, limit=limit)


@router.post("/signals/{signal_id}/decide")
async def decide_signal(signal_id: str, decision: SignalDecision):
    from app.services.trading.trading_service import trading_service

    if decision.approved:
        result = await trading_service.approve_signal(signal_id)
    else:
        result = await trading_service.reject_signal(
            signal_id, reason=decision.reason or ""
        )
    if not result:
        raise HTTPException(404, "Signal not found or already decided")
    return result


# ── Trade Execution ──────────────────────────────────────────────


@router.get("/history")
async def get_trade_history(limit: int = 50):
    from app.services.trading.trading_service import trading_service

    return await trading_service.get_trade_history(limit=limit)


@router.get("/trade-chain/{trade_id}")
async def get_trade_chain(trade_id: str):
    """Get the full agent action chain for a specific trade."""
    from app.services.trading.trading_service import trading_service

    chain = await trading_service.get_trade_chain(trade_id)
    if not chain:
        raise HTTPException(404, "Trade not found")
    return chain


@router.post("/execute")
async def execute_trade(req: TradeRequest):
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        raise HTTPException(503, "Trading service not connected")
    return await trading_service.execute_trade(
        symbol=req.symbol,
        side=req.side,
        size=req.size,
        order_type=req.order_type,
        price=req.price,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        signal_id=req.signal_id,
    )


# ── Close Trade ──────────────────────────────────────────────────


@router.post("/close/{trade_id}")
async def close_trade(trade_id: str):
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        raise HTTPException(503, "Trading service not connected")
    try:
        return await trading_service.close_trade(trade_id)
    except Exception as e:
        raise HTTPException(400, str(e))


# ── Market Data ──────────────────────────────────────────────────


@router.get("/market/{symbol}")
async def get_market_data(symbol: str):
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        raise HTTPException(503, "Trading service not connected")
    prices = await trading_service.get_prices([symbol])
    return prices[0] if prices else {"error": "No data"}


@router.get("/analysis/{symbol}")
async def get_analysis(symbol: str, interval: str = "1h", limit: int = 100):
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        raise HTTPException(503, "Trading service not connected")
    return await trading_service.run_technical_analysis(symbol, interval, limit)


@router.get("/advanced-analysis/{symbol}")
async def get_advanced_analysis(symbol: str, interval: str = "4h", limit: int = 200):
    """Full market analysis: EMAs, RSI, Stoch RSI, MACD, ADX, Ichimoku,
    support/resistance, candlestick patterns, volume profile, and verdict."""
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        raise HTTPException(503, "Trading service not connected")
    return await trading_service.run_advanced_analysis(symbol, interval, limit)


# ── Platforms ────────────────────────────────────────────────────


@router.get("/tp-profile/{symbol}")
async def get_tp_profile(symbol: str):
    """Historical TP achievement analysis for a symbol."""
    from app.services.trading.trading_service import trading_service
    return await trading_service.get_symbol_tp_profile(symbol)


@router.get("/live-prices")
async def get_live_prices():
    """Get current prices for all open positions — designed for fast polling."""
    from app.services.trading.trading_service import trading_service

    if not trading_service.is_connected:
        return []
    positions = await trading_service.get_positions()
    symbols = list({p["symbol"] for p in positions})
    if not symbols:
        return []
    prices = await trading_service.get_prices(symbols)
    # Return a flat map: { "BTCUSDT": 71123.45, "ETHUSDT": 2218.65, ... }
    price_map = {}
    for p in prices:
        sym = p.get("symbol")
        price_map[sym] = p.get("last") or p.get("bid") or p.get("ask") or 0
    return price_map


@router.get("/platforms")
async def get_platforms():
    from app.services.trading.manager import evaluate_all_platforms

    results = await evaluate_all_platforms()
    best = results[0] if results and results[0].get("score", 0) > 0 else None
    return {
        "platforms": results,
        "recommended": best.get("platform") if best else None,
        "total_configured": len(results),
    }


# ── Trading Settings / Auto-Trade Config ─────────────────────────


class AutoTradeConfigUpdate(BaseModel):
    mode: str | None = None
    min_confidence: float | None = None
    max_risk_per_trade_pct: float | None = None
    max_daily_trades: int | None = None
    max_daily_loss_pct: float | None = None
    require_stop_loss: bool | None = None
    require_take_profit: bool | None = None
    min_risk_reward_ratio: float | None = None
    allowed_symbols: list[str] | None = None
    blocked_symbols: list[str] | None = None
    max_open_positions: int | None = None
    max_position_per_symbol: int | None = None


class RiskCapsUpdate(BaseModel):
    max_sl_pct: float | None = None
    max_tp_pct: float | None = None
    max_position_pct: float | None = None
    breakeven_activate_pct: float | None = None
    trailing_stop_activate_pct: float | None = None
    trailing_stop_distance_pct: float | None = None


@router.get("/settings")
async def get_trading_settings():
    """Get all trading configuration — auto-trade config + risk caps + account info."""
    from app.services.auto_trade_executor import auto_trade_executor
    from app.services.trading.trading_service import trading_service

    await auto_trade_executor.load_from_redis()

    risk_caps = {
        "max_sl_pct": trading_service.MAX_SL_PCT,
        "max_tp_pct": trading_service.MAX_TP_PCT,
        "max_position_pct": trading_service.MAX_POSITION_PCT,
        "breakeven_activate_pct": trading_service.BREAKEVEN_ACTIVATE_PCT,
        "trailing_stop_activate_pct": trading_service.TRAILING_STOP_ACTIVATE_PCT,
        "trailing_stop_distance_pct": trading_service.TRAILING_STOP_DISTANCE_PCT,
    }

    accounts = []
    if trading_service.is_connected:
        try:
            accounts = await trading_service.get_all_accounts()
        except Exception:
            pass

    return {
        "auto_trade": auto_trade_executor.config.to_dict(),
        "auto_trade_daily_stats": {
            "date": auto_trade_executor._daily.date,
            "trades_executed": auto_trade_executor._daily.trades_executed,
            "trades_rejected": auto_trade_executor._daily.trades_rejected,
            "total_pnl": auto_trade_executor._daily.total_pnl,
        },
        "risk_caps": risk_caps,
        "accounts": accounts,
    }


@router.put("/settings/auto-trade")
async def update_auto_trade_config(data: AutoTradeConfigUpdate):
    """Update auto-trade configuration."""
    from app.services.auto_trade_executor import auto_trade_executor

    if data.mode is not None:
        result = await auto_trade_executor.set_mode(data.mode)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

    updates = {k: v for k, v in data.model_dump().items() if v is not None and k != "mode"}
    if updates:
        await auto_trade_executor.update_config(**updates)

    return {"config": auto_trade_executor.config.to_dict(), "message": "Auto-trade config updated"}


@router.put("/settings/risk-caps")
async def update_risk_caps(data: RiskCapsUpdate):
    """Update trading risk cap parameters."""
    from app.services.trading.trading_service import trading_service

    fields_map = {
        "max_sl_pct": ("MAX_SL_PCT", 0.5, 20.0),
        "max_tp_pct": ("MAX_TP_PCT", 1.0, 50.0),
        "max_position_pct": ("MAX_POSITION_PCT", 1.0, 50.0),
        "breakeven_activate_pct": ("BREAKEVEN_ACTIVATE_PCT", 0.1, 5.0),
        "trailing_stop_activate_pct": ("TRAILING_STOP_ACTIVATE_PCT", 0.2, 10.0),
        "trailing_stop_distance_pct": ("TRAILING_STOP_DISTANCE_PCT", 0.1, 5.0),
    }

    updated = {}
    for field_name, (attr, min_val, max_val) in fields_map.items():
        value = getattr(data, field_name, None)
        if value is not None:
            if value < min_val or value > max_val:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field_name} must be between {min_val} and {max_val}",
                )
            setattr(trading_service, attr, value)
            updated[field_name] = value

    return {
        "risk_caps": {
            "max_sl_pct": trading_service.MAX_SL_PCT,
            "max_tp_pct": trading_service.MAX_TP_PCT,
            "max_position_pct": trading_service.MAX_POSITION_PCT,
            "breakeven_activate_pct": trading_service.BREAKEVEN_ACTIVATE_PCT,
            "trailing_stop_activate_pct": trading_service.TRAILING_STOP_ACTIVATE_PCT,
            "trailing_stop_distance_pct": trading_service.TRAILING_STOP_DISTANCE_PCT,
        },
        "updated": updated,
        "message": "Risk caps updated",
    }
