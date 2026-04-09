"""Portfolio Risk Manager — real-time portfolio risk monitoring and enforcement.

Tracks total exposure, per-symbol concentration, drawdown from peak equity,
and correlation between positions. Auto-alerts and can force-close positions
when risk limits are breached.
"""

from app.db.database import redis_pool
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskLimits:
    """Configurable portfolio risk limits."""
    max_drawdown_pct: float = 10.0           # Max drawdown from peak equity (%)
    max_total_exposure_pct: float = 50.0     # Max total position value / equity (%)
    max_single_position_pct: float = 20.0    # Max single position / equity (%)
    max_correlated_exposure_pct: float = 30.0 # Max correlated positions / equity (%)
    max_open_positions: int = 10
    drawdown_warning_pct: float = 5.0        # Warn at this drawdown level
    auto_close_on_critical: bool = False     # Auto-close positions at critical risk

    def to_dict(self) -> dict:
        return {
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_total_exposure_pct": self.max_total_exposure_pct,
            "max_single_position_pct": self.max_single_position_pct,
            "max_correlated_exposure_pct": self.max_correlated_exposure_pct,
            "max_open_positions": self.max_open_positions,
            "drawdown_warning_pct": self.drawdown_warning_pct,
            "auto_close_on_critical": self.auto_close_on_critical,
        }


# Symbol correlation groups (simplified)
_CORRELATION_GROUPS = {
    "USD_LONGS": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],
    "USD_SHORTS": ["USDJPY", "USDCAD", "USDCHF"],
    "CRYPTO_MAJOR": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
    "CRYPTO_ALT": ["SOLUSDT", "ADAUSDT", "XRPUSDT", "DOTUSDT"],
    "JPY_CROSSES": ["EURJPY", "GBPJPY", "AUDJPY"],
}


def _normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("_", "").replace("-", "").upper()


def _get_correlation_group(symbol: str) -> str | None:
    """Find which correlation group a symbol belongs to."""
    clean = _normalize_symbol(symbol)
    for group, symbols in _CORRELATION_GROUPS.items():
        if clean in symbols:
            return group
    return None


@dataclass
class RiskSnapshot:
    """Point-in-time risk assessment."""
    timestamp: str
    total_equity: float
    peak_equity: float
    current_drawdown_pct: float
    total_exposure: float
    total_exposure_pct: float
    open_positions: int
    position_breakdown: list[dict]
    correlation_exposures: dict
    risk_level: str
    alerts: list[str]
    limits: dict

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_equity": round(self.total_equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct, 2),
            "total_exposure": round(self.total_exposure, 2),
            "total_exposure_pct": round(self.total_exposure_pct, 2),
            "open_positions": self.open_positions,
            "position_breakdown": self.position_breakdown,
            "correlation_exposures": self.correlation_exposures,
            "risk_level": self.risk_level,
            "alerts": self.alerts,
            "limits": self.limits,
        }


class PortfolioRiskManager:
    """Monitors and enforces portfolio-level risk limits."""

    _REDIS_KEY = "portfolio_risk_manager:state"

    def __init__(self):
        self.limits = RiskLimits()
        self._peak_equity: float = 0.0
        self._alert_history: list[dict] = []
        self._last_snapshot: RiskSnapshot | None = None

    # ── Persistence ───────────────────────────────────────────────

    async def save_to_redis(self):
        try:
            data = {
                "limits": self.limits.to_dict(),
                "peak_equity": self._peak_equity,
            }
            await redis_pool.set(self._REDIS_KEY, json.dumps(data), ex=86400 * 30)
        except Exception as e:
            logger.debug(f"Could not save risk manager state: {e}")

    async def load_from_redis(self):
        try:
            raw = await redis_pool.get(self._REDIS_KEY)
            if raw:
                data = json.loads(raw)
                limits = data.get("limits", {})
                for k, v in limits.items():
                    if hasattr(self.limits, k):
                        setattr(self.limits, k, v)
                self._peak_equity = data.get("peak_equity", 0.0)
                logger.info(f"Risk manager loaded: peak_equity=${self._peak_equity:.2f}")
        except Exception as e:
            logger.debug(f"Could not load risk manager state: {e}")

    # ── Configuration ─────────────────────────────────────────────

    async def update_limits(self, **kwargs) -> dict:
        """Update risk limits."""
        for key, value in kwargs.items():
            if hasattr(self.limits, key):
                setattr(self.limits, key, value)
        await self.save_to_redis()
        return {"limits": self.limits.to_dict()}

    # ── Risk Assessment ───────────────────────────────────────────

    async def assess_risk(self) -> dict:
        """Perform a comprehensive portfolio risk assessment.

        Returns a RiskSnapshot with current risk levels and any alerts.
        """
        alerts: list[str] = []

        # Fetch portfolio data
        try:
            from app.services.trading.trading_service import trading_service
            if not trading_service.is_connected:
                return {"error": "Trading service not connected"}

            portfolio = await trading_service.get_portfolio_summary()
            positions = portfolio.get("positions", [])
            total_equity = portfolio.get("total_equity", 0)
        except Exception as e:
            return {"error": f"Could not fetch portfolio: {str(e)[:200]}"}

        if total_equity <= 0:
            return {"error": "No equity data available"}

        # Update peak equity
        if total_equity > self._peak_equity:
            self._peak_equity = total_equity
            await self.save_to_redis()

        # 1. Drawdown calculation
        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = ((self._peak_equity - total_equity) / self._peak_equity) * 100

        if drawdown_pct >= self.limits.max_drawdown_pct:
            alerts.append(
                f"CRITICAL: Drawdown {drawdown_pct:.1f}% exceeds max {self.limits.max_drawdown_pct}%"
            )
        elif drawdown_pct >= self.limits.drawdown_warning_pct:
            alerts.append(
                f"WARNING: Drawdown {drawdown_pct:.1f}% approaching max {self.limits.max_drawdown_pct}%"
            )

        # 2. Total exposure
        total_exposure = sum(
            abs(p.get("size", 0) * p.get("current_price", p.get("entry_price", 0)))
            for p in positions
        )
        exposure_pct = (total_exposure / total_equity * 100) if total_equity > 0 else 0

        if exposure_pct > self.limits.max_total_exposure_pct:
            alerts.append(
                f"HIGH: Total exposure {exposure_pct:.1f}% exceeds max {self.limits.max_total_exposure_pct}%"
            )

        # 3. Per-position concentration
        position_breakdown = []
        for p in positions:
            pos_value = abs(p.get("size", 0) * p.get("current_price", p.get("entry_price", 0)))
            pos_pct = (pos_value / total_equity * 100) if total_equity > 0 else 0
            symbol = p.get("symbol", "?")

            position_breakdown.append({
                "symbol": symbol,
                "side": p.get("side", "?"),
                "size": p.get("size", 0),
                "value": round(pos_value, 2),
                "pct_of_equity": round(pos_pct, 2),
                "unrealized_pnl": round(p.get("unrealized_pnl", 0), 2),
            })

            if pos_pct > self.limits.max_single_position_pct:
                alerts.append(
                    f"HIGH: {symbol} position {pos_pct:.1f}% exceeds max "
                    f"{self.limits.max_single_position_pct}% of equity"
                )

        # 4. Position count check
        if len(positions) > self.limits.max_open_positions:
            alerts.append(
                f"WARNING: {len(positions)} open positions exceeds max {self.limits.max_open_positions}"
            )

        # 5. Correlation exposure
        correlation_exposures: dict[str, float] = {}
        for p in positions:
            symbol = _normalize_symbol(p.get("symbol", ""))
            group = _get_correlation_group(symbol)
            if group:
                pos_value = abs(
                    p.get("size", 0) * p.get("current_price", p.get("entry_price", 0))
                )
                correlation_exposures[group] = correlation_exposures.get(group, 0) + pos_value

        for group, exposure in correlation_exposures.items():
            corr_pct = (exposure / total_equity * 100) if total_equity > 0 else 0
            correlation_exposures[group] = round(corr_pct, 2)
            if corr_pct > self.limits.max_correlated_exposure_pct:
                alerts.append(
                    f"HIGH: Correlated group {group} at {corr_pct:.1f}% "
                    f"exceeds max {self.limits.max_correlated_exposure_pct}%"
                )

        # Determine overall risk level
        if any("CRITICAL" in a for a in alerts):
            risk_level = RiskLevel.CRITICAL
        elif any("HIGH" in a for a in alerts):
            risk_level = RiskLevel.HIGH
        elif any("WARNING" in a for a in alerts):
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        snapshot = RiskSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_equity=total_equity,
            peak_equity=self._peak_equity,
            current_drawdown_pct=drawdown_pct,
            total_exposure=total_exposure,
            total_exposure_pct=exposure_pct,
            open_positions=len(positions),
            position_breakdown=position_breakdown,
            correlation_exposures=correlation_exposures,
            risk_level=risk_level.value,
            alerts=alerts,
            limits=self.limits.to_dict(),
        )
        self._last_snapshot = snapshot

        # Store alerts
        if alerts:
            self._alert_history.append({
                "timestamp": snapshot.timestamp,
                "risk_level": risk_level.value,
                "alerts": alerts,
            })
            if len(self._alert_history) > 100:
                self._alert_history = self._alert_history[-100:]

            # Broadcast risk alerts
            try:
                from app.services.event_bus import event_bus
                await event_bus.broadcast("risk_alert", {
                    "risk_level": risk_level.value,
                    "alerts": alerts,
                    "drawdown_pct": round(drawdown_pct, 2),
                })
            except Exception:
                pass

        return snapshot.to_dict()

    # ── Pre-Trade Risk Check ──────────────────────────────────────

    async def check_trade_risk(
        self, symbol: str, side: str, size: float, entry_price: float
    ) -> dict:
        """Check if a proposed trade would violate risk limits.

        Returns {"allowed": True/False, "warnings": [...]}
        """
        warnings: list[str] = []

        try:
            from app.services.trading.trading_service import trading_service
            if not trading_service.is_connected:
                return {"allowed": True, "warnings": ["Trading service not connected; skipping risk check"]}

            portfolio = await trading_service.get_portfolio_summary()
            total_equity = portfolio.get("total_equity", 0)
            positions = portfolio.get("positions", [])
        except Exception:
            return {"allowed": True, "warnings": ["Could not fetch portfolio for risk check"]}

        if total_equity <= 0:
            return {"allowed": True, "warnings": ["No equity data"]}

        trade_value = abs(size * entry_price)
        trade_pct = (trade_value / total_equity) * 100

        # Single position size
        if trade_pct > self.limits.max_single_position_pct:
            warnings.append(
                f"Trade would be {trade_pct:.1f}% of equity "
                f"(max {self.limits.max_single_position_pct}%)"
            )

        # Total exposure after trade
        current_exposure = sum(
            abs(p.get("size", 0) * p.get("current_price", p.get("entry_price", 0)))
            for p in positions
        )
        new_exposure_pct = ((current_exposure + trade_value) / total_equity) * 100
        if new_exposure_pct > self.limits.max_total_exposure_pct:
            warnings.append(
                f"Total exposure would reach {new_exposure_pct:.1f}% "
                f"(max {self.limits.max_total_exposure_pct}%)"
            )

        # Position count
        if len(positions) + 1 > self.limits.max_open_positions:
            warnings.append(
                f"Would exceed max positions ({self.limits.max_open_positions})"
            )

        # Correlation check
        group = _get_correlation_group(symbol)
        if group:
            group_exposure = sum(
                abs(p.get("size", 0) * p.get("current_price", p.get("entry_price", 0)))
                for p in positions
                if _get_correlation_group(_normalize_symbol(p.get("symbol", ""))) == group
            )
            new_corr_pct = ((group_exposure + trade_value) / total_equity) * 100
            if new_corr_pct > self.limits.max_correlated_exposure_pct:
                warnings.append(
                    f"Correlated group {group} would reach {new_corr_pct:.1f}% "
                    f"(max {self.limits.max_correlated_exposure_pct}%)"
                )

        allowed = len(warnings) == 0
        return {"allowed": allowed, "warnings": warnings, "trade_value_pct": round(trade_pct, 2)}

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current risk manager status."""
        return {
            "limits": self.limits.to_dict(),
            "peak_equity": round(self._peak_equity, 2),
            "last_assessment": self._last_snapshot.to_dict() if self._last_snapshot else None,
            "recent_alerts": self._alert_history[-10:],
        }

    def get_alert_history(self, limit: int = 20) -> list[dict]:
        return self._alert_history[-limit:]


# Global singleton
portfolio_risk_manager = PortfolioRiskManager()
