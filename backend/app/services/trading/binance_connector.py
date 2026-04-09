"""Binance Testnet connector — free crypto paper trading.

No registration required. Generate API keys at testnet.binance.vision.
24/7 markets, excellent REST API, zero country restrictions.
"""

import hashlib
import hmac
import logging
import time
from datetime import datetime
from urllib.parse import urlencode

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


class BinanceTestnetConnector(BaseTradingConnector):
    """Trading connector for Binance Spot Testnet."""

    platform_name = "Binance Testnet"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://testnet.binance.vision/api"
        self._client: httpx.AsyncClient | None = None

    def _sign(self, params: dict) -> dict:
        """Add timestamp and HMAC-SHA256 signature to request params."""
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    async def connect(self) -> bool:
        self._client = httpx.AsyncClient(timeout=15.0)
        # Test connectivity
        resp = await self._client.get(f"{self.base_url}/v3/ping")
        if resp.status_code != 200:
            return False
        # Test auth
        params = self._sign({})
        resp = await self._client.get(
            f"{self.base_url}/v3/account",
            headers=self._headers(),
            params=params,
        )
        if resp.status_code != 200:
            logger.error(f"Binance auth failed: {resp.status_code} {resp.text[:200]}")
            return False
        return True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_account_info(self) -> AccountInfo:
        params = self._sign({})
        resp = await self.client.get(
            f"{self.base_url}/v3/account",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        # Find USDT balance as primary
        balances = {
            b["asset"]: float(b["free"]) + float(b["locked"])
            for b in data.get("balances", [])
        }
        usdt_balance = balances.get("USDT", 0)

        # Count actual traded positions (not Testnet pre-funded dust)
        positions = await self.get_positions()

        return AccountInfo(
            platform=self.platform_name,
            account_id="testnet",
            balance=usdt_balance,
            equity=usdt_balance,  # Simplified — would need price conversion for full equity
            currency="USDT",
            open_positions=len(positions),
            is_demo=True,
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        # Normalize symbol (remove / and _)
        clean_symbol = symbol.replace("/", "").replace("_", "").upper()
        resp = await self.client.get(
            f"{self.base_url}/v3/ticker/bookTicker",
            params={"symbol": clean_symbol},
        )
        resp.raise_for_status()
        data = resp.json()
        bid = float(data.get("bidPrice", 0))
        ask = float(data.get("askPrice", 0))
        return Ticker(
            symbol=clean_symbol,
            bid=bid,
            ask=ask,
            last=(bid + ask) / 2,
        )

    async def get_symbols(self) -> list[str]:
        resp = await self.client.get(f"{self.base_url}/v3/exchangeInfo")
        resp.raise_for_status()
        data = resp.json()
        return [
            s["symbol"] for s in data.get("symbols", []) if s.get("status") == "TRADING"
        ]

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
        clean_symbol = symbol.replace("/", "").replace("_", "").upper()
        binance_type = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.STOP: "STOP_LOSS_LIMIT",
        }[order_type]

        params = {
            "symbol": clean_symbol,
            "side": side.value.upper(),
            "type": binance_type,
            "quantity": str(size),
        }

        if order_type == OrderType.LIMIT and price is not None:
            params["price"] = str(price)
            params["timeInForce"] = "GTC"
        elif order_type == OrderType.STOP and price is not None:
            params["stopPrice"] = str(price)
            params["price"] = str(price)
            params["timeInForce"] = "GTC"

        params = self._sign(params)
        resp = await self.client.post(
            f"{self.base_url}/v3/order",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        status_map = {
            "NEW": OrderStatus.PENDING,
            "FILLED": OrderStatus.FILLED,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
        }

        fills = data.get("fills", [])
        filled_price = float(fills[0]["price"]) if fills else 0

        return Order(
            id=str(data.get("orderId", "")),
            symbol=clean_symbol,
            side=side,
            type=order_type,
            size=size,
            price=price or 0,
            status=status_map.get(data.get("status", ""), OrderStatus.PENDING),
            filled_price=filled_price,
        )

    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Candle]:
        clean_symbol = symbol.replace("/", "").replace("_", "").upper()
        resp = await self.client.get(
            f"{self.base_url}/v3/klines",
            params={"symbol": clean_symbol, "interval": interval, "limit": limit},
        )
        resp.raise_for_status()
        return [
            Candle(
                timestamp=datetime.fromtimestamp(k[0] / 1000),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            )
            for k in resp.json()
        ]

    async def get_positions(self) -> list[Position]:
        """Binance spot doesn't have 'positions' — return non-zero balances.

        Only includes assets with meaningful holdings and excludes stablecoins
        and Binance Testnet pre-funded dust balances.
        """
        params = self._sign({})
        resp = await self.client.get(
            f"{self.base_url}/v3/account",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        # Stablecoins and quote currencies to exclude from positions
        QUOTE_ASSETS = {"USDT", "BUSD", "USDC", "USD", "DAI", "TUSD", "FDUSD"}

        # Get ticker prices so we can filter by USD value
        positions = []
        for b in data.get("balances", []):
            free = float(b["free"])
            locked = float(b["locked"])
            total = free + locked
            if total > 0 and b["asset"] not in QUOTE_ASSETS:
                positions.append(
                    Position(
                        id=b["asset"],
                        symbol=f"{b['asset']}USDT",
                        side=OrderSide.BUY,
                        size=total,
                        entry_price=0,  # Binance doesn't track entry price for spot
                    )
                )

        # If there are too many positions (Binance Testnet pre-funds hundreds
        # of assets), only return positions we actually traded.
        if len(positions) > 20:
            from app.db.database import async_session
            from sqlalchemy import text
            try:
                async with async_session() as session:
                    result = await session.execute(
                        text("SELECT DISTINCT symbol FROM trades WHERE platform = 'Binance Testnet' AND status = 'open'")
                    )
                    traded_symbols = {row[0] for row in result.fetchall()}
                if traded_symbols:
                    positions = [p for p in positions if p.symbol in traded_symbols]
                else:
                    # No tracked trades — return empty rather than 452 pre-funded balances
                    positions = []
            except Exception:
                # Fallback: return empty rather than misleading 452 positions
                positions = []

        return positions

    async def close_position(self, position_id: str) -> Order:
        """Sell all of a given asset (position_id = asset name like 'BTC')."""
        params = self._sign({})
        resp = await self.client.get(
            f"{self.base_url}/v3/account",
            headers=self._headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        asset_balance: float = 0
        for b in data.get("balances", []):
            if b["asset"] == position_id:
                asset_balance = float(b["free"])
                break

        if asset_balance <= 0:
            return Order(
                id="",
                symbol=f"{position_id}USDT",
                side=OrderSide.SELL,
                type=OrderType.MARKET,
                size=0,
                status=OrderStatus.REJECTED,
            )

        return await self.place_order(
            symbol=f"{position_id}USDT",
            side=OrderSide.SELL,
            size=asset_balance,
            order_type=OrderType.MARKET,
        )

    async def get_order_history(self, limit: int = 50) -> list[Order]:
        # Get orders for major pairs
        orders = []
        for symbol in ["BTCUSDT", "ETHUSDT"]:
            params = self._sign({"symbol": symbol, "limit": limit})
            resp = await self.client.get(
                f"{self.base_url}/v3/allOrders",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code == 200:
                for o in resp.json():
                    orders.append(
                        Order(
                            id=str(o.get("orderId", "")),
                            symbol=o.get("symbol", ""),
                            side=OrderSide.BUY
                            if o.get("side") == "BUY"
                            else OrderSide.SELL,
                            type=OrderType.MARKET
                            if o.get("type") == "MARKET"
                            else OrderType.LIMIT,
                            size=float(o.get("origQty", 0)),
                            status=OrderStatus.FILLED
                            if o.get("status") == "FILLED"
                            else OrderStatus.PENDING,
                            filled_price=float(o.get("price", 0)),
                            timestamp=datetime.fromtimestamp(o.get("time", 0) / 1000),
                        )
                    )
        return orders[:limit]
