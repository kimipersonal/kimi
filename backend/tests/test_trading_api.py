"""Tests for trading API — portfolio, positions, signals, trade execution."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException


class TestPortfolio:
    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_portfolio_connected(self, mock_ts):
        mock_ts.is_connected = True
        mock_ts.get_portfolio_summary = AsyncMock(return_value={
            "total_equity": 10000.0,
            "positions_count": 3,
        })

        from app.api.trading import get_portfolio
        result = await get_portfolio()
        assert result["total_equity"] == 10000.0

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_portfolio_disconnected(self, mock_ts):
        mock_ts.is_connected = False

        from app.api.trading import get_portfolio
        result = await get_portfolio()
        assert "error" in result
        assert result["platforms_connected"] == 0


class TestPositions:
    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_positions_connected(self, mock_ts):
        mock_ts.is_connected = True
        mock_ts.get_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "side": "long", "size": 0.1},
        ])

        from app.api.trading import get_positions
        result = await get_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "BTCUSDT"

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_positions_disconnected(self, mock_ts):
        mock_ts.is_connected = False

        from app.api.trading import get_positions
        result = await get_positions()
        assert result == []


class TestSignals:
    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_signals(self, mock_ts):
        mock_ts.get_signals = AsyncMock(return_value=[
            {"id": "sig-1", "symbol": "ETHUSDT", "action": "buy", "status": "pending"},
        ])

        from app.api.trading import get_signals
        result = await get_signals(status="pending", limit=10)
        assert len(result) == 1
        assert result[0]["id"] == "sig-1"

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_decide_signal_approve(self, mock_ts):
        mock_ts.approve_signal = AsyncMock(return_value={"id": "sig-1", "status": "approved"})

        from app.api.trading import decide_signal, SignalDecision
        result = await decide_signal("sig-1", SignalDecision(approved=True))
        assert result["status"] == "approved"

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_decide_signal_reject(self, mock_ts):
        mock_ts.reject_signal = AsyncMock(return_value={"id": "sig-1", "status": "rejected"})

        from app.api.trading import decide_signal, SignalDecision
        result = await decide_signal("sig-1", SignalDecision(approved=False, reason="Not now"))
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_decide_signal_not_found(self, mock_ts):
        mock_ts.approve_signal = AsyncMock(return_value=None)

        from app.api.trading import decide_signal, SignalDecision
        with pytest.raises(HTTPException) as exc_info:
            await decide_signal("nope", SignalDecision(approved=True))
        assert exc_info.value.status_code == 404


class TestTradeExecution:
    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_execute_trade(self, mock_ts):
        mock_ts.is_connected = True
        mock_ts.execute_trade = AsyncMock(return_value={"order_id": "order-1", "status": "filled"})

        from app.api.trading import execute_trade, TradeRequest
        result = await execute_trade(TradeRequest(
            symbol="BTCUSDT", side="buy", size=0.01,
        ))
        assert result["order_id"] == "order-1"

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_execute_trade_disconnected(self, mock_ts):
        mock_ts.is_connected = False

        from app.api.trading import execute_trade, TradeRequest
        with pytest.raises(HTTPException) as exc_info:
            await execute_trade(TradeRequest(symbol="BTCUSDT", side="buy", size=0.01))
        assert exc_info.value.status_code == 503


class TestTradeHistory:
    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_trade_history(self, mock_ts):
        mock_ts.get_trade_history = AsyncMock(return_value=[
            {"order_id": "order-1", "symbol": "BTCUSDT", "side": "buy"},
        ])

        from app.api.trading import get_trade_history
        result = await get_trade_history(limit=10)
        assert len(result) == 1


class TestMarketData:
    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_market_data(self, mock_ts):
        mock_ts.is_connected = True
        mock_ts.get_prices = AsyncMock(return_value=[{"symbol": "BTCUSDT", "price": 65000.0}])

        from app.api.trading import get_market_data
        result = await get_market_data("BTCUSDT")
        assert result["price"] == 65000.0

    @pytest.mark.asyncio
    @patch("app.services.trading.trading_service.trading_service")
    async def test_get_market_data_disconnected(self, mock_ts):
        mock_ts.is_connected = False

        from app.api.trading import get_market_data
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data("BTCUSDT")
        assert exc_info.value.status_code == 503
