"""Trading API — portfolio, positions, signals, trade execution, analysis."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/trading", tags=["trading"])


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


# ── Platforms ────────────────────────────────────────────────────


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
