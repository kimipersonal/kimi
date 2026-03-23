"""Capital.com connector — forex/stocks/crypto via REST API.

Capital.com accepts most countries including Uzbekistan.
Free demo account with virtual funds. Good REST API.
"""

import logging

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


class CapitalComConnector(BaseTradingConnector):
    """Trading connector for Capital.com demo."""

    platform_name = "Capital.com"

    def __init__(self, api_key: str, email: str, password: str, demo: bool = True):
        self.api_key = api_key
        self.email = email
        self.password = password
        base = (
            "demo-api-capital.backend-capital.com"
            if demo
            else "api-capital.backend-capital.com"
        )
        self.base_url = f"https://{base}/api/v1"
        self._client: httpx.AsyncClient | None = None
        self._cst: str = ""  # Client session token
        self._security_token: str = ""

    def _headers(self) -> dict:
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": self._cst,
            "X-SECURITY-TOKEN": self._security_token,
            "Content-Type": "application/json",
        }

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(timeout=15.0)

        # Create session (login)
        resp = await self._client.post(
            f"{self.base_url}/session",
            json={
                "identifier": self.email,
                "password": self.password,
            },
            headers={
                "X-CAP-API-KEY": self.api_key,
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            logger.error(
                f"Capital.com login failed: {resp.status_code} {resp.text[:200]}"
            )
            return False

        self._cst = resp.headers.get("CST", "")
        self._security_token = resp.headers.get("X-SECURITY-TOKEN", "")
        return bool(self._cst and self._security_token)

    async def disconnect(self) -> None:
        if self._client and self._cst:
            try:
                await self._client.delete(
                    f"{self.base_url}/session",
                    headers=self._headers(),
                )
            except Exception:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account_info(self) -> AccountInfo:
        resp = await self.client.get(
            f"{self.base_url}/accounts",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        accounts = data.get("accounts", [])
        if not accounts:
            raise RuntimeError("No accounts found")
        acc = accounts[0]
        return AccountInfo(
            platform=self.platform_name,
            account_id=acc.get("accountId", ""),
            balance=acc.get("balance", {}).get("balance", 0),
            equity=acc.get("balance", {}).get("equity", 0)
            or acc.get("balance", {}).get("balance", 0),
            currency=acc.get("currency", "USD"),
            is_demo=acc.get("accountType", "") == "DEMO",
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        # Capital.com uses epics like "EURUSD" without separators
        clean = symbol.replace("/", "").replace("_", "").upper()
        resp = await self.client.get(
            f"{self.base_url}/markets/{clean}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        snapshot = data.get("snapshot", {})
        return Ticker(
            symbol=clean,
            bid=snapshot.get("bid", 0),
            ask=snapshot.get("offer", 0),
            last=(snapshot.get("bid", 0) + snapshot.get("offer", 0)) / 2,
        )

    async def get_symbols(self) -> list[str]:
        symbols = []
        # Get popular markets
        for search_term in ["EUR", "GBP", "BTC", "AAPL"]:
            resp = await self.client.get(
                f"{self.base_url}/markets",
                headers=self._headers(),
                params={"searchTerm": search_term, "limit": 20},
            )
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get("markets", []):
                    epic = m.get("epic", "")
                    if epic and epic not in symbols:
                        symbols.append(epic)
        return symbols

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Candle]:
        from datetime import datetime

        clean = symbol.replace("/", "").replace("_", "").upper()
        resolution_map = {
            "1m": "MINUTE",
            "5m": "MINUTE_5",
            "15m": "MINUTE_15",
            "30m": "MINUTE_30",
            "1h": "HOUR",
            "4h": "HOUR_4",
            "1d": "DAY",
            "1w": "WEEK",
        }
        resolution = resolution_map.get(interval, "HOUR")
        resp = await self.client.get(
            f"{self.base_url}/prices/{clean}",
            headers=self._headers(),
            params={"resolution": resolution, "max": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        candles: list[Candle] = []
        for p in data.get("prices", []):
            ts = p.get("snapshotTime", "")
            try:
                dt = datetime.fromisoformat(ts.replace("/", "-"))
            except (ValueError, AttributeError):
                dt = datetime.utcnow()
            candles.append(
                Candle(
                    timestamp=dt,
                    open=float(p.get("openPrice", {}).get("ask", 0)),
                    high=float(p.get("highPrice", {}).get("ask", 0)),
                    low=float(p.get("lowPrice", {}).get("ask", 0)),
                    close=float(p.get("closePrice", {}).get("ask", 0)),
                    volume=float(p.get("lastTradedVolume", 0)),
                )
            )
        return candles

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
        clean = symbol.replace("/", "").replace("_", "").upper()
        direction = "BUY" if side == OrderSide.BUY else "SELL"

        if order_type == OrderType.MARKET:
            body = {
                "epic": clean,
                "direction": direction,
                "size": size,
            }
            if stop_loss is not None:
                body["stopLevel"] = stop_loss
            if take_profit is not None:
                body["profitLevel"] = take_profit

            resp = await self.client.post(
                f"{self.base_url}/positions",
                headers=self._headers(),
                json=body,
            )
        else:
            body = {
                "epic": clean,
                "direction": direction,
                "size": size,
                "level": price,
                "type": "LIMIT" if order_type == OrderType.LIMIT else "STOP",
            }
            if stop_loss is not None:
                body["stopLevel"] = stop_loss
            if take_profit is not None:
                body["profitLevel"] = take_profit

            resp = await self.client.post(
                f"{self.base_url}/workingorders",
                headers=self._headers(),
                json=body,
            )

        resp.raise_for_status()
        data = resp.json()

        return Order(
            id=data.get("dealReference", ""),
            symbol=clean,
            side=side,
            type=order_type,
            size=size,
            price=price or 0,
            status=OrderStatus.FILLED
            if data.get("dealStatus") == "ACCEPTED"
            else OrderStatus.PENDING,
        )

    async def get_positions(self) -> list[Position]:
        resp = await self.client.get(
            f"{self.base_url}/positions",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        positions = []
        for p in data.get("positions", []):
            pos_data = p.get("position", {})
            market = p.get("market", {})
            positions.append(
                Position(
                    id=pos_data.get("dealId", ""),
                    symbol=market.get("epic", ""),
                    side=OrderSide.BUY
                    if pos_data.get("direction") == "BUY"
                    else OrderSide.SELL,
                    size=pos_data.get("size", 0),
                    entry_price=pos_data.get("level", 0),
                    current_price=market.get("bid", 0),
                    unrealized_pnl=pos_data.get("upl", 0),
                )
            )
        return positions

    async def close_position(self, position_id: str) -> Order:
        resp = await self.client.delete(
            f"{self.base_url}/positions/{position_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return Order(
            id=position_id,
            symbol="",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=0,
            status=OrderStatus.FILLED,
        )

    async def get_order_history(self, limit: int = 50) -> list[Order]:
        resp = await self.client.get(
            f"{self.base_url}/history/activity",
            headers=self._headers(),
            params={"lastPeriod": 604800},  # Last 7 days
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        orders = []
        for a in data.get("activities", [])[:limit]:
            orders.append(
                Order(
                    id=a.get("dealId", ""),
                    symbol=a.get("epic", ""),
                    side=OrderSide.BUY
                    if a.get("direction") == "BUY"
                    else OrderSide.SELL,
                    type=OrderType.MARKET,
                    size=float(a.get("size", 0)),
                    status=OrderStatus.FILLED
                    if a.get("status") == "ACCEPTED"
                    else OrderStatus.REJECTED,
                )
            )
        return orders
