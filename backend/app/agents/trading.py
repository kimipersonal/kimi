"""Trading Agent — specialized agent for trading company roles.

Supports three roles:
- market_researcher: Scans markets, gets prices, identifies trends
- analyst: Technical analysis, generates trade signals
- risk_manager: Reviews signals, manages portfolio risk
"""

import json
import logging

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

# ── Tool schemas by role ─────────────────────────────────────────

RESEARCHER_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_market_prices",
            "description": "Get current bid/ask prices for a list of trading symbols.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Symbols, e.g. ['EURUSD','BTCUSDT','GBPUSD']",
                    },
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_candles",
            "description": "Get OHLCV candlestick data for a symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol"},
                    "interval": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                        "description": "Candle interval (default 1h)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of candles, max 500 (default 100)",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_summary",
            "description": "Get account balance and portfolio overview across all connected platforms.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

ANALYST_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_market_prices",
            "description": "Get current bid/ask prices for trading symbols.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of symbols",
                    },
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "technical_analysis",
            "description": (
                "Run full technical analysis on a symbol. Calculates SMA(20/50), "
                "RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14) and "
                "provides trend signals with overall assessment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol"},
                    "interval": {
                        "type": "string",
                        "enum": ["5m", "15m", "30m", "1h", "4h", "1d"],
                        "description": "Analysis timeframe (default 1h)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Candles for analysis, default 100",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "advanced_analysis",
            "description": (
                "TradingView-grade deep analysis. Calculates EMA(9/21/55), SMA(200), "
                "Stochastic RSI, MACD, ADX (trend strength), Ichimoku Cloud, Volume Profile, "
                "Support/Resistance levels, and candlestick patterns (doji, hammer, engulfing). "
                "Returns a weighted VERDICT (STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL) "
                "with a score from -100 to +100. Use 4h or 1d interval for best results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Trading symbol"},
                    "interval": {
                        "type": "string",
                        "enum": ["1h", "4h", "1d"],
                        "description": "Analysis timeframe (default 4h). Use 4h for swing trades, 1d for position trades.",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_technical_analysis",
            "description": (
                "Run technical analysis on MULTIPLE symbols in one call. "
                "Use this instead of calling technical_analysis repeatedly. "
                "Returns analysis for each symbol."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of symbols, e.g. ['BTCUSDT','ETHUSDT','EURUSD']",
                    },
                    "interval": {
                        "type": "string",
                        "enum": ["5m", "15m", "30m", "1h", "4h", "1d"],
                        "description": "Analysis timeframe (default 1h)",
                    },
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_trade_history",
            "description": (
                "Review past trade performance: win rate, P&L, best/worst trades, "
                "win/loss streaks, per-symbol breakdown, avg hold time, avg R:R achieved. "
                "Use this BEFORE creating signals to learn from what worked and what didn't."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Filter by symbol (optional, e.g. 'BTCUSDT'). Leave empty for all symbols.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_signal",
            "description": "Submit a trade signal recommendation for review by Risk Manager.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "direction": {"type": "string", "enum": ["buy", "sell"]},
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0.0–1.0",
                    },
                    "entry_price": {
                        "type": "number",
                        "description": "Recommended entry price",
                    },
                    "stop_loss": {
                        "type": "number",
                        "description": "Stop loss price",
                    },
                    "take_profit": {
                        "type": "number",
                        "description": "Take profit price",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Detailed reasoning for the signal",
                    },
                },
                "required": ["symbol", "direction", "confidence", "reasoning"],
            },
        },
    },
]

RISK_MANAGER_TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Get full portfolio summary: positions, P&L, account balances.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_prices",
            "description": "Get current prices for symbols.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of symbols",
                    },
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pending_signals",
            "description": "Get all pending trade signals awaiting risk review.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_signal",
            "description": "Approve a pending trade signal for execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": "ID of the signal to approve",
                    },
                },
                "required": ["signal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_signal",
            "description": "Reject a pending trade signal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": "ID of the signal",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for rejection",
                    },
                },
                "required": ["signal_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_position_size",
            "description": "Calculate safe position size based on account risk parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "stop_loss_pips": {
                        "type": "number",
                        "description": "Distance to stop loss in pips",
                    },
                    "risk_percent": {
                        "type": "number",
                        "description": "Percent of account to risk (default 1.0)",
                    },
                },
                "required": ["symbol", "side", "stop_loss_pips"],
            },
        },
    },
]

# Historical analysis tool for risk managers
HISTORY_ANALYSIS_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "tp_history_analysis",
            "description": (
                "Analyze historical TP (take-profit) achievement for a symbol. "
                "Shows how often trades actually hit their TP target, what percentage "
                "of TP is typically achieved, and recommended realistic exit points. "
                "Use this BEFORE deciding whether to hold or close a position — "
                "if history says full TP is rarely reached, close at current profit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Trading symbol, e.g. 'BTCUSDT'",
                    },
                },
                "required": ["symbol"],
            },
        },
    },
]

# Tools for trade management — shared by risk_manager and auto_trade_executor
TRADE_MANAGEMENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "close_trade",
            "description": "Close an open trade by its trade ID. Sells the position on the exchange and records the exit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trade_id": {
                        "type": "string",
                        "description": "The trade ID to close (from get_open_trades)",
                    },
                },
                "required": ["trade_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_trades",
            "description": "Get all open trades from the database with their entry price, SL, TP, and current P&L status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_sl_tp",
            "description": "Check all open trades against current market prices and auto-close any that hit stop-loss or take-profit levels. Returns list of closed trades.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_ROLE_SCHEMAS: dict[str, list[dict]] = {
    "market_researcher": RESEARCHER_TOOLS_SCHEMA,
    "analyst": ANALYST_TOOLS_SCHEMA,
    "risk_manager": RISK_MANAGER_TOOLS_SCHEMA + TRADE_MANAGEMENT_TOOLS + HISTORY_ANALYSIS_TOOLS,
    "auto_trade_executor": RISK_MANAGER_TOOLS_SCHEMA + TRADE_MANAGEMENT_TOOLS + HISTORY_ANALYSIS_TOOLS,
}

TRADING_ROLES = {"market_researcher", "analyst", "risk_manager", "auto_trade_executor"}


class TradingAgent(BaseAgent):
    """Specialized agent for trading company roles."""

    def __init__(
        self,
        agent_id: str,
        name: str,
        role: str,
        system_prompt: str,
        model_tier: str = "smart",
        model_id: str | None = None,
        tools: list | None = None,
        company_id: str | None = None,
        sandbox_enabled: bool = False,
        browser_enabled: bool = False,
        skills: list[str] | None = None,
        network_enabled: bool = False,
        standing_instructions: str | None = None,
        work_interval_seconds: int = 3600,
    ):
        # Enable sandbox for analysts (code-based analysis) or if explicitly requested
        super().__init__(
            agent_id=agent_id,
            name=name,
            role=role,
            system_prompt=system_prompt,
            model_tier=model_tier,
            model_id=model_id,
            tools=tools or [],
            sandbox_enabled=sandbox_enabled or (role == "analyst"),
            browser_enabled=browser_enabled,
            skills=skills,
            company_id=company_id,
            standing_instructions=standing_instructions,
            work_interval_seconds=work_interval_seconds,
        )
        self.network_enabled = network_enabled

    def _get_tools_schema(self) -> list[dict]:
        """Return trading tools based on this agent's role."""
        schemas = super()._get_tools_schema()  # base tools (sandbox if enabled)
        schemas.extend(_ROLE_SCHEMAS.get(self.role, RESEARCHER_TOOLS_SCHEMA))
        return schemas

    async def execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute trading tools via the trading service."""
        from app.services.trading.trading_service import trading_service

        if not trading_service.is_connected:
            return json.dumps(
                {"error": "Trading service not connected. No platforms available."}
            )

        try:
            result: object
            match tool_name:
                case "get_market_prices":
                    result = await trading_service.get_prices(
                        arguments.get("symbols", [])
                    )
                case "get_candles":
                    result = await trading_service.get_candles(
                        symbol=arguments["symbol"],
                        interval=arguments.get("interval", "1h"),
                        limit=min(arguments.get("limit", 100), 500),
                    )
                case "get_account_summary" | "get_portfolio":
                    result = await trading_service.get_portfolio_summary()
                case "technical_analysis":
                    result = await trading_service.run_technical_analysis(
                        symbol=arguments["symbol"],
                        interval=arguments.get("interval", "1h"),
                        limit=arguments.get("limit", 100),
                    )
                case "multi_technical_analysis":
                    symbols = arguments.get("symbols", [])
                    interval = arguments.get("interval", "1h")
                    results = {}
                    for sym in symbols[:6]:
                        try:
                            results[sym] = await trading_service.run_technical_analysis(
                                symbol=sym, interval=interval, limit=100,
                            )
                        except Exception as e:
                            results[sym] = {"error": str(e)[:200]}
                    result = results
                case "create_signal":
                    result = await trading_service.create_signal(
                        symbol=arguments["symbol"],
                        direction=arguments["direction"],
                        confidence=arguments.get("confidence", 0.5),
                        entry_price=arguments.get("entry_price"),
                        stop_loss=arguments.get("stop_loss"),
                        take_profit=arguments.get("take_profit"),
                        reasoning=arguments["reasoning"],
                        agent_id=self.agent_id,
                        company_id=self.company_id,
                    )
                case "advanced_analysis":
                    result = await trading_service.run_advanced_analysis(
                        symbol=arguments["symbol"],
                        interval=arguments.get("interval", "4h"),
                    )
                case "review_trade_history":
                    result = await trading_service.get_trade_performance(
                        symbol=arguments.get("symbol"),
                    )
                case "get_pending_signals":
                    result = await trading_service.get_signals(status="pending")
                case "approve_signal":
                    result = await trading_service.approve_signal(
                        arguments["signal_id"], approved_by=self.agent_id
                    )
                    if not result:
                        result = {"error": "Signal not found or not pending"}
                case "reject_signal":
                    result = await trading_service.reject_signal(
                        arguments["signal_id"],
                        reason=arguments.get("reason", "Risk criteria not met"),
                        rejected_by=self.agent_id,
                    )
                    if not result:
                        result = {"error": "Signal not found or not pending"}
                case "calculate_position_size":
                    result = await trading_service.calculate_position_size(
                        symbol=arguments["symbol"],
                        side=arguments["side"],
                        stop_loss_pips=arguments["stop_loss_pips"],
                        risk_percent=arguments.get("risk_percent", 1.0),
                    )
                case "close_trade":
                    # Smart safeguard: if trade is losing, tighten SL instead
                    # of panic-closing. If profitable, allow the close.
                    trade_id = arguments["trade_id"]
                    from app.db.database import async_session as _async_session
                    from app.db.models import Trade as _Trade
                    async with _async_session() as _sess:
                        _trade = await _sess.get(_Trade, trade_id)
                    if _trade and _trade.status == "open" and _trade.entry_price:
                        from datetime import datetime, timezone
                        # Get current price
                        _prices = await trading_service.get_prices([_trade.symbol])
                        _curr = _prices[0].get("last", 0) if _prices else 0
                        if _curr:
                            if _trade.side == "buy":
                                _pnl = (_curr - _trade.entry_price) * _trade.size
                            else:
                                _pnl = (_trade.entry_price - _curr) * _trade.size

                            # If LOSING → don't close, tighten SL instead
                            if _pnl < 0 and _trade.stop_loss:
                                if _trade.side == "buy":
                                    sl_dist = _trade.entry_price - _trade.stop_loss
                                else:
                                    sl_dist = _trade.stop_loss - _trade.entry_price
                                # Tighten SL to 50% of original distance
                                if sl_dist > 0:
                                    if _trade.side == "buy":
                                        new_sl = round(_trade.entry_price - sl_dist * 0.5, 6)
                                        # Only tighten, never widen
                                        if new_sl > _trade.stop_loss:
                                            old_sl = _trade.stop_loss
                                            async with _async_session() as _sess2:
                                                _db_t = await _sess2.get(_Trade, trade_id)
                                                if _db_t:
                                                    _db_t.stop_loss = new_sl
                                                    if not _db_t.metadata_:
                                                        _db_t.metadata_ = {}
                                                    _db_t.metadata_["sl_tightened_by_agent"] = True
                                                    _db_t.metadata_["original_stop_loss"] = _db_t.metadata_.get(
                                                        "original_stop_loss", old_sl
                                                    )
                                                    from sqlalchemy.orm.attributes import flag_modified
                                                    flag_modified(_db_t, "metadata_")
                                                    await _sess2.commit()
                                            result = {
                                                "action": "sl_tightened",
                                                "message": f"Trade is losing ${abs(_pnl):.2f}. "
                                                f"Instead of closing at a loss, SL tightened: "
                                                f"{old_sl:.2f} → {new_sl:.2f} (50% closer). "
                                                f"SL monitor will auto-close if price keeps falling.",
                                                "trade_id": trade_id,
                                                "old_sl": old_sl,
                                                "new_sl": new_sl,
                                                "current_pnl": round(_pnl, 2),
                                            }
                                            return json.dumps(result, default=str)
                                    else:  # sell
                                        new_sl = round(_trade.entry_price + sl_dist * 0.5, 6)
                                        if new_sl < _trade.stop_loss:
                                            old_sl = _trade.stop_loss
                                            async with _async_session() as _sess2:
                                                _db_t = await _sess2.get(_Trade, trade_id)
                                                if _db_t:
                                                    _db_t.stop_loss = new_sl
                                                    if not _db_t.metadata_:
                                                        _db_t.metadata_ = {}
                                                    _db_t.metadata_["sl_tightened_by_agent"] = True
                                                    _db_t.metadata_["original_stop_loss"] = _db_t.metadata_.get(
                                                        "original_stop_loss", old_sl
                                                    )
                                                    from sqlalchemy.orm.attributes import flag_modified
                                                    flag_modified(_db_t, "metadata_")
                                                    await _sess2.commit()
                                            result = {
                                                "action": "sl_tightened",
                                                "message": f"Trade is losing ${abs(_pnl):.2f}. "
                                                f"Instead of closing at a loss, SL tightened: "
                                                f"{old_sl:.2f} → {new_sl:.2f} (50% closer). "
                                                f"SL monitor will auto-close if price keeps falling.",
                                                "trade_id": trade_id,
                                                "old_sl": old_sl,
                                                "new_sl": new_sl,
                                                "current_pnl": round(_pnl, 2),
                                            }
                                            return json.dumps(result, default=str)
                    # If profitable or no SL to tighten → allow close
                    result = await trading_service.close_trade(
                        trade_id=trade_id,
                    )
                case "get_open_trades":
                    result = await trading_service.get_trade_history(limit=50)
                    result = [t for t in result if t.get("status") == "open"]
                case "check_sl_tp":
                    result = await trading_service.check_open_trades()
                    if not result:
                        result = {"message": "No trades hit SL/TP levels"}
                case "tp_history_analysis":
                    result = await trading_service.get_symbol_tp_profile(
                        symbol=arguments["symbol"]
                    )
                case _:
                    # Delegate to base class for sandbox/browser tools
                    return await super().execute_tool(tool_name, arguments)

            return json.dumps(result, default=str)
        except Exception as e:
            logger.error(f"Trading tool {tool_name} error: {e}")
            return json.dumps({"error": str(e)[:200]})
