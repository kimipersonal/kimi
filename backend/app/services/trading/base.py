"""Base trading connector — abstract interface for all trading platforms."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import httpx


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class AccountInfo:
    platform: str
    account_id: str
    balance: float
    equity: float
    currency: str
    leverage: float = 1.0
    open_positions: int = 0
    is_demo: bool = True


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Position:
    id: str
    symbol: str
    side: OrderSide
    size: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Order:
    id: str
    symbol: str
    side: OrderSide
    type: OrderType
    size: float
    price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    filled_price: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PlatformEvaluation:
    """Result of AI evaluation of a trading platform."""

    platform: str
    is_connected: bool = False
    latency_ms: float = 0.0
    available_symbols: int = 0
    has_market_orders: bool = False
    has_limit_orders: bool = False
    has_stop_loss: bool = False
    has_take_profit: bool = False
    spreads_quality: str = "unknown"  # tight, moderate, wide
    api_reliability: str = "unknown"  # excellent, good, poor
    error: str = ""
    score: float = 0.0  # 0-100


class BaseTradingConnector(ABC):
    """Abstract base class for all trading platform connectors."""

    platform_name: str = "unknown"
    _client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the HTTP client, raising if not connected."""
        assert self._client is not None, f"{self.platform_name}: not connected"
        return self._client

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the trading platform. Returns True if successful."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the platform."""

    @abstractmethod
    async def get_account_info(self) -> AccountInfo:
        """Get account balance, equity, etc."""

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """Get current price for a symbol."""

    @abstractmethod
    async def get_symbols(self) -> list[str]:
        """Get list of tradeable symbols."""

    @abstractmethod
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
        """Place a trading order."""

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    async def close_position(self, position_id: str) -> Order:
        """Close a specific position."""

    @abstractmethod
    async def get_candles(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> list[Candle]:
        """Get OHLCV candlestick data."""

    @abstractmethod
    async def get_order_history(self, limit: int = 50) -> list[Order]:
        """Get recent order history."""

    async def evaluate(self) -> PlatformEvaluation:
        """Evaluate this platform's capabilities and quality."""
        import time

        eval_result = PlatformEvaluation(platform=self.platform_name)

        # Test connection
        start = time.time()
        try:
            connected = await self.connect()
            eval_result.latency_ms = (time.time() - start) * 1000
            eval_result.is_connected = connected
        except Exception as e:
            eval_result.error = str(e)[:200]
            return eval_result

        if not connected:
            eval_result.error = "Connection failed"
            return eval_result

        # Test features
        try:
            symbols = await self.get_symbols()
            eval_result.available_symbols = len(symbols)

            await self.get_account_info()

            # Test price feed with a common symbol
            test_symbols = ["EURUSD", "EUR/USD", "EUR_USD", "BTCUSDT", "BTC/USDT"]
            ticker = None
            for sym in test_symbols:
                if (
                    sym in symbols
                    or sym.replace("/", "") in symbols
                    or sym.replace("_", "/") in symbols
                ):
                    try:
                        ticker = await self.get_ticker(sym)
                        break
                    except Exception:
                        continue

            if ticker:
                spread_pct = (ticker.spread / ticker.bid) * 100 if ticker.bid > 0 else 0
                if spread_pct < 0.02:
                    eval_result.spreads_quality = "tight"
                elif spread_pct < 0.1:
                    eval_result.spreads_quality = "moderate"
                else:
                    eval_result.spreads_quality = "wide"

            eval_result.has_market_orders = True
            eval_result.has_limit_orders = True
            eval_result.api_reliability = "good"

            # Score: latency (30pts) + symbols (20pts) + spreads (30pts) + features (20pts)
            lat_score = max(0, 30 - (eval_result.latency_ms / 100))
            sym_score = min(20, eval_result.available_symbols / 5)
            spread_score = {"tight": 30, "moderate": 20, "wide": 10, "unknown": 5}.get(
                eval_result.spreads_quality, 5
            )
            feat_score = sum(
                [
                    5 if eval_result.has_market_orders else 0,
                    5 if eval_result.has_limit_orders else 0,
                    5 if eval_result.has_stop_loss else 0,
                    5 if eval_result.has_take_profit else 0,
                ]
            )
            eval_result.score = min(
                100, lat_score + sym_score + spread_score + feat_score
            )

        except Exception as e:
            eval_result.error = f"Evaluation error: {str(e)[:150]}"
            eval_result.api_reliability = "poor"

        return eval_result
