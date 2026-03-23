"""MetaAPI connector — connects to MT5 accounts via MetaAPI.cloud REST API.

Uses the user's existing OANDA Global Markets MT5 demo account.
MetaAPI provides a REST wrapper around MT5, enabling HTTP-based trading.
Free tier: 1 account, unlimited API calls.
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


class MetaAPIConnector(BaseTradingConnector):
    """Trading connector for MT5 via MetaAPI.cloud."""

    platform_name = "MetaAPI (MT5)"

    def __init__(self, token: str, account_id: str):
        self.token = token
        self.account_id = account_id
        self.base_url = "https://mt-client-api-v1.agiliumtrade.agiliumtrade.ai"
        self.provisioning_url = (
            "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"
        )
        self._client: httpx.AsyncClient | None = None
        self._deployed = False

    def _headers(self) -> dict:
        return {"auth-token": self.token, "Content-Type": "application/json"}

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(timeout=30.0)

        # Check account state
        resp = await self._client.get(
            f"{self.provisioning_url}/users/current/accounts/{self.account_id}",
            headers=self._headers(),
        )
        if resp.status_code != 200:
            logger.error(
                f"MetaAPI account check failed: {resp.status_code} {resp.text[:200]}"
            )
            return False

        data = resp.json()
        state = data.get("state", "")
        logger.info(f"MetaAPI account state: {state}")

        if state != "DEPLOYED":
            # Deploy the account
            deploy_resp = await self._client.post(
                f"{self.provisioning_url}/users/current/accounts/{self.account_id}/deploy",
                headers=self._headers(),
            )
            if deploy_resp.status_code not in (200, 204):
                logger.error(f"MetaAPI deploy failed: {deploy_resp.status_code}")
                return False

        self._deployed = True
        return True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account_info(self) -> AccountInfo:
        resp = await self.client.get(
            f"{self.base_url}/users/current/accounts/{self.account_id}/account-information",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return AccountInfo(
            platform=self.platform_name,
            account_id=self.account_id,
            balance=data.get("balance", 0),
            equity=data.get("equity", 0),
            currency=data.get("currency", "USD"),
            leverage=data.get("leverage", 1),
            open_positions=len(await self.get_positions()),
            is_demo=True,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        resp = await self.client.get(
            f"{self.base_url}/users/current/accounts/{self.account_id}/symbols/{symbol}/current-price",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return Ticker(
            symbol=symbol,
            bid=data.get("bid", 0),
            ask=data.get("ask", 0),
            last=data.get("last", data.get("bid", 0)),
        )

    async def get_symbols(self) -> list[str]:
        resp = await self.client.get(
            f"{self.base_url}/users/current/accounts/{self.account_id}/symbols",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return [s.get("symbol", "") for s in resp.json()]

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
        action = "ORDER_TYPE_BUY" if side == OrderSide.BUY else "ORDER_TYPE_SELL"
        if order_type == OrderType.LIMIT:
            action += "_LIMIT"
        elif order_type == OrderType.STOP:
            action += "_STOP"

        body = {
            "symbol": symbol,
            "actionType": action,
            "volume": size,
        }
        if price is not None:
            body["openPrice"] = price
        if stop_loss is not None:
            body["stopLoss"] = stop_loss
        if take_profit is not None:
            body["takeProfit"] = take_profit

        resp = await self.client.post(
            f"{self.base_url}/users/current/accounts/{self.account_id}/trade",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        return Order(
            id=data.get("orderId", data.get("positionId", "")),
            symbol=symbol,
            side=side,
            type=order_type,
            size=size,
            price=price or 0,
            status=OrderStatus.FILLED
            if data.get("stringCode") == "TRADE_RETCODE_DONE"
            else OrderStatus.PENDING,
            filled_price=data.get("openPrice", 0),
        )

    async def get_positions(self) -> list[Position]:
        resp = await self.client.get(
            f"{self.base_url}/users/current/accounts/{self.account_id}/positions",
            headers=self._headers(),
        )
        resp.raise_for_status()
        positions = []
        for p in resp.json():
            positions.append(
                Position(
                    id=p.get("id", ""),
                    symbol=p.get("symbol", ""),
                    side=OrderSide.BUY
                    if p.get("type") == "POSITION_TYPE_BUY"
                    else OrderSide.SELL,
                    size=p.get("volume", 0),
                    entry_price=p.get("openPrice", 0),
                    current_price=p.get("currentPrice", 0),
                    unrealized_pnl=p.get("unrealizedProfit", 0),
                )
            )
        return positions

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Candle]:
        from datetime import datetime

        tf_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
            "1w": "1w",
        }
        tf = tf_map.get(interval, "1h")
        resp = await self.client.get(
            f"{self.base_url}/users/current/accounts/{self.account_id}"
            f"/historical-market-data/symbols/{symbol}/timeframes/{tf}/candles",
            headers=self._headers(),
            params={"limit": limit},
        )
        resp.raise_for_status()
        candles: list[Candle] = []
        for c in resp.json():
            ts = c.get("time", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                dt = datetime.utcnow()
            candles.append(
                Candle(
                    timestamp=dt,
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=float(c.get("tickVolume", 0)),
                )
            )
        return candles

    async def close_position(self, position_id: str) -> Order:
        resp = await self.client.post(
            f"{self.base_url}/users/current/accounts/{self.account_id}/trade",
            headers=self._headers(),
            json={
                "actionType": "POSITION_CLOSE_ID",
                "positionId": position_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return Order(
            id=position_id,
            symbol="",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=0,
            status=OrderStatus.FILLED
            if data.get("stringCode") == "TRADE_RETCODE_DONE"
            else OrderStatus.REJECTED,
        )

    async def get_order_history(self, limit: int = 50) -> list[Order]:
        resp = await self.client.get(
            f"{self.base_url}/users/current/accounts/{self.account_id}/history-orders-by-time-range",
            headers=self._headers(),
            params={"startTime": "2020-01-01T00:00:00Z", "limit": limit},
        )
        resp.raise_for_status()
        orders = []
        for o in resp.json():
            orders.append(
                Order(
                    id=o.get("id", ""),
                    symbol=o.get("symbol", ""),
                    side=OrderSide.BUY
                    if "BUY" in o.get("type", "")
                    else OrderSide.SELL,
                    type=OrderType.MARKET,
                    size=o.get("volume", 0),
                    status=OrderStatus.FILLED,
                    filled_price=o.get("openPrice", 0),
                )
            )
        return orders
