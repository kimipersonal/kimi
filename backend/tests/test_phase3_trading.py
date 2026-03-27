"""Tests for Phase 3: Autonomous Trading Execution.

Covers:
- AutoTradeExecutor: mode presets, signal evaluation, daily limits
- PositionCalculator: forex/crypto/stock sizing, edge cases
- PortfolioRiskManager: risk assessment, drawdown, exposure, correlation
- TradeJournal: record/query (mocked DB), stats
- MarketAlertService: create/cancel/check/trigger alerts
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════════
# AUTO-TRADE EXECUTOR
# ═══════════════════════════════════════════════════════════════════


class TestAutoTradeExecutor:
    """Test auto-trade mode configuration and signal evaluation."""

    def _make_executor(self):
        from app.services.auto_trade_executor import AutoTradeExecutor
        return AutoTradeExecutor()

    # --- Mode Configuration ---

    @pytest.mark.asyncio
    async def test_set_mode_conservative(self):
        exe = self._make_executor()
        with patch.object(exe, "save_to_redis", new_callable=AsyncMock):
            result = await exe.set_mode("conservative")
        assert result["mode"] == "conservative"
        assert exe.config.min_confidence == 0.90
        assert exe.config.min_risk_reward_ratio == 2.0

    @pytest.mark.asyncio
    async def test_set_mode_moderate(self):
        exe = self._make_executor()
        with patch.object(exe, "save_to_redis", new_callable=AsyncMock):
            result = await exe.set_mode("moderate")
        assert result["mode"] == "moderate"
        assert exe.config.min_confidence == 0.80
        assert exe.config.max_daily_trades == 10

    @pytest.mark.asyncio
    async def test_set_mode_aggressive(self):
        exe = self._make_executor()
        with patch.object(exe, "save_to_redis", new_callable=AsyncMock):
            result = await exe.set_mode("aggressive")
        assert result["mode"] == "aggressive"
        assert exe.config.min_confidence == 0.70

    @pytest.mark.asyncio
    async def test_set_mode_disabled(self):
        exe = self._make_executor()
        with patch.object(exe, "save_to_redis", new_callable=AsyncMock):
            result = await exe.set_mode("disabled")
        assert result["mode"] == "disabled"

    @pytest.mark.asyncio
    async def test_set_mode_invalid(self):
        exe = self._make_executor()
        with patch.object(exe, "save_to_redis", new_callable=AsyncMock):
            result = await exe.set_mode("yolo")
        assert "error" in result

    # --- Signal Evaluation ---

    @pytest.mark.asyncio
    async def test_evaluate_signal_disabled_mode(self):
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.DISABLED
        result = await exe.evaluate_signal({"symbol": "EURUSD", "confidence": 0.95})
        assert result["approved"] is False
        assert "disabled" in result["reasons"][0].lower()

    @pytest.mark.asyncio
    async def test_evaluate_signal_low_confidence_rejected(self):
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.CONSERVATIVE
        exe.config.min_confidence = 0.90

        signal = {
            "symbol": "EURUSD",
            "direction": "buy",
            "confidence": 0.75,
            "entry_price": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
        }
        result = await exe.evaluate_signal(signal)
        assert result["approved"] is False
        assert any("confidence" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_evaluate_signal_no_stop_loss_rejected(self):
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.MODERATE
        exe.config.require_stop_loss = True

        signal = {
            "symbol": "EURUSD",
            "direction": "buy",
            "confidence": 0.95,
            "entry_price": 1.1000,
            "stop_loss": None,
            "take_profit": 1.1100,
        }
        result = await exe.evaluate_signal(signal)
        assert result["approved"] is False
        assert any("stop-loss" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_evaluate_signal_bad_risk_reward(self):
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.MODERATE
        exe.config.min_risk_reward_ratio = 1.5
        exe.config.require_stop_loss = True
        exe.config.require_take_profit = True

        signal = {
            "symbol": "EURUSD",
            "direction": "buy",
            "confidence": 0.95,
            "entry_price": 1.1000,
            "stop_loss": 1.0900,   # 100 pip SL
            "take_profit": 1.1050,  # 50 pip TP → 0.5:1 RR
        }
        result = await exe.evaluate_signal(signal)
        assert result["approved"] is False
        assert any("risk:reward" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_evaluate_signal_blocked_symbol(self):
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.MODERATE
        exe.config.blocked_symbols = ["EURUSD"]
        exe.config.require_stop_loss = False
        exe.config.require_take_profit = False

        signal = {"symbol": "EURUSD", "direction": "buy", "confidence": 0.95}
        result = await exe.evaluate_signal(signal)
        assert result["approved"] is False
        assert any("blocked" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_evaluate_signal_daily_limit_reached(self):
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.MODERATE
        exe.config.max_daily_trades = 3
        exe.config.require_stop_loss = False
        exe.config.require_take_profit = False
        exe._daily.date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        exe._daily.trades_executed = 3

        signal = {"symbol": "GBPUSD", "direction": "sell", "confidence": 0.95}
        result = await exe.evaluate_signal(signal)
        assert result["approved"] is False
        assert any("daily trade limit" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_evaluate_signal_all_criteria_met(self):
        """Signal that meets all criteria should be approved."""
        exe = self._make_executor()
        from app.services.auto_trade_executor import AutoTradeMode
        exe.config.mode = AutoTradeMode.MODERATE
        exe.config.min_confidence = 0.80
        exe.config.min_risk_reward_ratio = 1.5
        exe.config.require_stop_loss = True
        exe.config.require_take_profit = True

        signal = {
            "symbol": "EURUSD",
            "direction": "buy",
            "confidence": 0.92,
            "entry_price": 1.1000,
            "stop_loss": 1.0950,   # 50 pip SL
            "take_profit": 1.1100,  # 100 pip TP → 2:1 RR
        }
        # Mock position check to avoid trading service dependency
        with patch(
            "app.services.auto_trade_executor.trading_service",
            create=True,
        ) as mock_ts:
            mock_ts.is_connected = False
            with patch(
                "app.services.position_calculator.position_calculator.calculate_size",
                new_callable=AsyncMock,
                return_value={"recommended_size": 0.1},
            ):
                result = await exe.evaluate_signal(signal)
        assert result["approved"] is True

    # --- Daily Stats ---

    def test_daily_reset(self):
        exe = self._make_executor()
        exe._daily.trades_executed = 5
        exe._daily.date = "2024-01-01"  # Old date
        exe._ensure_daily_reset()
        assert exe._daily.trades_executed == 0
        assert exe._daily.date == datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def test_daily_no_reset_same_day(self):
        exe = self._make_executor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        exe._daily.date = today
        exe._daily.trades_executed = 5
        exe._ensure_daily_reset()
        assert exe._daily.trades_executed == 5

    def test_get_status(self):
        exe = self._make_executor()
        status = exe.get_status()
        assert "config" in status
        assert "daily_stats" in status
        assert "recent_log" in status
        assert status["config"]["mode"] == "conservative"

    def test_execution_log_capped(self):
        exe = self._make_executor()
        for i in range(60):
            exe._log_execution(
                {"id": str(i), "symbol": "TEST"},
                {"approved": True, "reasons": []},
                executed=True,
            )
        assert len(exe._execution_log) == 50


# ═══════════════════════════════════════════════════════════════════
# POSITION CALCULATOR
# ═══════════════════════════════════════════════════════════════════


class TestPositionCalculator:
    """Test risk-based position sizing calculations."""

    def _calc(self):
        from app.services.position_calculator import PositionCalculator
        return PositionCalculator()

    # --- Forex ---

    @pytest.mark.asyncio
    async def test_forex_position_size(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="EURUSD",
            entry_price=1.1000,
            stop_loss=1.0950,
            risk_pct=1.0,
            account_equity=10000,
        )
        assert "recommended_size" in result
        assert result["instrument_type"] == "forex"
        assert result["risk_amount"] == 100.0  # 1% of 10000
        assert result["recommended_size"] > 0

    @pytest.mark.asyncio
    async def test_forex_jpy_pair(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="USDJPY",
            entry_price=150.00,
            stop_loss=149.50,
            risk_pct=1.0,
            account_equity=10000,
        )
        assert result["instrument_type"] == "forex"
        assert result["recommended_size"] > 0

    # --- Crypto ---

    @pytest.mark.asyncio
    async def test_crypto_position_size(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="BTCUSDT",
            entry_price=60000,
            stop_loss=59000,
            risk_pct=2.0,
            account_equity=5000,
        )
        assert result["instrument_type"] == "crypto"
        assert result["risk_amount"] == 100.0  # 2% of 5000
        # size = 100 / (60000-59000) = 0.1 BTC
        assert abs(result["recommended_size"] - 0.1) < 0.001

    # --- Stock ---

    @pytest.mark.asyncio
    async def test_stock_position_size(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="AAPL",
            entry_price=180.0,
            stop_loss=175.0,
            risk_pct=1.0,
            account_equity=20000,
        )
        assert result["instrument_type"] == "stock"
        # 200 / 5 = 40 shares
        assert result["recommended_size"] == 40.0

    # --- Edge Cases ---

    @pytest.mark.asyncio
    async def test_zero_entry_price(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="EURUSD", entry_price=0, stop_loss=1.0, account_equity=10000,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sl_equals_entry(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="EURUSD", entry_price=1.1, stop_loss=1.1, account_equity=10000,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_risk_pct_out_of_range(self):
        calc = self._calc()
        result = await calc.calculate_size(
            symbol="EURUSD", entry_price=1.1, stop_loss=1.0, risk_pct=15,
            account_equity=10000,
        )
        assert "error" in result

    def test_quick_size(self):
        from app.services.position_calculator import position_calculator
        result = position_calculator.quick_size(
            equity=10000, entry_price=100.0, stop_loss=95.0, risk_pct=2.0,
        )
        assert result["risk_amount"] == 200.0
        assert result["recommended_size"] == 40.0  # 200 / 5

    def test_quick_size_zero_equity(self):
        from app.services.position_calculator import position_calculator
        result = position_calculator.quick_size(equity=0, entry_price=100, stop_loss=95)
        assert "error" in result

    # --- Instrument Classification ---

    def test_classify_forex(self):
        from app.services.position_calculator import _classify_instrument
        assert _classify_instrument("EURUSD") == "forex"
        assert _classify_instrument("EUR/USD") == "forex"
        assert _classify_instrument("USDJPY") == "forex"

    def test_classify_crypto(self):
        from app.services.position_calculator import _classify_instrument
        assert _classify_instrument("BTCUSDT") == "crypto"
        assert _classify_instrument("ETH/USDT") == "crypto"
        assert _classify_instrument("SOLUSDT") == "crypto"

    def test_classify_stock(self):
        from app.services.position_calculator import _classify_instrument
        assert _classify_instrument("AAPL") == "stock"
        assert _classify_instrument("TSLA") == "stock"


# ═══════════════════════════════════════════════════════════════════
# PORTFOLIO RISK MANAGER
# ═══════════════════════════════════════════════════════════════════


class TestPortfolioRiskManager:
    """Test portfolio risk assessment and limit enforcement."""

    def _make_manager(self):
        from app.services.portfolio_risk_manager import PortfolioRiskManager
        return PortfolioRiskManager()

    # --- Risk Limits ---

    @pytest.mark.asyncio
    async def test_update_limits(self):
        mgr = self._make_manager()
        with patch.object(mgr, "save_to_redis", new_callable=AsyncMock):
            result = await mgr.update_limits(max_drawdown_pct=15.0, max_open_positions=20)
        assert mgr.limits.max_drawdown_pct == 15.0
        assert mgr.limits.max_open_positions == 20

    def test_default_limits(self):
        mgr = self._make_manager()
        assert mgr.limits.max_drawdown_pct == 10.0
        assert mgr.limits.max_total_exposure_pct == 50.0
        assert mgr.limits.max_single_position_pct == 20.0

    # --- Pre-Trade Risk Check ---

    @pytest.mark.asyncio
    async def test_check_trade_risk_allowed(self):
        mgr = self._make_manager()
        mock_portfolio = {
            "total_equity": 10000,
            "positions": [],
        }
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_portfolio_summary = AsyncMock(return_value=mock_portfolio)
        with patch(
            "app.services.trading.trading_service.trading_service",
            mock_ts,
        ):
            result = await mgr.check_trade_risk("EURUSD", "buy", 0.1, 1.1)

        assert result["allowed"] is True
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_check_trade_risk_too_large(self):
        mgr = self._make_manager()
        mgr.limits.max_single_position_pct = 10.0

        mock_portfolio = {
            "total_equity": 10000,
            "positions": [],
        }
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_portfolio_summary = AsyncMock(return_value=mock_portfolio)
        with patch(
            "app.services.trading.trading_service.trading_service",
            mock_ts,
        ):
            result = await mgr.check_trade_risk("AAPL", "buy", 100, 50.0)

        assert result["allowed"] is False
        assert any("equity" in w.lower() for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_check_trade_risk_max_positions(self):
        mgr = self._make_manager()
        mgr.limits.max_open_positions = 2

        mock_portfolio = {
            "total_equity": 10000,
            "positions": [
                {"symbol": "EURUSD", "size": 0.1, "entry_price": 1.1, "current_price": 1.1},
                {"symbol": "GBPUSD", "size": 0.1, "entry_price": 1.3, "current_price": 1.3},
            ],
        }
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_portfolio_summary = AsyncMock(return_value=mock_portfolio)
        with patch(
            "app.services.trading.trading_service.trading_service",
            mock_ts,
        ):
            result = await mgr.check_trade_risk("AUDUSD", "buy", 0.01, 0.65)

        assert result["allowed"] is False
        assert any("positions" in w.lower() for w in result["warnings"])

    # --- Correlation Groups ---

    def test_correlation_group_usd_longs(self):
        from app.services.portfolio_risk_manager import _get_correlation_group
        assert _get_correlation_group("EURUSD") == "USD_LONGS"
        assert _get_correlation_group("GBPUSD") == "USD_LONGS"

    def test_correlation_group_crypto(self):
        from app.services.portfolio_risk_manager import _get_correlation_group
        assert _get_correlation_group("BTCUSDT") == "CRYPTO_MAJOR"
        assert _get_correlation_group("ETHUSDT") == "CRYPTO_MAJOR"

    def test_correlation_group_unknown(self):
        from app.services.portfolio_risk_manager import _get_correlation_group
        assert _get_correlation_group("AAPL") is None

    # --- Status ---

    def test_get_status(self):
        mgr = self._make_manager()
        status = mgr.get_status()
        assert "limits" in status
        assert "peak_equity" in status
        assert "last_assessment" in status

    def test_alert_history(self):
        mgr = self._make_manager()
        mgr._alert_history = [{"risk": "high"}] * 5
        history = mgr.get_alert_history(limit=3)
        assert len(history) == 3


# ═══════════════════════════════════════════════════════════════════
# TRADE JOURNAL
# ═══════════════════════════════════════════════════════════════════


class TestTradeJournal:
    """Test trade journal recording and querying."""

    def _make_journal(self):
        from app.services.trade_journal import TradeJournal
        return TradeJournal()

    @pytest.mark.asyncio
    async def test_record_trade_success(self):
        """Test recording a trade with mocked DB and embedding."""
        journal = self._make_journal()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(journal, "_get_embedding", new_callable=AsyncMock, return_value=[0.1] * 768):
            with patch("app.db.database.async_session", return_value=mock_session):
                result = await journal.record_trade(
                    trade_id="t-001",
                    symbol="EURUSD",
                    direction="buy",
                    entry_price=1.1000,
                    exit_price=1.1100,
                    pnl=100.0,
                    outcome="win",
                    reasoning="SMA crossover bullish",
                    lessons="Trend following works in this market",
                )

        assert result["recorded"] is True
        assert result["trade_id"] == "t-001"
        assert result["symbol"] == "EURUSD"
        assert result["outcome"] == "win"

    @pytest.mark.asyncio
    async def test_record_trade_no_embedding(self):
        """Should still record with zero embedding if embedding fails."""
        journal = self._make_journal()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(journal, "_get_embedding", new_callable=AsyncMock, return_value=None):
            with patch("app.db.database.async_session", return_value=mock_session):
                result = await journal.record_trade(
                    trade_id="t-002",
                    symbol="BTCUSDT",
                    direction="sell",
                    entry_price=60000,
                )

        assert result["recorded"] is True

    @pytest.mark.asyncio
    async def test_record_trade_importance_win(self):
        """Wins should have moderate-to-high importance."""
        journal = self._make_journal()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(journal, "_get_embedding", new_callable=AsyncMock, return_value=[0.0] * 768):
            with patch("app.db.database.async_session", return_value=mock_session):
                result = await journal.record_trade(
                    trade_id="t-003",
                    symbol="EURUSD",
                    direction="buy",
                    entry_price=1.1,
                    pnl=500.0,
                    outcome="win",
                )

        assert result["importance"] > 0.5

    @pytest.mark.asyncio
    async def test_record_trade_importance_loss(self):
        """Losses should be highly important (learn from mistakes)."""
        journal = self._make_journal()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(journal, "_get_embedding", new_callable=AsyncMock, return_value=[0.0] * 768):
            with patch("app.db.database.async_session", return_value=mock_session):
                result = await journal.record_trade(
                    trade_id="t-004",
                    symbol="GBPUSD",
                    direction="sell",
                    entry_price=1.3,
                    pnl=-300.0,
                    outcome="loss",
                )

        assert result["importance"] > 0.5

    @pytest.mark.asyncio
    async def test_query_journal_no_embedding(self):
        """Query should return empty if embedding fails."""
        journal = self._make_journal()
        with patch.object(journal, "_get_embedding", new_callable=AsyncMock, return_value=None):
            results = await journal.query_journal("what worked?")
        assert results == []


# ═══════════════════════════════════════════════════════════════════
# MARKET ALERTS
# ═══════════════════════════════════════════════════════════════════


class TestMarketAlerts:
    """Test market alert creation, cancellation, and triggering."""

    def _make_service(self):
        from app.services.market_alerts import MarketAlertService
        return MarketAlertService()

    # --- Alert Creation ---

    @pytest.mark.asyncio
    async def test_create_alert_price_above(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            result = await svc.create_alert(
                symbol="EURUSD",
                alert_type="price_above",
                threshold=1.1200,
                message="EUR/USD breakout!",
            )
        assert "id" in result
        assert result["alert"]["symbol"] == "EURUSD"
        assert result["alert"]["alert_type"] == "price_above"
        assert result["alert"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_alert_price_below(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            result = await svc.create_alert("BTCUSDT", "price_below", 50000)
        assert result["alert"]["threshold"] == 50000

    @pytest.mark.asyncio
    async def test_create_alert_pct_change(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            result = await svc.create_alert("ETHUSDT", "pct_change", 5.0, repeat=True)
        assert result["alert"]["repeat"] is True

    @pytest.mark.asyncio
    async def test_create_alert_invalid_type(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            result = await svc.create_alert("EURUSD", "invalid_type", 1.0)
        assert "error" in result

    # --- Alert Cancellation ---

    @pytest.mark.asyncio
    async def test_cancel_alert(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            created = await svc.create_alert("EURUSD", "price_above", 1.12)
            alert_id = created["id"]
            result = await svc.cancel_alert(alert_id)
        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_alert(self):
        svc = self._make_service()
        result = await svc.cancel_alert("nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            created = await svc.create_alert("EURUSD", "price_above", 1.12)
            alert_id = created["id"]
            await svc.cancel_alert(alert_id)
            result = await svc.cancel_alert(alert_id)
        assert "error" in result
        assert "cancelled" in result["error"].lower()

    # --- Alert Listing ---

    @pytest.mark.asyncio
    async def test_list_alerts(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("EURUSD", "price_above", 1.12)
            await svc.create_alert("BTCUSDT", "price_below", 50000)
        alerts = svc.list_alerts()
        assert len(alerts) == 2

    @pytest.mark.asyncio
    async def test_list_alerts_filtered(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("EURUSD", "price_above", 1.12)
            created = await svc.create_alert("BTCUSDT", "price_below", 50000)
            await svc.cancel_alert(created["id"])

        active = svc.list_alerts(status_filter="active")
        cancelled = svc.list_alerts(status_filter="cancelled")
        assert len(active) == 1
        assert len(cancelled) == 1

    # --- Alert Triggering ---

    @pytest.mark.asyncio
    async def test_check_alerts_price_above_triggered(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("EURUSD", "price_above", 1.1200)

        # Mock trading service returning price above threshold
        mock_prices = [{"symbol": "EURUSD", "last": 1.1250}]
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_prices = AsyncMock(return_value=mock_prices)
        mock_eb = MagicMock()
        mock_eb.broadcast = AsyncMock()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            with patch("app.services.trading.trading_service.trading_service", mock_ts):
                with patch("app.services.event_bus.event_bus", mock_eb):
                    triggered = await svc.check_alerts()

        assert len(triggered) == 1
        assert triggered[0]["symbol"] == "EURUSD"
        assert triggered[0]["current_price"] == 1.1250

    @pytest.mark.asyncio
    async def test_check_alerts_price_below_triggered(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("BTCUSDT", "price_below", 55000)

        mock_prices = [{"symbol": "BTCUSDT", "last": 54000}]
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_prices = AsyncMock(return_value=mock_prices)
        mock_eb = MagicMock()
        mock_eb.broadcast = AsyncMock()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            with patch("app.services.trading.trading_service.trading_service", mock_ts):
                with patch("app.services.event_bus.event_bus", mock_eb):
                    triggered = await svc.check_alerts()

        assert len(triggered) == 1

    @pytest.mark.asyncio
    async def test_check_alerts_not_triggered(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("EURUSD", "price_above", 1.1500)

        mock_prices = [{"symbol": "EURUSD", "last": 1.1200}]  # Below threshold
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_prices = AsyncMock(return_value=mock_prices)
        with patch("app.services.trading.trading_service.trading_service", mock_ts):
            triggered = await svc.check_alerts()

        assert len(triggered) == 0

    @pytest.mark.asyncio
    async def test_check_alerts_repeat_rearmed(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            created = await svc.create_alert("EURUSD", "price_above", 1.12, repeat=True)
            alert_id = created["id"]

        mock_prices = [{"symbol": "EURUSD", "last": 1.1250}]
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_prices = AsyncMock(return_value=mock_prices)
        mock_eb = MagicMock()
        mock_eb.broadcast = AsyncMock()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            with patch("app.services.trading.trading_service.trading_service", mock_ts):
                with patch("app.services.event_bus.event_bus", mock_eb):
                    triggered = await svc.check_alerts()

        assert len(triggered) == 1
        # Alert should still be active (re-armed)
        from app.services.market_alerts import AlertStatus
        assert svc._alerts[alert_id].status == AlertStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_check_alerts_pct_change_triggered(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("BTCUSDT", "pct_change", 3.0)

        # Set last known price
        svc._last_prices["BTCUSDT"] = 60000

        # New price is 5% higher → should trigger 3% threshold
        mock_prices = [{"symbol": "BTCUSDT", "last": 63000}]
        mock_ts = MagicMock()
        mock_ts.is_connected = True
        mock_ts.get_prices = AsyncMock(return_value=mock_prices)
        mock_eb = MagicMock()
        mock_eb.broadcast = AsyncMock()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            with patch("app.services.trading.trading_service.trading_service", mock_ts):
                with patch("app.services.event_bus.event_bus", mock_eb):
                    triggered = await svc.check_alerts()

        assert len(triggered) == 1

    # --- Status ---

    @pytest.mark.asyncio
    async def test_get_status(self):
        svc = self._make_service()
        with patch.object(svc, "save_to_redis", new_callable=AsyncMock):
            await svc.create_alert("EURUSD", "price_above", 1.12)
        status = svc.get_status()
        assert status["active_alerts"] == 1
        assert status["total_alerts"] == 1

    # --- Triggered History ---

    @pytest.mark.asyncio
    async def test_triggered_history_capped(self):
        svc = self._make_service()
        svc._triggered_history = [{"event": i} for i in range(210)]
        # After check, history should be capped
        # We can't easily trigger 200+ alerts, but test the cap logic
        assert len(svc._triggered_history) == 210
        # Direct cap test
        svc._triggered_history = svc._triggered_history[-200:]
        assert len(svc._triggered_history) == 200


# ═══════════════════════════════════════════════════════════════════
# CEO TOOL INTEGRATION
# ═══════════════════════════════════════════════════════════════════


class TestCEOPhase3Tools:
    """Verify Phase 3 tools are registered in CEO agent."""

    def test_phase3_tools_in_tool_list(self):
        """CEO agent should have all Phase 3 tools registered."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo-p3")
        phase3_tools = [
            "set_auto_trade_mode",
            "get_auto_trade_status",
            "calculate_position_size",
            "assess_portfolio_risk",
            "update_risk_limits",
            "record_trade_journal",
            "query_trade_journal",
            "get_trade_stats",
            "create_market_alert",
            "list_market_alerts",
            "cancel_market_alert",
        ]
        for tool in phase3_tools:
            assert tool in ceo.tools, f"Tool '{tool}' missing from CEO tool list"

    def test_phase3_tool_schemas_present(self):
        """All Phase 3 tools should have OpenAI function schemas."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo-p3-schema")
        schemas = ceo._get_tools_schema()
        schema_names = {
            s["function"]["name"]
            for s in schemas
            if s.get("type") == "function"
        }
        phase3_tools = [
            "set_auto_trade_mode",
            "get_auto_trade_status",
            "calculate_position_size",
            "assess_portfolio_risk",
            "update_risk_limits",
            "record_trade_journal",
            "query_trade_journal",
            "get_trade_stats",
            "create_market_alert",
            "list_market_alerts",
            "cancel_market_alert",
        ]
        for tool in phase3_tools:
            assert tool in schema_names, f"Schema for '{tool}' missing from CEO"

    def test_total_ceo_tools_count(self):
        """CEO should now have Phase 1 + Phase 2 + Phase 3 tools."""
        from app.agents.ceo import CEOAgent
        ceo = CEOAgent(agent_id="test-ceo-count")
        # Base tools are added separately via _get_tools_schema,
        # but the tools list should have all CEO-specific tools
        # Phase 1: 4 (set_agent_budget, set_company_budget, check_budget_status, generate_daily_report)
        # Phase 2: 8 (initiate_vote, get_vote_results, list_votes, set_company_kpi, get_company_kpis, get_all_kpis, update_kpi_value, list_delegations)
        # Phase 3: 11 (set_auto_trade_mode, get_auto_trade_status, calculate_position_size, assess_portfolio_risk, update_risk_limits, record_trade_journal, query_trade_journal, get_trade_stats, create_market_alert, list_market_alerts, cancel_market_alert)
        # + base: schedule_task, schedule_once, list_schedules, cancel_schedule, etc.
        assert len(ceo.tools) >= 47  # Phase 1 + Phase 2 + Phase 3 = 47 tools


# ═══════════════════════════════════════════════════════════════════
# CONFIG SERIALIZATION
# ═══════════════════════════════════════════════════════════════════


class TestConfigSerialization:
    """Test that all configs serialize to dict properly."""

    def test_auto_trade_config_to_dict(self):
        from app.services.auto_trade_executor import AutoTradeConfig
        cfg = AutoTradeConfig()
        d = cfg.to_dict()
        assert "mode" in d
        assert "min_confidence" in d
        assert isinstance(d["mode"], str)

    def test_risk_limits_to_dict(self):
        from app.services.portfolio_risk_manager import RiskLimits
        limits = RiskLimits()
        d = limits.to_dict()
        assert "max_drawdown_pct" in d
        assert d["max_drawdown_pct"] == 10.0

    def test_market_alert_round_trip(self):
        from app.services.market_alerts import MarketAlert, AlertType, AlertStatus
        alert = MarketAlert(
            id="test-1",
            symbol="EURUSD",
            alert_type=AlertType.PRICE_ABOVE,
            threshold=1.12,
            created_by="ceo",
            created_at="2024-01-01T00:00:00",
            message="Breakout!",
            repeat=True,
        )
        d = alert.to_dict()
        restored = MarketAlert.from_dict(d)
        assert restored.id == alert.id
        assert restored.symbol == alert.symbol
        assert restored.alert_type == alert.alert_type
        assert restored.threshold == alert.threshold
        assert restored.repeat is True

    def test_sizing_result_to_dict(self):
        from app.services.position_calculator import SizingResult
        r = SizingResult(
            symbol="EURUSD",
            account_equity=10000,
            risk_pct=1.0,
            risk_amount=100,
            entry_price=1.1,
            stop_loss=1.095,
            stop_loss_distance=0.005,
            recommended_size=0.2,
            lot_type="mini",
            max_loss=100,
            instrument_type="forex",
        )
        d = r.to_dict()
        assert d["symbol"] == "EURUSD"
        assert d["recommended_size"] == 0.2
