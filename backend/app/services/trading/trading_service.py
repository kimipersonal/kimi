"""Trading Service — orchestrates exchange connections, trade execution, and portfolio tracking."""

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select

from app.db.database import async_session
from app.db.models import Trade, TradeSignal
from app.services.event_bus import event_bus
from app.services.trading.base import (
    BaseTradingConnector,
    OrderSide,
    OrderType,
)

logger = logging.getLogger(__name__)


class TradingService:
    """Singleton service orchestrating all trading operations."""

    def __init__(self) -> None:
        self._connectors: dict[str, BaseTradingConnector] = {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and len(self._connectors) > 0

    @property
    def platforms(self) -> list[str]:
        return list(self._connectors.keys())

    async def startup(self) -> None:
        """Connect to all configured trading platforms."""
        from app.services.trading.manager import get_configured_connectors

        connectors = get_configured_connectors()
        for connector in connectors:
            try:
                ok = await connector.connect()
                if ok:
                    self._connectors[connector.platform_name] = connector
                    logger.info(f"Trading: connected to {connector.platform_name}")
                else:
                    logger.warning(
                        f"Trading: failed to connect to {connector.platform_name}"
                    )
            except Exception as e:
                logger.error(
                    f"Trading: error connecting to {connector.platform_name}: {e}"
                )

        self._connected = len(self._connectors) > 0
        if self._connected:
            logger.info(
                f"Trading service started with {len(self._connectors)} platform(s): "
                f"{', '.join(self._connectors.keys())}"
            )
        else:
            logger.warning("Trading service started with no connected platforms")

    async def shutdown(self) -> None:
        """Disconnect from all platforms."""
        for name, connector in self._connectors.items():
            try:
                await connector.disconnect()
                logger.info(f"Trading: disconnected from {name}")
            except Exception as e:
                logger.error(f"Trading: error disconnecting from {name}: {e}")
        self._connectors.clear()
        self._connected = False

    def _find_connector(self, symbol: str | None = None) -> BaseTradingConnector:
        """Find the best connector for a symbol."""
        if not self._connectors:
            raise RuntimeError("No trading platforms connected")

        if symbol and len(self._connectors) > 1:
            clean = symbol.replace("/", "").replace("_", "").upper()
            # Crypto symbols → Binance
            if any(clean.endswith(q) for q in ("USDT", "BTC", "ETH", "BNB", "BUSD")):
                if "Binance Testnet" in self._connectors:
                    return self._connectors["Binance Testnet"]
            # Forex/stocks → OANDA, Capital.com, or MetaAPI
            if any(
                clean.startswith(c)
                for c in ("EUR", "GBP", "USD", "JPY", "AUD", "CHF", "CAD", "NZD")
            ):
                if "OANDA" in self._connectors:
                    return self._connectors["OANDA"]
                if "Capital.com" in self._connectors:
                    return self._connectors["Capital.com"]
                if "MetaAPI (MT5)" in self._connectors:
                    return self._connectors["MetaAPI (MT5)"]

        return next(iter(self._connectors.values()))

    # ── Market Data ──────────────────────────────────────────────

    async def get_prices(self, symbols: list[str]) -> list[dict]:
        """Get current prices for multiple symbols."""
        results: list[dict] = []
        for sym in symbols:
            try:
                connector = self._find_connector(sym)
                ticker = await connector.get_ticker(sym)
                results.append(
                    {
                        "symbol": ticker.symbol,
                        "bid": ticker.bid,
                        "ask": ticker.ask,
                        "last": ticker.last,
                        "spread": round(ticker.spread, 5),
                        "platform": connector.platform_name,
                    }
                )
            except Exception as e:
                results.append({"symbol": sym, "error": str(e)[:100]})
        return results

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[dict]:
        """Get OHLCV candle data for a symbol."""
        connector = self._find_connector(symbol)
        candles = await connector.get_candles(symbol, interval, limit)
        return [
            {
                "timestamp": c.timestamp.isoformat(),
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]

    async def get_account_info(self, platform: str | None = None) -> dict:
        """Get account info from a specific or default platform."""
        if platform and platform in self._connectors:
            connector = self._connectors[platform]
        else:
            connector = self._find_connector()
        info = await connector.get_account_info()
        return {
            "platform": info.platform,
            "account_id": info.account_id,
            "balance": info.balance,
            "equity": info.equity,
            "currency": info.currency,
            "leverage": info.leverage,
            "open_positions": info.open_positions,
            "is_demo": info.is_demo,
        }

    async def get_all_accounts(self) -> list[dict]:
        """Get account info from all connected platforms."""
        accounts: list[dict] = []
        for name, connector in self._connectors.items():
            try:
                info = await connector.get_account_info()
                accounts.append(
                    {
                        "platform": info.platform,
                        "account_id": info.account_id,
                        "balance": info.balance,
                        "equity": info.equity,
                        "currency": info.currency,
                        "is_demo": info.is_demo,
                    }
                )
            except Exception as e:
                accounts.append({"platform": name, "error": str(e)[:100]})
        return accounts

    # ── Portfolio ─────────────────────────────────────────────────

    async def get_positions(self) -> list[dict]:
        """Get all open positions from all platforms."""
        positions: list[dict] = []
        for name, connector in self._connectors.items():
            try:
                for p in await connector.get_positions():
                    positions.append(
                        {
                            "id": p.id,
                            "symbol": p.symbol,
                            "side": p.side.value,
                            "size": p.size,
                            "entry_price": p.entry_price,
                            "current_price": p.current_price,
                            "unrealized_pnl": p.unrealized_pnl,
                            "platform": name,
                        }
                    )
            except Exception as e:
                logger.error(f"Error getting positions from {name}: {e}")
        return positions

    async def get_portfolio_summary(self) -> dict:
        """Get portfolio overview: accounts + positions + P&L."""
        accounts = await self.get_all_accounts()
        positions = await self.get_positions()
        total_balance = sum(a.get("balance", 0) for a in accounts if "error" not in a)
        total_equity = sum(a.get("equity", 0) for a in accounts if "error" not in a)
        unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

        async with async_session() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == "closed")
            )
            closed_trades = result.scalars().all()
        realized_pnl = sum(t.pnl or 0 for t in closed_trades)

        return {
            "accounts": accounts,
            "positions": positions,
            "total_balance": round(total_balance, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "open_positions_count": len(positions),
            "platforms_connected": len(self._connectors),
        }

    # ── Technical Analysis ────────────────────────────────────────

    async def run_technical_analysis(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> dict:
        """Run technical analysis on a symbol using pandas-ta."""
        import pandas as pd
        import pandas_ta as ta  # noqa: F401 — registers .ta accessor

        connector = self._find_connector(symbol)
        candles = await connector.get_candles(symbol, interval, limit)
        if len(candles) < 20:
            return {"error": f"Not enough data: {len(candles)} candles (need ≥20)"}

        df = pd.DataFrame(
            [
                {
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ]
        )
        df.set_index("timestamp", inplace=True)

        df.ta.sma(length=20, append=True)
        df.ta.sma(length=50, append=True)
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.atr(length=14, append=True)

        latest = df.iloc[-1]
        current_price = float(latest["close"])

        def val(v: object) -> float | None:
            try:
                f = float(v)  # type: ignore[arg-type]
                return round(f, 5) if not pd.isna(f) else None
            except (TypeError, ValueError):
                return None

        sma20 = val(latest.get("SMA_20"))
        sma50 = val(latest.get("SMA_50"))
        rsi = val(latest.get("RSI_14"))
        macd_val = val(latest.get("MACD_12_26_9"))
        macd_signal = val(latest.get("MACDs_12_26_9"))
        macd_hist = val(latest.get("MACDh_12_26_9"))
        bb_upper = val(latest.get("BBU_20_2.0"))
        bb_middle = val(latest.get("BBM_20_2.0"))
        bb_lower = val(latest.get("BBL_20_2.0"))
        atr = val(latest.get("ATRr_14"))

        signals: list[str] = []
        if sma20 is not None and sma50 is not None:
            if sma20 > sma50:
                signals.append("SMA crossover: BULLISH (20 > 50)")
            else:
                signals.append("SMA crossover: BEARISH (20 < 50)")
        if rsi is not None:
            if rsi > 70:
                signals.append(f"RSI: OVERBOUGHT ({rsi:.1f})")
            elif rsi < 30:
                signals.append(f"RSI: OVERSOLD ({rsi:.1f})")
            else:
                signals.append(f"RSI: NEUTRAL ({rsi:.1f})")
        if macd_val is not None and macd_signal is not None:
            if macd_val > macd_signal:
                signals.append("MACD: BULLISH (above signal)")
            else:
                signals.append("MACD: BEARISH (below signal)")
        if bb_upper is not None and bb_lower is not None:
            if current_price > bb_upper:
                signals.append("Bollinger: ABOVE upper band (potential reversal)")
            elif current_price < bb_lower:
                signals.append("Bollinger: BELOW lower band (potential reversal)")
            else:
                signals.append("Bollinger: Within bands")

        bullish = sum(1 for s in signals if "BULLISH" in s or "OVERSOLD" in s)
        bearish = sum(1 for s in signals if "BEARISH" in s or "OVERBOUGHT" in s)
        overall = (
            "BULLISH"
            if bullish > bearish
            else ("BEARISH" if bearish > bullish else "NEUTRAL")
        )

        return {
            "symbol": symbol,
            "interval": interval,
            "candles_analyzed": len(candles),
            "current_price": current_price,
            "indicators": {
                "sma_20": sma20,
                "sma_50": sma50,
                "rsi_14": rsi,
                "macd": macd_val,
                "macd_signal": macd_signal,
                "macd_histogram": macd_hist,
                "bollinger_upper": bb_upper,
                "bollinger_middle": bb_middle,
                "bollinger_lower": bb_lower,
                "atr_14": atr,
            },
            "signals": signals,
            "overall_trend": overall,
            "price_range": {
                "high": round(float(df["high"].max()), 5),
                "low": round(float(df["low"].min()), 5),
                "avg_volume": round(float(df["volume"].mean()), 2),
            },
        }

    # ── Trade Signals ─────────────────────────────────────────────

    async def create_signal(
        self,
        symbol: str,
        direction: str,
        confidence: float,
        entry_price: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        reasoning: str,
        agent_id: str,
        company_id: str | None = None,
    ) -> dict:
        """Create a trade signal in DB."""
        async with async_session() as session:
            signal = TradeSignal(
                symbol=symbol,
                direction=direction,
                confidence=confidence,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reasoning=reasoning,
                agent_id=agent_id,
                company_id=company_id,
            )
            session.add(signal)
            await session.commit()
            await session.refresh(signal)
            result = {
                "id": signal.id,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "confidence": signal.confidence,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "reasoning": signal.reasoning,
                "status": signal.status,
                "created_at": signal.created_at.isoformat(),
            }

        await event_bus.broadcast("trade_signal", result, agent_id=agent_id)
        return result

    async def get_signals(
        self, status: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Get trade signals from DB."""
        async with async_session() as session:
            q = select(TradeSignal).order_by(desc(TradeSignal.created_at)).limit(limit)
            if status:
                q = q.where(TradeSignal.status == status)
            result = await session.execute(q)
            return [
                {
                    "id": s.id,
                    "symbol": s.symbol,
                    "direction": s.direction,
                    "confidence": s.confidence,
                    "entry_price": s.entry_price,
                    "stop_loss": s.stop_loss,
                    "take_profit": s.take_profit,
                    "reasoning": s.reasoning,
                    "status": s.status,
                    "agent_id": s.agent_id,
                    "approved_by": s.approved_by,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in result.scalars().all()
            ]

    async def approve_signal(
        self, signal_id: str, approved_by: str = "owner"
    ) -> dict | None:
        """Approve a pending trade signal."""
        async with async_session() as session:
            signal = await session.get(TradeSignal, signal_id)
            if not signal or signal.status != "pending":
                return None
            signal.status = "approved"
            signal.approved_by = approved_by
            signal.decided_at = datetime.now(timezone.utc)
            await session.commit()
            return {"id": signal.id, "symbol": signal.symbol, "status": "approved"}

    async def reject_signal(
        self, signal_id: str, reason: str, rejected_by: str = "owner"
    ) -> dict | None:
        """Reject a pending trade signal."""
        async with async_session() as session:
            signal = await session.get(TradeSignal, signal_id)
            if not signal or signal.status != "pending":
                return None
            signal.status = "rejected"
            signal.approved_by = rejected_by
            signal.decided_at = datetime.now(timezone.utc)
            await session.commit()
            return {
                "id": signal.id,
                "symbol": signal.symbol,
                "status": "rejected",
                "reason": reason,
            }

    # ── Trade Execution ───────────────────────────────────────────

    async def execute_trade(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "market",
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        signal_id: str | None = None,
        agent_id: str | None = None,
        company_id: str | None = None,
    ) -> dict:
        """Execute a trade on the exchange and record in DB."""
        connector = self._find_connector(symbol)
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        o_type = {
            "market": OrderType.MARKET,
            "limit": OrderType.LIMIT,
            "stop": OrderType.STOP,
        }.get(order_type.lower(), OrderType.MARKET)

        order = await connector.place_order(
            symbol=symbol,
            side=order_side,
            size=size,
            order_type=o_type,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        async with async_session() as session:
            trade = Trade(
                platform=connector.platform_name,
                symbol=symbol,
                side=side.lower(),
                size=size,
                entry_price=order.filled_price or order.price or 0,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status="open" if order.status.value == "filled" else order.status.value,
                signal_id=signal_id,
                agent_id=agent_id,
                company_id=company_id,
                external_order_id=order.id,
            )
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            trade_id = trade.id

        if signal_id:
            async with async_session() as session:
                sig = await session.get(TradeSignal, signal_id)
                if sig:
                    sig.status = "executed"
                    await session.commit()

        result = {
            "trade_id": trade_id,
            "order_id": order.id,
            "symbol": symbol,
            "side": side,
            "size": size,
            "filled_price": order.filled_price,
            "status": order.status.value,
            "platform": connector.platform_name,
        }
        await event_bus.broadcast("trade_executed", result, agent_id=agent_id)
        return result

    async def close_trade(self, position_id: str, platform: str | None = None) -> dict:
        """Close an open position."""
        if platform and platform in self._connectors:
            connector = self._connectors[platform]
        else:
            connector = self._find_connector()

        order = await connector.close_position(position_id)
        return {
            "position_id": position_id,
            "status": order.status.value,
            "platform": connector.platform_name,
        }

    async def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Get trade history from DB."""
        async with async_session() as session:
            result = await session.execute(
                select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
            )
            return [
                {
                    "id": t.id,
                    "platform": t.platform,
                    "symbol": t.symbol,
                    "side": t.side,
                    "size": t.size,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "stop_loss": t.stop_loss,
                    "take_profit": t.take_profit,
                    "pnl": t.pnl,
                    "status": t.status,
                    "signal_id": t.signal_id,
                    "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                }
                for t in result.scalars().all()
            ]

    # ── Risk ──────────────────────────────────────────────────────

    async def calculate_position_size(
        self,
        symbol: str,
        side: str,
        stop_loss_pips: float,
        risk_percent: float = 1.0,
    ) -> dict:
        """Calculate position size based on risk parameters."""
        try:
            connector = self._find_connector(symbol)
            account = await connector.get_account_info()
            ticker = await connector.get_ticker(symbol)

            risk_amount = account.balance * (risk_percent / 100)
            clean = symbol.replace("/", "").replace("_", "").upper()
            pip_value = 0.01 if "JPY" in clean else 0.0001

            position_size = (
                risk_amount / (stop_loss_pips * pip_value) if stop_loss_pips > 0 else 0
            )

            return {
                "symbol": symbol,
                "account_balance": account.balance,
                "risk_percent": risk_percent,
                "risk_amount": round(risk_amount, 2),
                "stop_loss_pips": stop_loss_pips,
                "recommended_size": round(position_size, 2),
                "current_price": ticker.last,
            }
        except Exception as e:
            return {"error": str(e)[:200]}


# Global singleton
trading_service = TradingService()
