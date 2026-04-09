"""Trading Service — orchestrates exchange connections, trade execution, and portfolio tracking."""

import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import desc, select, and_

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
        """Get all open positions from all platforms, enriched with DB & live data."""
        # 1. Get raw positions from connectors
        raw_positions: list[dict] = []
        for name, connector in self._connectors.items():
            try:
                for p in await connector.get_positions():
                    raw_positions.append(
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

        if not raw_positions:
            return []

        # 2. Enrich with entry prices from DB (open trades)
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Trade).where(Trade.status == "open")
                )
                open_trades = {t.symbol: t for t in result.scalars().all()}
        except Exception:
            open_trades = {}

        # 3. Fetch live prices for all position symbols
        symbols = [p["symbol"] for p in raw_positions]
        try:
            price_list = await self.get_prices(symbols)
            live_prices = {p["symbol"]: p for p in price_list if "error" not in p}
        except Exception:
            live_prices = {}

        # 4. Merge everything
        for pos in raw_positions:
            sym = pos["symbol"]
            db_trade = open_trades.get(sym)

            # Entry price + trade_id from DB trade
            if db_trade:
                pos["trade_id"] = db_trade.id
                if not pos["entry_price"] or pos["entry_price"] == 0:
                    pos["entry_price"] = db_trade.entry_price
                pos["stop_loss"] = db_trade.stop_loss
                pos["take_profit"] = db_trade.take_profit

            # Current price from live data
            price_data = live_prices.get(sym, {})
            current = price_data.get("last") or price_data.get("bid", 0)
            if current:
                pos["current_price"] = current

            # Calculate unrealized PnL
            if pos["entry_price"] and pos["current_price"]:
                if pos["side"] == "buy":
                    pos["unrealized_pnl"] = round(
                        (pos["current_price"] - pos["entry_price"]) * pos["size"], 2
                    )
                else:
                    pos["unrealized_pnl"] = round(
                        (pos["entry_price"] - pos["current_price"]) * pos["size"], 2
                    )

        return raw_positions

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

    async def run_advanced_analysis(
        self, symbol: str, interval: str = "4h", limit: int = 200
    ) -> dict:
        """TradingView-grade analysis: EMA, Stoch RSI, VWAP, Ichimoku, S/R levels,
        volume profile, and multi-timeframe confirmation."""
        import pandas as pd
        import pandas_ta as ta  # noqa: F401

        connector = self._find_connector(symbol)
        candles = await connector.get_candles(symbol, interval, limit)
        if len(candles) < 30:
            return {"error": f"Not enough data: {len(candles)} candles (need ≥52)"}

        df = pd.DataFrame([
            {"timestamp": c.timestamp, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ])
        df.set_index("timestamp", inplace=True)

        def val(v: object) -> float | None:
            try:
                f = float(v)
                return round(f, 5) if not pd.isna(f) else None
            except (TypeError, ValueError):
                return None

        # ── Core Indicators ───────────────────────────────────
        df.ta.ema(length=9, append=True)
        df.ta.ema(length=21, append=True)
        df.ta.ema(length=55, append=True)
        df.ta.sma(length=200, append=True)
        df.ta.rsi(length=14, append=True)
        df.ta.stochrsi(length=14, rsi_length=14, k=3, d=3, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.atr(length=14, append=True)
        df.ta.adx(length=14, append=True)

        # ── Volume Analysis ───────────────────────────────────
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        latest = df.iloc[-1]
        prev = df.iloc[-2]

        vol_ratio = (
            round(float(latest["volume"]) / float(latest["vol_sma20"]), 2)
            if latest.get("vol_sma20") and float(latest["vol_sma20"]) > 0
            else None
        )

        # ── Support & Resistance (pivot-based) ────────────────
        recent = df.tail(50)
        highs = recent["high"].nlargest(5).values.tolist()
        lows = recent["low"].nsmallest(5).values.tolist()
        resistance_levels = sorted(set(round(h, 2) for h in highs), reverse=True)[:3]
        support_levels = sorted(set(round(l, 2) for l in lows))[:3]

        # ── Price Action Patterns ─────────────────────────────
        current_price = float(latest["close"])
        prev_close = float(prev["close"])
        candle_body = abs(float(latest["close"]) - float(latest["open"]))
        candle_range = float(latest["high"]) - float(latest["low"])
        body_ratio = round(candle_body / candle_range, 2) if candle_range > 0 else 0

        # Detect doji, hammer, engulfing
        patterns = []
        if body_ratio < 0.1:
            patterns.append("doji (indecision)")
        elif body_ratio < 0.3:
            lower_wick = min(float(latest["open"]), float(latest["close"])) - float(latest["low"])
            if lower_wick > candle_body * 2:
                patterns.append("hammer (bullish reversal)")
        if float(latest["close"]) > float(latest["open"]):
            if float(prev["close"]) < float(prev["open"]):
                if float(latest["close"]) > float(prev["open"]) and float(latest["open"]) < float(prev["close"]):
                    patterns.append("bullish engulfing")
        elif float(latest["close"]) < float(latest["open"]):
            if float(prev["close"]) > float(prev["open"]):
                if float(latest["close"]) < float(prev["open"]) and float(latest["open"]) > float(prev["close"]):
                    patterns.append("bearish engulfing")

        # Three consecutive same-direction candles
        last3 = df.tail(3)
        if all(float(last3.iloc[i]["close"]) > float(last3.iloc[i]["open"]) for i in range(3)):
            patterns.append("three white soldiers (strong bullish)")
        elif all(float(last3.iloc[i]["close"]) < float(last3.iloc[i]["open"]) for i in range(3)):
            patterns.append("three black crows (strong bearish)")

        # ── Ichimoku Cloud ────────────────────────────────────
        high_9 = df["high"].rolling(9).max()
        low_9 = df["low"].rolling(9).min()
        high_26 = df["high"].rolling(26).max()
        low_26 = df["low"].rolling(26).min()
        high_52 = df["high"].rolling(52).max()
        low_52 = df["low"].rolling(52).min()
        tenkan = (high_9 + low_9) / 2
        kijun = (high_26 + low_26) / 2
        senkou_a = ((tenkan + kijun) / 2).shift(26)
        senkou_b = ((high_52 + low_52) / 2).shift(26)

        ichimoku_signal = "NEUTRAL"
        sa = val(senkou_a.iloc[-1])
        sb = val(senkou_b.iloc[-1])
        if sa is not None and sb is not None:
            cloud_top = max(sa, sb)
            cloud_bottom = min(sa, sb)
            if current_price > cloud_top:
                ichimoku_signal = "BULLISH (above cloud)"
            elif current_price < cloud_bottom:
                ichimoku_signal = "BEARISH (below cloud)"
            else:
                ichimoku_signal = "INSIDE CLOUD (no trade)"

        # ── Signal Scoring ────────────────────────────────────
        bull_points = 0
        bear_points = 0
        total_weight = 0

        ema9 = val(latest.get("EMA_9"))
        ema21 = val(latest.get("EMA_21"))
        ema55 = val(latest.get("EMA_55"))
        sma200 = val(latest.get("SMA_200"))
        rsi = val(latest.get("RSI_14"))
        stoch_k = val(latest.get("STOCHRSIk_14_14_3_3"))
        stoch_d = val(latest.get("STOCHRSId_14_14_3_3"))
        adx = val(latest.get("ADX_14"))
        macd_val = val(latest.get("MACD_12_26_9"))
        macd_sig = val(latest.get("MACDs_12_26_9"))
        atr = val(latest.get("ATRr_14"))

        # EMA alignment (weight 2)
        if ema9 and ema21 and ema55:
            total_weight += 2
            if ema9 > ema21 > ema55:
                bull_points += 2
            elif ema9 < ema21 < ema55:
                bear_points += 2

        # Price vs SMA200 (weight 1)
        if sma200:
            total_weight += 1
            if current_price > sma200:
                bull_points += 1
            else:
                bear_points += 1

        # RSI (weight 1)
        if rsi:
            total_weight += 1
            if rsi > 60:
                bull_points += 1
            elif rsi < 40:
                bear_points += 1

        # Stochastic RSI (weight 1)
        if stoch_k and stoch_d:
            total_weight += 1
            if stoch_k > stoch_d and stoch_k < 80:
                bull_points += 1
            elif stoch_k < stoch_d and stoch_k > 20:
                bear_points += 1

        # MACD (weight 1)
        if macd_val is not None and macd_sig is not None:
            total_weight += 1
            if macd_val > macd_sig:
                bull_points += 1
            else:
                bear_points += 1

        # Ichimoku (weight 2)
        total_weight += 2
        if "BULLISH" in ichimoku_signal:
            bull_points += 2
        elif "BEARISH" in ichimoku_signal:
            bear_points += 2

        # Volume confirmation (weight 1)
        if vol_ratio and vol_ratio > 1.2:
            total_weight += 1
            if current_price > prev_close:
                bull_points += 1
            else:
                bear_points += 1

        score = ((bull_points - bear_points) / total_weight * 100) if total_weight > 0 else 0
        if score > 30:
            verdict = "STRONG BUY"
        elif score > 10:
            verdict = "BUY"
        elif score < -30:
            verdict = "STRONG SELL"
        elif score < -10:
            verdict = "SELL"
        else:
            verdict = "NEUTRAL"

        trend_strength = "WEAK"
        if adx:
            if adx > 40:
                trend_strength = "VERY STRONG"
            elif adx > 25:
                trend_strength = "STRONG"
            elif adx > 20:
                trend_strength = "MODERATE"

        return {
            "symbol": symbol,
            "interval": interval,
            "candles_analyzed": len(candles),
            "current_price": current_price,
            "verdict": verdict,
            "score": round(score, 1),
            "trend_strength": trend_strength,
            "indicators": {
                "ema_9": ema9, "ema_21": ema21, "ema_55": ema55,
                "sma_200": sma200,
                "rsi_14": rsi,
                "stoch_rsi_k": stoch_k, "stoch_rsi_d": stoch_d,
                "macd": macd_val, "macd_signal": macd_sig,
                "adx": adx,
                "atr_14": atr,
            },
            "ichimoku": ichimoku_signal,
            "volume": {
                "current": round(float(latest["volume"]), 2),
                "avg_20": round(float(latest["vol_sma20"]), 2) if latest.get("vol_sma20") and not pd.isna(latest["vol_sma20"]) else None,
                "ratio": vol_ratio,
                "signal": "HIGH" if vol_ratio and vol_ratio > 1.5 else ("NORMAL" if vol_ratio and vol_ratio > 0.7 else "LOW"),
            },
            "price_action": {
                "patterns": patterns if patterns else ["none detected"],
                "candle_body_ratio": body_ratio,
            },
            "support_resistance": {
                "resistance": resistance_levels,
                "support": support_levels,
            },
            "scoring": {
                "bull_points": bull_points,
                "bear_points": bear_points,
                "total_weight": total_weight,
            },
        }

    async def get_trade_performance(self, symbol: str | None = None) -> dict:
        """Analyze past trade history — win rates, best/worst, streaks, per-symbol stats."""
        async with async_session() as session:
            query = select(Trade).where(Trade.status == "closed").order_by(desc(Trade.closed_at))
            if symbol:
                query = query.where(Trade.symbol == symbol.upper())
            result = await session.execute(query)
            trades = result.scalars().all()

        if not trades:
            return {"message": "No closed trades found", "total_trades": 0}

        wins = [t for t in trades if (t.pnl or 0) > 0]
        losses = [t for t in trades if (t.pnl or 0) < 0]
        breakeven = [t for t in trades if (t.pnl or 0) == 0]

        total_pnl = sum(t.pnl or 0 for t in trades)
        gross_profit = sum(t.pnl or 0 for t in wins)
        gross_loss = abs(sum(t.pnl or 0 for t in losses))

        # Per-symbol breakdown
        by_symbol: dict[str, dict] = {}
        for t in trades:
            s = t.symbol or "UNKNOWN"
            if s not in by_symbol:
                by_symbol[s] = {"trades": 0, "wins": 0, "pnl": 0.0}
            by_symbol[s]["trades"] += 1
            if (t.pnl or 0) > 0:
                by_symbol[s]["wins"] += 1
            by_symbol[s]["pnl"] += t.pnl or 0

        symbol_stats = {
            sym: {
                "trades": d["trades"],
                "win_rate": round(d["wins"] / d["trades"] * 100, 1),
                "total_pnl": round(d["pnl"], 2),
            }
            for sym, d in by_symbol.items()
        }

        # Win/loss streak
        streak = 0
        streak_type = None
        for t in trades:
            p = t.pnl or 0
            if p > 0:
                if streak_type == "win":
                    streak += 1
                else:
                    streak = 1
                    streak_type = "win"
            elif p < 0:
                if streak_type == "loss":
                    streak += 1
                else:
                    streak = 1
                    streak_type = "loss"
            # skip breakeven for streak

        # Best / worst trade
        best = max(trades, key=lambda t: t.pnl or 0)
        worst = min(trades, key=lambda t: t.pnl or 0)

        # Average hold time
        hold_times = []
        for t in trades:
            if t.opened_at and t.closed_at:
                hold_times.append((t.closed_at - t.opened_at).total_seconds() / 3600)
        avg_hold_hours = round(sum(hold_times) / len(hold_times), 1) if hold_times else None

        # Average R:R achieved
        rr_list = []
        for t in trades:
            if t.entry_price and t.stop_loss and t.exit_price:
                risk = abs(t.entry_price - t.stop_loss)
                reward = abs(t.exit_price - t.entry_price)
                if risk > 0:
                    rr_list.append(round(reward / risk, 2))

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0,
            "best_trade": {
                "symbol": best.symbol, "pnl": round(best.pnl or 0, 2),
                "side": best.side, "date": best.closed_at.isoformat() if best.closed_at else None,
            },
            "worst_trade": {
                "symbol": worst.symbol, "pnl": round(worst.pnl or 0, 2),
                "side": worst.side, "date": worst.closed_at.isoformat() if worst.closed_at else None,
            },
            "current_streak": f"{streak} {streak_type}" if streak_type else "none",
            "avg_hold_hours": avg_hold_hours,
            "avg_rr_achieved": round(sum(rr_list) / len(rr_list), 2) if rr_list else None,
            "by_symbol": symbol_stats,
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

        # Evaluate signal for auto-execution (skip manual approval for qualifying signals)
        auto_executed = False
        try:
            from app.services.auto_trade_executor import auto_trade_executor, AutoTradeMode
            if auto_trade_executor.config.mode != AutoTradeMode.DISABLED:
                evaluation = await auto_trade_executor.evaluate_signal(result)
                if evaluation.get("approved"):
                    execution = await auto_trade_executor.try_auto_execute(result)
                    if execution.get("executed"):
                        result["status"] = "executed"
                        result["auto_executed"] = True
                        result["trade"] = execution.get("trade_result")
                        auto_executed = True
                        logger.info(
                            f"Signal {result['id']} auto-executed: "
                            f"{symbol} {direction}"
                        )
        except Exception as e:
            logger.debug(f"Auto-execute evaluation skipped: {e}")

        # If not auto-executed, signal needs Risk Manager review — trigger immediately
        if not auto_executed and result.get("status") == "pending":
            await event_bus.broadcast(
                "signal_needs_review",
                {
                    "signal_id": result["id"],
                    "symbol": symbol,
                    "direction": direction,
                    "confidence": confidence,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "reasoning": reasoning[:200],
                    "created_at": result["created_at"],
                },
                agent_id=agent_id,
            )
            logger.info(
                f"Signal {result['id']} needs review — event dispatched "
                f"({symbol} {direction} conf={confidence})"
            )

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

    # Signal expiry TTL — signals older than this are stale and should not execute
    SIGNAL_TTL_MINUTES = 5

    async def approve_signal(
        self, signal_id: str, approved_by: str = "owner"
    ) -> dict | None:
        """Approve a pending trade signal and execute it.

        When a Risk Manager manually approves a signal, we execute it directly
        without re-checking auto_trade_executor confidence thresholds — the
        human/agent already decided it's good enough.
        """
        async with async_session() as session:
            signal = await session.get(TradeSignal, signal_id)
            if not signal or signal.status != "pending":
                return None

            # Check if signal has expired (price has likely moved)
            age = datetime.now(timezone.utc) - signal.created_at
            if age > timedelta(minutes=self.SIGNAL_TTL_MINUTES):
                signal.status = "expired"
                signal.decided_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info(
                    f"Signal {signal_id} expired — {age.total_seconds():.0f}s old "
                    f"(TTL={self.SIGNAL_TTL_MINUTES}min)"
                )
                return {
                    "id": signal_id,
                    "symbol": signal.symbol,
                    "status": "expired",
                    "age_seconds": age.total_seconds(),
                    "note": f"Signal expired after {self.SIGNAL_TTL_MINUTES} minutes — price may have moved",
                }
            signal.status = "approved"
            signal.approved_by = approved_by
            signal.decided_at = datetime.now(timezone.utc)
            await session.commit()

            signal_dict = {
                "id": signal.id,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "confidence": signal.confidence,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "agent_id": signal.agent_id,
                "company_id": signal.company_id,
            }

        # Execute directly — Risk Manager already approved, skip auto_executor criteria.
        # Only check that we actually have a connector for this symbol.
        try:
            self._find_connector(signal_dict["symbol"])

            # Calculate position size based on risk
            try:
                from app.services.position_calculator import position_calculator
                sizing = await position_calculator.calculate_size(
                    symbol=signal_dict["symbol"],
                    entry_price=signal_dict.get("entry_price") or 0,
                    stop_loss=signal_dict.get("stop_loss") or 0,
                    risk_pct=1.0,
                )
                size = sizing.get("recommended_size", 0.01)
            except Exception:
                size = 0.01

            trade_result = await self.execute_trade(
                symbol=signal_dict["symbol"],
                side=signal_dict["direction"],
                size=size,
                order_type="market",
                stop_loss=signal_dict.get("stop_loss"),
                take_profit=signal_dict.get("take_profit"),
                signal_id=signal_dict["id"],
                agent_id=signal_dict.get("agent_id"),
                company_id=signal_dict.get("company_id"),
            )
            return {
                "id": signal_dict["id"],
                "symbol": signal_dict["symbol"],
                "status": "executed",
                "trade": trade_result,
            }
        except RuntimeError as e:
            # No connector for this symbol (e.g. forex without OANDA)
            logger.warning(
                f"Signal {signal_id} approved but cannot execute: {e}"
            )
            return {
                "id": signal_dict["id"],
                "symbol": signal_dict["symbol"],
                "status": "approved",
                "note": f"No trading platform available: {e}",
            }
        except Exception as e:
            logger.error(f"Trade execution after approval failed: {e}")
            return {
                "id": signal_dict["id"],
                "symbol": signal_dict["symbol"],
                "status": "approved",
                "error": str(e)[:200],
            }

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
        """Execute a trade on the exchange and record in DB.

        Applies hard risk caps:
        - SL capped at MAX_SL_PCT of entry price
        - TP capped at MAX_TP_PCT of entry price
        - Position value capped at MAX_POSITION_PCT of account equity
        """
        connector = self._find_connector(symbol)
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        o_type = {
            "market": OrderType.MARKET,
            "limit": OrderType.LIMIT,
            "stop": OrderType.STOP,
        }.get(order_type.lower(), OrderType.MARKET)

        # ── Apply SL/TP caps before execution ─────────────────────
        # Get a reference price for cap calculation
        ref_price = price
        if not ref_price:
            try:
                ticker = await connector.get_ticker(symbol)
                ref_price = ticker.last or ticker.bid or 0
            except Exception:
                ref_price = 0

        if ref_price and ref_price > 0:
            max_sl_distance = ref_price * (self.MAX_SL_PCT / 100)
            max_tp_distance = ref_price * (self.MAX_TP_PCT / 100)

            if stop_loss:
                sl_distance = abs(ref_price - stop_loss)
                if sl_distance > max_sl_distance:
                    old_sl = stop_loss
                    if side.lower() == "buy":
                        stop_loss = round(ref_price - max_sl_distance, 6)
                    else:
                        stop_loss = round(ref_price + max_sl_distance, 6)
                    logger.info(
                        f"SL capped for {symbol}: {old_sl} → {stop_loss} "
                        f"(max {self.MAX_SL_PCT}% from entry {ref_price})"
                    )

            if take_profit:
                tp_distance = abs(take_profit - ref_price)
                if tp_distance > max_tp_distance:
                    old_tp = take_profit
                    if side.lower() == "buy":
                        take_profit = round(ref_price + max_tp_distance, 6)
                    else:
                        take_profit = round(ref_price - max_tp_distance, 6)
                    logger.info(
                        f"TP capped for {symbol}: {old_tp} → {take_profit} "
                        f"(max {self.MAX_TP_PCT}% from entry {ref_price})"
                    )

        # ── Apply position size cap ───────────────────────────────
        if ref_price and ref_price > 0:
            try:
                account = await connector.get_account_info()
                equity = account.equity or account.balance or 0
                if equity > 0:
                    max_value = equity * (self.MAX_POSITION_PCT / 100)
                    position_value = size * ref_price
                    if position_value > max_value:
                        old_size = size
                        size = round(max_value / ref_price, 6)
                        logger.info(
                            f"Position size capped for {symbol}: {old_size} → {size} "
                            f"(value ${position_value:.0f} exceeded {self.MAX_POSITION_PCT}% "
                            f"of equity ${equity:.0f} = max ${max_value:.0f})"
                        )
            except Exception as e:
                logger.debug(f"Could not check position size cap: {e}")

        # ── Apply exchange quantity precision ─────────────────────
        clean_sym = symbol.replace("/", "").replace("_", "").upper()
        qty_decimals = self._QTY_PRECISION.get(clean_sym, 4)
        size = self._floor_qty(size, qty_decimals)
        if size <= 0:
            raise ValueError(f"Position size rounded to zero for {symbol}")

        order = await connector.place_order(
            symbol=symbol,
            side=order_side,
            size=size,
            order_type=o_type,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        entry_price = order.filled_price or order.price or ref_price or 0

        async with async_session() as session:
            trade = Trade(
                platform=connector.platform_name,
                symbol=symbol,
                side=side.lower(),
                size=size,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status="open" if order.status.value == "filled" else order.status.value,
                signal_id=signal_id,
                agent_id=agent_id,
                company_id=company_id,
                external_order_id=order.id,
                metadata_={"initial_stop_loss": stop_loss, "initial_take_profit": take_profit},
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

    async def close_trade(self, trade_id: str, platform: str | None = None) -> dict:
        """Close an open trade/position and update the DB record."""
        # Look up the trade in DB to get the symbol and external order info
        async with async_session() as session:
            trade = await session.get(Trade, trade_id)

        if trade:
            symbol = trade.symbol
            asset_id = trade.symbol.replace("USDT", "").replace("USD", "")
            platform = platform or trade.platform
        else:
            asset_id = trade_id
            symbol = trade_id

        if platform and platform in self._connectors:
            connector = self._connectors[platform]
        else:
            connector = self._find_connector(symbol)

        order = await connector.close_position(asset_id)

        # Update the trade record in DB
        if trade:
            async with async_session() as session:
                db_trade = await session.get(Trade, trade_id)
                if db_trade:
                    db_trade.status = "closed"
                    db_trade.exit_price = order.filled_price or 0
                    db_trade.closed_at = datetime.now(timezone.utc)
                    if db_trade.entry_price and order.filled_price:
                        if db_trade.side == "buy":
                            db_trade.pnl = (order.filled_price - db_trade.entry_price) * db_trade.size
                        else:
                            db_trade.pnl = (db_trade.entry_price - order.filled_price) * db_trade.size
                    await session.commit()

        result = {
            "trade_id": trade_id,
            "status": "closed",
            "exit_price": order.filled_price,
            "order_status": order.status.value,
            "platform": connector.platform_name,
        }
        await event_bus.broadcast("trade_closed", result)
        return result

    # ── Risk Caps ──────────────────────────────────────────────────
    # Hard limits applied at execution time — cannot be overridden by agents
    MAX_SL_PCT = 5.0        # Max stop-loss distance as % of entry price
    MAX_TP_PCT = 10.0       # Max take-profit distance as % of entry price
    MAX_POSITION_PCT = 10.0 # Max position value as % of account equity
    TRAILING_STOP_ACTIVATE_PCT = 1.5  # Activate trailing after this % profit
    TRAILING_STOP_DISTANCE_PCT = 1.0  # Trail SL this % behind price

    # Binance quantity step sizes (decimal places) — floor to avoid overshoot
    _QTY_PRECISION: dict[str, int] = {
        "BTCUSDT": 5, "ETHUSDT": 4, "BNBUSDT": 3,
        "SOLUSDT": 3, "XRPUSDT": 1, "DOGEUSDT": 0,
        "ADAUSDT": 1, "AVAXUSDT": 2, "DOTUSDT": 2,
        "LINKUSDT": 2, "MATICUSDT": 1, "LTCUSDT": 3,
    }

    @staticmethod
    def _floor_qty(size: float, decimals: int) -> float:
        """Floor quantity to the given decimal places (never round up)."""
        import math
        factor = 10 ** decimals
        return math.floor(size * factor) / factor

    async def check_open_trades(self) -> list[dict]:
        """Monitor open trades: trailing stop updates + SL/TP close checks.

        Returns a list of trades that were closed.
        """
        closed: list[dict] = []
        async with async_session() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == "open")
            )
            open_trades = result.scalars().all()

        for trade in open_trades:
            if not trade.stop_loss and not trade.take_profit:
                continue  # No SL/TP — nothing to monitor

            try:
                price_list = await self.get_prices([trade.symbol])
                if not price_list or "error" in price_list[0]:
                    continue
                price_data = price_list[0]
                current_price = price_data.get("last") or price_data.get("bid", 0)
                if not current_price:
                    continue

                # ── Trailing Stop Logic ───────────────────────────
                meta = trade.metadata_ or {}
                initial_sl = meta.get("initial_stop_loss", trade.stop_loss)
                highest = meta.get("highest_price", trade.entry_price or current_price)
                lowest = meta.get("lowest_price", trade.entry_price or current_price)
                meta_changed = False

                # Track price extremes
                if current_price > highest:
                    highest = current_price
                    meta["highest_price"] = highest
                    meta_changed = True
                if current_price < lowest:
                    lowest = current_price
                    meta["lowest_price"] = lowest
                    meta_changed = True

                # Move SL when price moves in our favour
                if trade.entry_price and trade.stop_loss:
                    if trade.side == "buy":
                        profit_pct = (highest - trade.entry_price) / trade.entry_price * 100
                        if profit_pct >= self.TRAILING_STOP_ACTIVATE_PCT:
                            trail_sl = round(highest * (1 - self.TRAILING_STOP_DISTANCE_PCT / 100), 6)
                            # Only move SL up, never down
                            if trail_sl > trade.stop_loss:
                                old_sl = trade.stop_loss
                                async with async_session() as sess:
                                    db_t = await sess.get(Trade, trade.id)
                                    if db_t:
                                        db_t.stop_loss = trail_sl
                                        if not db_t.metadata_:
                                            db_t.metadata_ = {}
                                        db_t.metadata_["initial_stop_loss"] = initial_sl
                                        db_t.metadata_["highest_price"] = highest
                                        db_t.metadata_["lowest_price"] = lowest
                                        db_t.metadata_["trailing_active"] = True
                                        await sess.commit()
                                trade.stop_loss = trail_sl
                                logger.info(
                                    f"Trailing SL moved UP for {trade.symbol}: "
                                    f"{old_sl:.2f} → {trail_sl:.2f} (highest={highest:.2f}, +{profit_pct:.1f}%)"
                                )
                                meta_changed = False  # already saved
                    else:  # sell
                        profit_pct = (trade.entry_price - lowest) / trade.entry_price * 100
                        if profit_pct >= self.TRAILING_STOP_ACTIVATE_PCT:
                            trail_sl = round(lowest * (1 + self.TRAILING_STOP_DISTANCE_PCT / 100), 6)
                            if trail_sl < trade.stop_loss:
                                old_sl = trade.stop_loss
                                async with async_session() as sess:
                                    db_t = await sess.get(Trade, trade.id)
                                    if db_t:
                                        db_t.stop_loss = trail_sl
                                        if not db_t.metadata_:
                                            db_t.metadata_ = {}
                                        db_t.metadata_["initial_stop_loss"] = initial_sl
                                        db_t.metadata_["highest_price"] = highest
                                        db_t.metadata_["lowest_price"] = lowest
                                        db_t.metadata_["trailing_active"] = True
                                        await sess.commit()
                                trade.stop_loss = trail_sl
                                logger.info(
                                    f"Trailing SL moved DOWN for {trade.symbol}: "
                                    f"{old_sl:.2f} → {trail_sl:.2f} (lowest={lowest:.2f}, +{profit_pct:.1f}%)"
                                )
                                meta_changed = False

                # Persist extreme price tracking if changed (without SL move)
                if meta_changed:
                    async with async_session() as sess:
                        db_t = await sess.get(Trade, trade.id)
                        if db_t:
                            db_t.metadata_ = {**(db_t.metadata_ or {}), **meta}
                            await sess.commit()

                # ── SL/TP Close Check ─────────────────────────────
                should_close = False
                close_reason = ""

                if trade.side == "buy":
                    if trade.stop_loss and current_price <= trade.stop_loss:
                        is_trailing = meta.get("trailing_active", False)
                        should_close = True
                        close_reason = (
                            "Trailing SL hit" if is_trailing else "SL hit"
                        ) + f": price {current_price:.2f} <= SL {trade.stop_loss:.2f}"
                    elif trade.take_profit and current_price >= trade.take_profit:
                        should_close = True
                        close_reason = f"TP hit: price {current_price:.2f} >= TP {trade.take_profit:.2f}"
                else:  # sell
                    if trade.stop_loss and current_price >= trade.stop_loss:
                        is_trailing = meta.get("trailing_active", False)
                        should_close = True
                        close_reason = (
                            "Trailing SL hit" if is_trailing else "SL hit"
                        ) + f": price {current_price:.2f} >= SL {trade.stop_loss:.2f}"
                    elif trade.take_profit and current_price <= trade.take_profit:
                        should_close = True
                        close_reason = f"TP hit: price {current_price:.2f} <= TP {trade.take_profit:.2f}"

                if should_close:
                    logger.info(f"Auto-closing trade {trade.id} ({trade.symbol}): {close_reason}")
                    try:
                        result = await self.close_trade(trade.id, platform=trade.platform)
                        result["reason"] = close_reason
                        # Record close reason in metadata
                        async with async_session() as sess:
                            db_t = await sess.get(Trade, trade.id)
                            if db_t:
                                if not db_t.metadata_:
                                    db_t.metadata_ = {}
                                db_t.metadata_["close_reason"] = close_reason
                                db_t.metadata_["close_price"] = current_price
                                await sess.commit()
                        closed.append(result)
                    except Exception as e:
                        logger.error(f"Failed to close trade {trade.id}: {e}")
                        # Update DB anyway if we can't close on exchange
                        async with async_session() as session:
                            db_trade = await session.get(Trade, trade.id)
                            if db_trade:
                                db_trade.status = "closed"
                                db_trade.exit_price = current_price
                                db_trade.closed_at = datetime.now(timezone.utc)
                                if db_trade.entry_price:
                                    if db_trade.side == "buy":
                                        db_trade.pnl = (current_price - db_trade.entry_price) * db_trade.size
                                    else:
                                        db_trade.pnl = (db_trade.entry_price - current_price) * db_trade.size
                                await session.commit()
                        closed.append({
                            "trade_id": trade.id,
                            "status": "closed",
                            "exit_price": current_price,
                            "reason": close_reason,
                            "note": f"DB updated, exchange close failed: {e}",
                        })
            except Exception as e:
                logger.debug(f"Error checking trade {trade.id}: {e}")

        return closed

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

    async def expire_stale_signals(self) -> int:
        """Expire pending signals older than SIGNAL_TTL_MINUTES.

        Returns count of expired signals.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.SIGNAL_TTL_MINUTES)
        count = 0
        async with async_session() as session:
            result = await session.execute(
                select(TradeSignal).where(
                    and_(
                        TradeSignal.status == "pending",
                        TradeSignal.created_at < cutoff,
                    )
                )
            )
            stale = result.scalars().all()
            for sig in stale:
                sig.status = "expired"
                sig.decided_at = datetime.now(timezone.utc)
                count += 1
            if count:
                await session.commit()
                logger.info(f"Expired {count} stale signal(s) (older than {self.SIGNAL_TTL_MINUTES}min)")
        return count

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
