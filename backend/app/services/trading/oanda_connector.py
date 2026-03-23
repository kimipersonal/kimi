"""OANDA v20 connector — forex trading via REST API.

OANDA fxPractice (demo) accounts are free with full API access.
No KYC/passport required for demo. Simple Bearer token auth.
"""

import logging
from datetime import datetime, timezone

import httpx

from app.services.trading.base import (
    AccountInfo,
    BaseTradingConnector,
    Candle,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Ticker,
)

logger = logging.getLogger(__name__)

# OANDA granularity mapping
_INTERVAL_MAP = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
    "1w": "W",
}


class OandaConnector(BaseTradingConnector):
    """Trading connector for OANDA v20 REST API (demo/practice)."""

    platform_name = "OANDA"

    def __init__(self, api_key: str, account_id: str, api_url: str):
        self.api_key = api_key
        self.account_id = account_id
        self.api_url = api_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(timeout=15.0)
        try:
            resp = await self._client.get(
                f"{self.api_url}/v3/accounts/{self.account_id}/summary",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                logger.error(f"OANDA connect failed: {resp.status_code} {resp.text[:200]}")
                return False
            logger.info("OANDA connected successfully")
            return True
        except Exception as e:
            logger.error(f"OANDA connect error: {e}")
            return False

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account_info(self) -> AccountInfo:
        resp = await self.client.get(
            f"{self.api_url}/v3/accounts/{self.account_id}/summary",
            headers=self._headers(),
        )
        resp.raise_for_status()
        acct = resp.json()["account"]
        return AccountInfo(
            platform=self.platform_name,
            account_id=self.account_id,
            balance=float(acct["balance"]),
            equity=float(acct.get("NAV", acct["balance"])),
            currency=acct.get("currency", "USD"),
            leverage=1.0 / float(acct.get("marginRate", "0.02") or "0.02"),
            open_positions=int(acct.get("openPositionCount", 0)),
            is_demo="practice" in self.api_url or "fxpractice" in self.api_url,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        instrument = self._to_oanda_symbol(symbol)
        resp = await self.client.get(
            f"{self.api_url}/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instrument},
            headers=self._headers(),
        )
        resp.raise_for_status()
        prices = resp.json().get("prices", [])
        if not prices:
            raise ValueError(f"No pricing data for {symbol}")
        p = prices[0]
        bid = float(p["bids"][0]["price"]) if p.get("bids") else 0.0
        ask = float(p["asks"][0]["price"]) if p.get("asks") else 0.0
        return Ticker(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=(bid + ask) / 2,
            timestamp=datetime.now(timezone.utc),
        )

    async def get_symbols(self) -> list[str]:
        resp = await self.client.get(
            f"{self.api_url}/v3/accounts/{self.account_id}/instruments",
            headers=self._headers(),
        )
        resp.raise_for_status()
        instruments = resp.json().get("instruments", [])
        return [i["name"] for i in instruments]

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> Order:
        instrument = self._to_oanda_symbol(symbol)
        units = size if side == OrderSide.BUY else -size

        if order_type == OrderType.MARKET:
            order_body: dict = {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
            }
        elif order_type == OrderType.LIMIT:
            order_body = {
                "type": "LIMIT",
                "instrument": instrument,
                "units": str(units),
                "price": str(price),
                "timeInForce": "GTC",
            }
        else:  # STOP
            order_body = {
                "type": "STOP",
                "instrument": instrument,
                "units": str(units),
                "price": str(price),
                "timeInForce": "GTC",
            }

        if stop_loss is not None:
            order_body["stopLossOnFill"] = {"price": str(stop_loss)}
        if take_profit is not None:
            order_body["takeProfitOnFill"] = {"price": str(take_profit)}

        resp = await self.client.post(
            f"{self.api_url}/v3/accounts/{self.account_id}/orders",
            json={"order": order_body},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()

        # Market orders fill immediately
        if "orderFillTransaction" in data:
            fill = data["orderFillTransaction"]
            return Order(
                id=str(fill.get("id", "")),
                symbol=symbol,
                side=side,
                type=order_type,
                size=abs(float(fill.get("units", size))),
                price=float(fill.get("price", 0)),
                filled_price=float(fill.get("price", 0)),
                status=OrderStatus.FILLED,
            )

        # Pending (limit/stop) orders
        if "orderCreateTransaction" in data:
            created = data["orderCreateTransaction"]
            return Order(
                id=str(created.get("id", "")),
                symbol=symbol,
                side=side,
                type=order_type,
                size=size,
                price=float(created.get("price", 0)),
                status=OrderStatus.PENDING,
            )

        # Rejection
        if "orderRejectTransaction" in data:
            rej = data["orderRejectTransaction"]
            raise RuntimeError(f"Order rejected: {rej.get('rejectReason', 'unknown')}")

        raise RuntimeError(f"Unexpected OANDA response: {data}")

    async def get_positions(self) -> list[Position]:
        resp = await self.client.get(
            f"{self.api_url}/v3/accounts/{self.account_id}/openPositions",
            headers=self._headers(),
        )
        resp.raise_for_status()
        positions = []
        for p in resp.json().get("positions", []):
            long_units = float(p.get("long", {}).get("units", 0))
            short_units = float(p.get("short", {}).get("units", 0))

            if long_units > 0:
                side_data = p["long"]
                side = OrderSide.BUY
                units = long_units
            elif short_units < 0:
                side_data = p["short"]
                side = OrderSide.SELL
                units = abs(short_units)
            else:
                continue

            positions.append(
                Position(
                    id=p["instrument"],
                    symbol=p["instrument"],
                    side=side,
                    size=units,
                    entry_price=float(side_data.get("averagePrice", 0)),
                    unrealized_pnl=float(side_data.get("unrealizedPL", 0)),
                )
            )
        return positions

    async def close_position(self, position_id: str) -> Order:
        # OANDA uses instrument name as position ID
        # Try closing long first, then short
        instrument = position_id
        resp = await self.client.put(
            f"{self.api_url}/v3/accounts/{self.account_id}/positions/{instrument}/close",
            json={"longUnits": "ALL"},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            # Try short side
            resp = await self.client.put(
                f"{self.api_url}/v3/accounts/{self.account_id}/positions/{instrument}/close",
                json={"shortUnits": "ALL"},
                headers=self._headers(),
            )
        resp.raise_for_status()
        data = resp.json()

        # Extract from the close transaction
        txn = data.get("longOrderFillTransaction") or data.get("shortOrderFillTransaction", {})
        return Order(
            id=str(txn.get("id", "")),
            symbol=instrument,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=abs(float(txn.get("units", 0))),
            filled_price=float(txn.get("price", 0)),
            status=OrderStatus.FILLED,
        )

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Candle]:
        instrument = self._to_oanda_symbol(symbol)
        granularity = _INTERVAL_MAP.get(interval, "H1")
        resp = await self.client.get(
            f"{self.api_url}/v3/instruments/{instrument}/candles",
            params={
                "granularity": granularity,
                "count": min(limit, 5000),
                "price": "M",  # mid prices
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        candles = []
        for c in resp.json().get("candles", []):
            if not c.get("complete", True):
                continue
            mid = c.get("mid", {})
            candles.append(
                Candle(
                    timestamp=datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
                    open=float(mid.get("o", 0)),
                    high=float(mid.get("h", 0)),
                    low=float(mid.get("l", 0)),
                    close=float(mid.get("c", 0)),
                    volume=float(c.get("volume", 0)),
                )
            )
        return candles

    async def get_order_history(self, limit: int = 50) -> list[Order]:
        resp = await self.client.get(
            f"{self.api_url}/v3/accounts/{self.account_id}/orders",
            params={"count": limit},
            headers=self._headers(),
        )
        resp.raise_for_status()
        orders = []
        for o in resp.json().get("orders", []):
            units = float(o.get("units", 0))
            orders.append(
                Order(
                    id=str(o.get("id", "")),
                    symbol=o.get("instrument", ""),
                    side=OrderSide.BUY if units > 0 else OrderSide.SELL,
                    type=OrderType.LIMIT
                    if o.get("type") == "LIMIT"
                    else OrderType.STOP
                    if o.get("type") == "STOP"
                    else OrderType.MARKET,
                    size=abs(units),
                    price=float(o.get("price", 0)),
                    status=OrderStatus.PENDING,
                )
            )
        return orders

    @staticmethod
    def _to_oanda_symbol(symbol: str) -> str:
        """Convert symbol format to OANDA format (EUR/USD → EUR_USD)."""
        s = symbol.strip().upper()
        if "_" in s:
            return s
        if "/" in s:
            return s.replace("/", "_")
        # Try to split 6-char forex pairs (EURUSD → EUR_USD)
        if len(s) == 6 and s.isalpha():
            return f"{s[:3]}_{s[3:]}"
        return s
