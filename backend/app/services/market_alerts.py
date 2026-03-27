"""Market Event Alerts — configurable price and event monitoring.

Allows the CEO and agents to set price alerts (above/below thresholds),
percentage change alerts, and periodic market scans. Triggers notifications
via the event bus and Telegram when conditions are met.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PCT_CHANGE = "pct_change"     # % change within period
    VOLATILITY = "volatility"     # ATR-based spike


class AlertStatus(str, Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class MarketAlert:
    """A single market alert configuration."""
    id: str
    symbol: str
    alert_type: AlertType
    threshold: float          # Price level or % change
    created_by: str           # agent_id who created it
    created_at: str
    status: AlertStatus = AlertStatus.ACTIVE
    message: str = ""         # Custom message when triggered
    repeat: bool = False      # Re-arm after triggering
    triggered_at: str | None = None
    triggered_price: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "alert_type": self.alert_type.value,
            "threshold": self.threshold,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "status": self.status.value,
            "message": self.message,
            "repeat": self.repeat,
            "triggered_at": self.triggered_at,
            "triggered_price": self.triggered_price,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MarketAlert":
        return cls(
            id=data["id"],
            symbol=data["symbol"],
            alert_type=AlertType(data["alert_type"]),
            threshold=data["threshold"],
            created_by=data["created_by"],
            created_at=data["created_at"],
            status=AlertStatus(data.get("status", "active")),
            message=data.get("message", ""),
            repeat=data.get("repeat", False),
            triggered_at=data.get("triggered_at"),
            triggered_price=data.get("triggered_price"),
        )


class MarketAlertService:
    """Manages market alerts and checks them against live prices."""

    _REDIS_KEY = "market_alerts:state"

    def __init__(self):
        self._alerts: dict[str, MarketAlert] = {}
        self._triggered_history: list[dict] = []
        self._running = False
        self._check_task: asyncio.Task | None = None
        # Cache last known prices to detect % changes
        self._last_prices: dict[str, float] = {}

    # ── Persistence ───────────────────────────────────────────────

    async def save_to_redis(self):
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            data = {
                "alerts": {k: v.to_dict() for k, v in self._alerts.items()},
                "last_prices": self._last_prices,
            }
            await r.set(self._REDIS_KEY, json.dumps(data), ex=86400 * 30)
            await r.aclose()
        except Exception as e:
            logger.debug(f"Could not save market alerts: {e}")

    async def load_from_redis(self):
        try:
            import redis.asyncio as aioredis
            from app.config import get_settings
            r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
            raw = await r.get(self._REDIS_KEY)
            await r.aclose()
            if raw:
                data = json.loads(raw)
                for aid, adict in data.get("alerts", {}).items():
                    self._alerts[aid] = MarketAlert.from_dict(adict)
                self._last_prices = data.get("last_prices", {})
                active = sum(
                    1 for a in self._alerts.values()
                    if a.status == AlertStatus.ACTIVE
                )
                logger.info(f"Market alerts loaded: {active} active")
        except Exception as e:
            logger.debug(f"Could not load market alerts: {e}")

    # ── Alert Management ──────────────────────────────────────────

    async def create_alert(
        self,
        symbol: str,
        alert_type: str,
        threshold: float,
        created_by: str = "ceo",
        message: str = "",
        repeat: bool = False,
    ) -> dict:
        """Create a new market alert."""
        try:
            atype = AlertType(alert_type.lower())
        except ValueError:
            return {"error": f"Invalid alert type. Valid: {[t.value for t in AlertType]}"}

        alert_id = str(uuid4())[:8]
        alert = MarketAlert(
            id=alert_id,
            symbol=symbol.upper(),
            alert_type=atype,
            threshold=threshold,
            created_by=created_by,
            created_at=datetime.now(timezone.utc).isoformat(),
            message=message,
            repeat=repeat,
        )

        self._alerts[alert_id] = alert
        await self.save_to_redis()

        logger.info(f"Alert created: {alert_id} {symbol} {alert_type} @ {threshold}")
        return {
            "id": alert_id,
            "alert": alert.to_dict(),
            "message": f"Alert set: {symbol} {alert_type} at {threshold}",
        }

    async def cancel_alert(self, alert_id: str) -> dict:
        """Cancel an active alert."""
        alert = self._alerts.get(alert_id)
        if not alert:
            return {"error": f"Alert {alert_id} not found"}
        if alert.status != AlertStatus.ACTIVE:
            return {"error": f"Alert {alert_id} is already {alert.status.value}"}

        alert.status = AlertStatus.CANCELLED
        await self.save_to_redis()
        return {"id": alert_id, "status": "cancelled"}

    def list_alerts(self, status_filter: str | None = None) -> list[dict]:
        """List all alerts, optionally filtered by status."""
        alerts = list(self._alerts.values())
        if status_filter:
            try:
                sf = AlertStatus(status_filter)
                alerts = [a for a in alerts if a.status == sf]
            except ValueError:
                pass
        return [a.to_dict() for a in sorted(alerts, key=lambda x: x.created_at, reverse=True)]

    # ── Alert Checking ────────────────────────────────────────────

    async def check_alerts(self) -> list[dict]:
        """Check all active alerts against current market prices.

        Returns list of triggered alerts.
        """
        active = [a for a in self._alerts.values() if a.status == AlertStatus.ACTIVE]
        if not active:
            return []

        # Get unique symbols
        symbols = list(set(a.symbol for a in active))

        # Fetch current prices
        try:
            from app.services.trading.trading_service import trading_service
            if not trading_service.is_connected:
                return []
            prices_data = await trading_service.get_prices(symbols)
        except Exception as e:
            logger.debug(f"Could not fetch prices for alerts: {e}")
            return []

        # Build price lookup
        current_prices: dict[str, float] = {}
        for p in prices_data:
            if isinstance(p, dict) and "symbol" in p and "last" in p:
                current_prices[p["symbol"].upper()] = p["last"]

        triggered: list[dict] = []
        for alert in active:
            price = current_prices.get(alert.symbol)
            if price is None:
                continue

            is_triggered = False
            if alert.alert_type == AlertType.PRICE_ABOVE and price >= alert.threshold:
                is_triggered = True
            elif alert.alert_type == AlertType.PRICE_BELOW and price <= alert.threshold:
                is_triggered = True
            elif alert.alert_type == AlertType.PCT_CHANGE:
                last = self._last_prices.get(alert.symbol)
                if last and last > 0:
                    change_pct = abs((price - last) / last) * 100
                    if change_pct >= alert.threshold:
                        is_triggered = True

            if is_triggered:
                alert.status = AlertStatus.TRIGGERED
                alert.triggered_at = datetime.now(timezone.utc).isoformat()
                alert.triggered_price = price

                event = {
                    "alert_id": alert.id,
                    "symbol": alert.symbol,
                    "alert_type": alert.alert_type.value,
                    "threshold": alert.threshold,
                    "current_price": price,
                    "message": alert.message or f"{alert.symbol} hit {alert.alert_type.value} {alert.threshold}",
                }
                triggered.append(event)

                self._triggered_history.append({
                    **event,
                    "triggered_at": alert.triggered_at,
                })
                if len(self._triggered_history) > 200:
                    self._triggered_history = self._triggered_history[-200:]

                # Broadcast event
                try:
                    from app.services.event_bus import event_bus
                    await event_bus.broadcast("market_alert_triggered", event)
                except Exception:
                    pass

                # Re-arm if repeating
                if alert.repeat:
                    alert.status = AlertStatus.ACTIVE
                    alert.triggered_at = None
                    alert.triggered_price = None

            # Update last known price
            self._last_prices[alert.symbol] = price

        if triggered:
            await self.save_to_redis()

        return triggered

    # ── Background Monitor ────────────────────────────────────────

    async def start(self, interval_seconds: int = 60):
        """Start background alert checking loop."""
        if self._running:
            return
        self._running = True

        async def _loop():
            while self._running:
                try:
                    triggered = await self.check_alerts()
                    if triggered:
                        logger.info(f"Market alerts triggered: {len(triggered)}")
                except Exception as e:
                    logger.error(f"Market alert check error: {e}")
                await asyncio.sleep(interval_seconds)

        self._check_task = asyncio.create_task(_loop(), name="market_alert_checker")
        logger.info(f"Market alert monitor started (interval={interval_seconds}s)")

    async def stop(self):
        """Stop background checking."""
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
            self._check_task = None

    # ── Status ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        active = sum(1 for a in self._alerts.values() if a.status == AlertStatus.ACTIVE)
        triggered = sum(1 for a in self._alerts.values() if a.status == AlertStatus.TRIGGERED)
        return {
            "running": self._running,
            "total_alerts": len(self._alerts),
            "active_alerts": active,
            "triggered_alerts": triggered,
            "recent_triggers": self._triggered_history[-10:],
        }


# Global singleton
market_alert_service = MarketAlertService()
