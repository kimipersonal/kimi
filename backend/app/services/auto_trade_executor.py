"""Auto-Trade Executor — automatically execute low-risk trade signals.

Monitors incoming trade signals and auto-executes those that meet
configurable safety criteria (high confidence, proper SL/TP, within
risk limits). Signals that don't qualify are left for manual review.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class AutoTradeMode(str, Enum):
    """Operating modes for auto-execution."""
    DISABLED = "disabled"        # No auto-execution
    CONSERVATIVE = "conservative"  # Only very high confidence
    MODERATE = "moderate"        # Standard thresholds
    AGGRESSIVE = "aggressive"    # Lower thresholds


@dataclass
class AutoTradeConfig:
    """Configuration for auto-trade execution."""
    mode: AutoTradeMode = AutoTradeMode.CONSERVATIVE
    min_confidence: float = 0.85          # Minimum signal confidence (0-1)
    max_risk_per_trade_pct: float = 1.0   # Max risk per trade as % of equity
    max_daily_trades: int = 10            # Max auto-trades per day
    max_daily_loss_pct: float = 3.0       # Stop auto-trading if daily loss exceeds %
    require_stop_loss: bool = True        # Require SL on every auto-trade
    require_take_profit: bool = True      # Require TP on every auto-trade
    min_risk_reward_ratio: float = 1.5    # Minimum TP distance / SL distance
    allowed_symbols: list[str] = field(default_factory=list)  # Empty = all allowed
    blocked_symbols: list[str] = field(default_factory=list)
    max_open_positions: int = 5           # Max total open positions
    max_position_per_symbol: int = 1      # Max positions per symbol

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "min_confidence": self.min_confidence,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "max_daily_trades": self.max_daily_trades,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "require_stop_loss": self.require_stop_loss,
            "require_take_profit": self.require_take_profit,
            "min_risk_reward_ratio": self.min_risk_reward_ratio,
            "allowed_symbols": self.allowed_symbols,
            "blocked_symbols": self.blocked_symbols,
            "max_open_positions": self.max_open_positions,
            "max_position_per_symbol": self.max_position_per_symbol,
        }


# Preset configs per mode
_MODE_PRESETS: dict[AutoTradeMode, dict] = {
    AutoTradeMode.CONSERVATIVE: {
        "min_confidence": 0.90,
        "max_risk_per_trade_pct": 0.5,
        "max_daily_trades": 5,
        "max_daily_loss_pct": 2.0,
        "min_risk_reward_ratio": 2.0,
    },
    AutoTradeMode.MODERATE: {
        "min_confidence": 0.80,
        "max_risk_per_trade_pct": 1.0,
        "max_daily_trades": 10,
        "max_daily_loss_pct": 3.0,
        "min_risk_reward_ratio": 1.5,
    },
    AutoTradeMode.AGGRESSIVE: {
        "min_confidence": 0.70,
        "max_risk_per_trade_pct": 2.0,
        "max_daily_trades": 20,
        "max_daily_loss_pct": 5.0,
        "min_risk_reward_ratio": 1.0,
    },
}


@dataclass
class _DailyStats:
    """Track daily auto-trade statistics."""
    date: str = ""
    trades_executed: int = 0
    trades_rejected: int = 0
    total_pnl: float = 0.0
    rejection_reasons: dict = field(default_factory=dict)

    def reset(self):
        self.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.trades_executed = 0
        self.trades_rejected = 0
        self.total_pnl = 0.0
        self.rejection_reasons = {}

    def record_rejection(self, reason: str):
        self.trades_rejected += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1


class AutoTradeExecutor:
    """Evaluates trade signals and auto-executes qualifying ones."""

    _REDIS_KEY = "auto_trade_executor:state"

    def __init__(self):
        self.config = AutoTradeConfig()
        self._daily = _DailyStats()
        self._execution_log: list[dict] = []  # Recent execution log (last 50)
        self._running = False

    # ── Persistence ───────────────────────────────────────────────

    async def save_to_redis(self):
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            data = {
                "config": self.config.to_dict(),
                "daily": {
                    "date": self._daily.date,
                    "trades_executed": self._daily.trades_executed,
                    "trades_rejected": self._daily.trades_rejected,
                    "total_pnl": self._daily.total_pnl,
                    "rejection_reasons": self._daily.rejection_reasons,
                },
            }
            await r.set(self._REDIS_KEY, json.dumps(data), ex=86400 * 7)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not save auto-trade state: {e}")

    async def load_from_redis(self):
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(self._REDIS_KEY)
            await r.aclose()
            if raw:
                data = json.loads(raw)
                cfg = data.get("config", {})
                self.config.mode = AutoTradeMode(cfg.get("mode", "conservative"))
                self.config.min_confidence = cfg.get("min_confidence", 0.85)
                self.config.max_risk_per_trade_pct = cfg.get("max_risk_per_trade_pct", 1.0)
                self.config.max_daily_trades = cfg.get("max_daily_trades", 10)
                self.config.max_daily_loss_pct = cfg.get("max_daily_loss_pct", 3.0)
                self.config.require_stop_loss = cfg.get("require_stop_loss", True)
                self.config.require_take_profit = cfg.get("require_take_profit", True)
                self.config.min_risk_reward_ratio = cfg.get("min_risk_reward_ratio", 1.5)
                self.config.allowed_symbols = cfg.get("allowed_symbols", [])
                self.config.blocked_symbols = cfg.get("blocked_symbols", [])
                self.config.max_open_positions = cfg.get("max_open_positions", 5)
                self.config.max_position_per_symbol = cfg.get("max_position_per_symbol", 1)

                daily = data.get("daily", {})
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if daily.get("date") == today:
                    self._daily.date = daily["date"]
                    self._daily.trades_executed = daily.get("trades_executed", 0)
                    self._daily.trades_rejected = daily.get("trades_rejected", 0)
                    self._daily.total_pnl = daily.get("total_pnl", 0.0)
                    self._daily.rejection_reasons = daily.get("rejection_reasons", {})
                else:
                    self._daily.reset()
                logger.info(f"Auto-trade config loaded: mode={self.config.mode.value}")
        except Exception as e:
            logger.debug(f"Could not load auto-trade state: {e}")

    # ── Configuration ─────────────────────────────────────────────

    async def set_mode(self, mode: str) -> dict:
        """Set the auto-trade mode and apply preset configuration."""
        try:
            new_mode = AutoTradeMode(mode.lower())
        except ValueError:
            return {"error": f"Invalid mode. Valid: {[m.value for m in AutoTradeMode]}"}

        self.config.mode = new_mode
        if new_mode in _MODE_PRESETS:
            for k, v in _MODE_PRESETS[new_mode].items():
                setattr(self.config, k, v)

        await self.save_to_redis()
        return {
            "mode": new_mode.value,
            "config": self.config.to_dict(),
            "message": f"Auto-trade mode set to {new_mode.value}",
        }

    async def update_config(self, **kwargs) -> dict:
        """Update individual config parameters."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        await self.save_to_redis()
        return {"config": self.config.to_dict()}

    # ── Signal Evaluation ─────────────────────────────────────────

    def _ensure_daily_reset(self):
        """Reset daily stats if it's a new day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily.date != today:
            self._daily.reset()

    async def evaluate_signal(self, signal: dict) -> dict:
        """Evaluate a trade signal against auto-trade criteria.

        Returns:
            dict with "approved" (bool), "reasons" (list), and optionally
            "position_size" if approved.
        """
        self._ensure_daily_reset()
        reasons: list[str] = []
        warnings: list[str] = []

        # Check if auto-trading is enabled
        if self.config.mode == AutoTradeMode.DISABLED:
            return {"approved": False, "reasons": ["Auto-trading is disabled"]}

        symbol = signal.get("symbol", "").upper()
        direction = signal.get("direction", "").lower()
        confidence = signal.get("confidence", 0)
        entry_price = signal.get("entry_price")
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        # 1. Confidence check
        if confidence < self.config.min_confidence:
            reasons.append(
                f"Confidence {confidence:.0%} below threshold {self.config.min_confidence:.0%}"
            )

        # 2. Stop-loss check
        if self.config.require_stop_loss and not stop_loss:
            reasons.append("Stop-loss required but not provided")

        # 3. Take-profit check
        if self.config.require_take_profit and not take_profit:
            reasons.append("Take-profit required but not provided")

        # 4. Risk:reward ratio
        if entry_price and stop_loss and take_profit:
            sl_distance = abs(entry_price - stop_loss)
            tp_distance = abs(take_profit - entry_price)
            if sl_distance > 0:
                rr_ratio = tp_distance / sl_distance
                if rr_ratio < self.config.min_risk_reward_ratio:
                    reasons.append(
                        f"Risk:reward {rr_ratio:.2f} below minimum {self.config.min_risk_reward_ratio}"
                    )
            else:
                reasons.append("Stop-loss distance is zero")

        # 5. Symbol allowlist / blocklist
        if self.config.allowed_symbols and symbol not in [
            s.upper() for s in self.config.allowed_symbols
        ]:
            reasons.append(f"Symbol {symbol} not in allowed list")
        if symbol in [s.upper() for s in self.config.blocked_symbols]:
            reasons.append(f"Symbol {symbol} is blocked")

        # 6. Daily trade limit
        if self._daily.trades_executed >= self.config.max_daily_trades:
            reasons.append(
                f"Daily trade limit reached ({self.config.max_daily_trades})"
            )

        # 7. Daily loss limit
        if self._daily.total_pnl < 0:
            loss_pct = abs(self._daily.total_pnl)  # Already tracked as % or abs
            if loss_pct > self.config.max_daily_loss_pct:
                reasons.append(
                    f"Daily loss limit exceeded ({loss_pct:.1f}% > {self.config.max_daily_loss_pct}%)"
                )

        # 8. Open positions check (requires trading service)
        try:
            from app.services.trading.trading_service import trading_service
            if trading_service.is_connected:
                positions = await trading_service.get_positions()
                if len(positions) >= self.config.max_open_positions:
                    reasons.append(
                        f"Max open positions reached ({len(positions)}/{self.config.max_open_positions})"
                    )
                # Per-symbol check
                symbol_positions = [
                    p for p in positions if p.get("symbol", "").upper() == symbol
                ]
                if len(symbol_positions) >= self.config.max_position_per_symbol:
                    reasons.append(
                        f"Max positions for {symbol} reached ({len(symbol_positions)}/{self.config.max_position_per_symbol})"
                    )
        except Exception as e:
            warnings.append(f"Could not check positions: {str(e)[:100]}")

        approved = len(reasons) == 0
        result = {
            "approved": approved,
            "signal_id": signal.get("id"),
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "reasons": reasons if not approved else ["All criteria met"],
            "warnings": warnings,
            "config_mode": self.config.mode.value,
        }

        if approved:
            # Calculate position size using risk parameters
            try:
                from app.services.position_calculator import position_calculator
                sizing = await position_calculator.calculate_size(
                    symbol=symbol,
                    entry_price=entry_price or 0,
                    stop_loss=stop_loss or 0,
                    risk_pct=self.config.max_risk_per_trade_pct,
                )
                result["position_size"] = sizing
            except Exception as e:
                result["position_size"] = {"error": str(e)[:200]}
        else:
            for reason in reasons:
                self._daily.record_rejection(reason.split(" ")[0])

        return result

    # ── Execution ─────────────────────────────────────────────────

    async def try_auto_execute(self, signal: dict) -> dict:
        """Evaluate and optionally execute a trade signal automatically.

        This is the main entry point — called when a new signal is created.
        Returns the evaluation + execution result.
        """
        evaluation = await self.evaluate_signal(signal)

        if not evaluation["approved"]:
            logger.info(
                f"Auto-trade REJECTED signal {signal.get('id', '?')}: "
                f"{', '.join(evaluation['reasons'])}"
            )
            self._log_execution(signal, evaluation, executed=False)
            await self.save_to_redis()
            return evaluation

        # Execute the trade
        try:
            from app.services.trading.trading_service import trading_service
            size_info = evaluation.get("position_size", {})
            size = size_info.get("recommended_size", 0.01)

            trade_result = await trading_service.execute_trade(
                symbol=signal["symbol"],
                side=signal["direction"],
                size=size,
                order_type="market",
                stop_loss=signal.get("stop_loss"),
                take_profit=signal.get("take_profit"),
                signal_id=signal.get("id"),
                agent_id=signal.get("agent_id"),
                company_id=signal.get("company_id"),
            )

            self._daily.trades_executed += 1
            evaluation["executed"] = True
            evaluation["trade_result"] = trade_result
            logger.info(
                f"Auto-trade EXECUTED signal {signal.get('id', '?')}: "
                f"{signal['symbol']} {signal['direction']} size={size}"
            )

            # Broadcast event for monitoring
            from app.services.event_bus import event_bus
            await event_bus.broadcast("auto_trade_executed", {
                "signal_id": signal.get("id"),
                "symbol": signal["symbol"],
                "direction": signal["direction"],
                "size": size,
                "confidence": signal.get("confidence", 0),
            })

        except Exception as e:
            evaluation["executed"] = False
            evaluation["execution_error"] = str(e)[:300]
            logger.error(f"Auto-trade execution FAILED: {e}")

        self._log_execution(signal, evaluation, executed=evaluation.get("executed", False))
        await self.save_to_redis()
        return evaluation

    def _log_execution(self, signal: dict, evaluation: dict, executed: bool):
        """Add to recent execution log."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal_id": signal.get("id"),
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "confidence": signal.get("confidence"),
            "approved": evaluation["approved"],
            "executed": executed,
            "reasons": evaluation.get("reasons", []),
        }
        self._execution_log.append(entry)
        if len(self._execution_log) > 50:
            self._execution_log = self._execution_log[-50:]

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get auto-trade executor status and daily statistics."""
        self._ensure_daily_reset()
        return {
            "config": self.config.to_dict(),
            "daily_stats": {
                "date": self._daily.date,
                "trades_executed": self._daily.trades_executed,
                "trades_rejected": self._daily.trades_rejected,
                "total_pnl": round(self._daily.total_pnl, 2),
                "remaining_trades": max(
                    0, self.config.max_daily_trades - self._daily.trades_executed
                ),
                "rejection_reasons": self._daily.rejection_reasons,
            },
            "recent_log": self._execution_log[-10:],
        }

    def reset_daily(self):
        """Reset daily counters (called at midnight UTC)."""
        self._daily.reset()


# Global singleton
auto_trade_executor = AutoTradeExecutor()
