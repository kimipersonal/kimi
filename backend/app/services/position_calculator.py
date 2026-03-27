"""Position Size Calculator — risk-based position sizing.

Calculates optimal position size based on account equity, stop-loss
distance, and configurable risk percentage. Supports forex pairs
(pip-based) and crypto/stock instruments (percentage-based).
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Standard pip values for common pair types
_PIP_SIZE = {
    "JPY": 0.01,      # JPY pairs: 1 pip = 0.01
    "DEFAULT": 0.0001, # Most pairs: 1 pip = 0.0001
}

# Standard lot sizes
_STANDARD_LOT = 100_000  # Forex standard lot
_MINI_LOT = 10_000
_MICRO_LOT = 1_000


@dataclass
class SizingResult:
    """Result of position size calculation."""
    symbol: str
    account_equity: float
    risk_pct: float
    risk_amount: float
    entry_price: float
    stop_loss: float
    stop_loss_distance: float
    recommended_size: float
    lot_type: str        # "standard", "mini", "micro", "units"
    max_loss: float      # Worst-case loss at stop-loss
    instrument_type: str  # "forex", "crypto", "stock"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "account_equity": round(self.account_equity, 2),
            "risk_pct": self.risk_pct,
            "risk_amount": round(self.risk_amount, 2),
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "stop_loss_distance": round(self.stop_loss_distance, 5),
            "recommended_size": round(self.recommended_size, 4),
            "lot_type": self.lot_type,
            "max_loss": round(self.max_loss, 2),
            "instrument_type": self.instrument_type,
        }


def _classify_instrument(symbol: str) -> str:
    """Classify a symbol as forex, crypto, or stock."""
    clean = symbol.replace("/", "").replace("_", "").replace("-", "").upper()
    crypto_tokens = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
                     "DOT", "LINK", "MATIC", "UNI", "ATOM", "LTC", "USDT",
                     "USDC", "SHIB", "ARB", "OP", "APT"}
    if any(clean.startswith(t) or clean.endswith(t) for t in crypto_tokens):
        return "crypto"

    forex_currencies = {"EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}
    # Forex pairs are typically 6 chars (EURUSD) or have / separator
    if len(clean) == 6 and clean[:3] in forex_currencies and clean[3:] in forex_currencies:
        return "forex"

    return "stock"


def _get_pip_size(symbol: str) -> float:
    """Get pip size for a forex pair."""
    clean = symbol.replace("/", "").replace("_", "").upper()
    return _PIP_SIZE["JPY"] if "JPY" in clean else _PIP_SIZE["DEFAULT"]


class PositionCalculator:
    """Calculate position sizes based on risk management rules."""

    async def calculate_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        risk_pct: float = 1.0,
        account_equity: float | None = None,
    ) -> dict:
        """Calculate recommended position size.

        Args:
            symbol: Trading instrument symbol
            entry_price: Expected entry price
            stop_loss: Stop-loss price level
            risk_pct: Risk per trade as % of equity (default 1%)
            account_equity: Account equity (if None, fetched from trading service)

        Returns:
            dict with sizing recommendation
        """
        if entry_price <= 0:
            return {"error": "Entry price must be positive"}
        if stop_loss <= 0:
            return {"error": "Stop-loss price must be positive"}
        if risk_pct <= 0 or risk_pct > 10:
            return {"error": "Risk percentage must be between 0 and 10"}

        # Fetch equity from trading service if not provided
        if account_equity is None:
            account_equity = await self._get_account_equity(symbol)
            if account_equity is None:
                return {"error": "Could not determine account equity"}

        if account_equity <= 0:
            return {"error": "Account equity must be positive"}

        instrument_type = _classify_instrument(symbol)
        sl_distance = abs(entry_price - stop_loss)

        if sl_distance == 0:
            return {"error": "Stop-loss cannot equal entry price"}

        risk_amount = account_equity * (risk_pct / 100)

        if instrument_type == "forex":
            result = self._calc_forex(symbol, entry_price, stop_loss, sl_distance,
                                      risk_amount, risk_pct, account_equity)
        elif instrument_type == "crypto":
            result = self._calc_crypto(symbol, entry_price, stop_loss, sl_distance,
                                       risk_amount, risk_pct, account_equity)
        else:
            result = self._calc_stock(symbol, entry_price, stop_loss, sl_distance,
                                      risk_amount, risk_pct, account_equity)

        return result.to_dict()

    def _calc_forex(
        self, symbol: str, entry: float, sl: float, sl_dist: float,
        risk_amount: float, risk_pct: float, equity: float,
    ) -> SizingResult:
        """Calculate forex position size in lots."""
        pip_size = _get_pip_size(symbol)
        sl_pips = sl_dist / pip_size

        # Position size in units: risk_amount / (pips * pip_value_per_unit)
        # For most pairs, pip_value per unit ≈ pip_size
        # Simplified: size = risk_amount / (sl_distance)
        size_units = risk_amount / sl_dist if sl_dist > 0 else 0

        # Convert to lots
        size_lots = size_units / _STANDARD_LOT

        # Determine lot type
        if size_lots >= 1.0:
            lot_type = "standard"
            display_size = round(size_lots, 2)
        elif size_lots >= 0.1:
            lot_type = "mini"
            display_size = round(size_lots, 2)
        else:
            lot_type = "micro"
            display_size = round(size_lots, 4)

        max_loss = sl_pips * pip_size * size_units

        return SizingResult(
            symbol=symbol,
            account_equity=equity,
            risk_pct=risk_pct,
            risk_amount=risk_amount,
            entry_price=entry,
            stop_loss=sl,
            stop_loss_distance=sl_dist,
            recommended_size=display_size,
            lot_type=lot_type,
            max_loss=risk_amount,  # By definition, max loss = risk amount
            instrument_type="forex",
        )

    def _calc_crypto(
        self, symbol: str, entry: float, sl: float, sl_dist: float,
        risk_amount: float, risk_pct: float, equity: float,
    ) -> SizingResult:
        """Calculate crypto position size in base units."""
        # Risk-based: size = risk_amount / sl_distance
        size = risk_amount / sl_dist if sl_dist > 0 else 0

        return SizingResult(
            symbol=symbol,
            account_equity=equity,
            risk_pct=risk_pct,
            risk_amount=risk_amount,
            entry_price=entry,
            stop_loss=sl,
            stop_loss_distance=sl_dist,
            recommended_size=round(size, 6),
            lot_type="units",
            max_loss=risk_amount,
            instrument_type="crypto",
        )

    def _calc_stock(
        self, symbol: str, entry: float, sl: float, sl_dist: float,
        risk_amount: float, risk_pct: float, equity: float,
    ) -> SizingResult:
        """Calculate stock position size in shares."""
        shares = risk_amount / sl_dist if sl_dist > 0 else 0

        return SizingResult(
            symbol=symbol,
            account_equity=equity,
            risk_pct=risk_pct,
            risk_amount=risk_amount,
            entry_price=entry,
            stop_loss=sl,
            stop_loss_distance=sl_dist,
            recommended_size=round(shares, 2),
            lot_type="shares",
            max_loss=risk_amount,
            instrument_type="stock",
        )

    async def _get_account_equity(self, symbol: str) -> float | None:
        """Fetch account equity from the trading service."""
        try:
            from app.services.trading.trading_service import trading_service
            if not trading_service.is_connected:
                return None
            accounts = await trading_service.get_all_accounts()
            if not accounts:
                return None
            # Sum equity across all accounts
            total_equity = sum(
                a.get("equity", a.get("balance", 0))
                for a in accounts
                if "error" not in a
            )
            return total_equity if total_equity > 0 else None
        except Exception as e:
            logger.error(f"Failed to get account equity: {e}")
            return None

    def quick_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss: float,
        risk_pct: float = 1.0,
    ) -> dict:
        """Synchronous quick calculation without fetching account data."""
        if equity <= 0 or entry_price <= 0 or stop_loss <= 0:
            return {"error": "All values must be positive"}

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance == 0:
            return {"error": "Stop-loss cannot equal entry price"}

        risk_amount = equity * (risk_pct / 100)
        size = risk_amount / sl_distance

        return {
            "equity": round(equity, 2),
            "risk_pct": risk_pct,
            "risk_amount": round(risk_amount, 2),
            "stop_loss_distance": round(sl_distance, 5),
            "recommended_size": round(size, 4),
            "max_loss": round(risk_amount, 2),
        }


# Global singleton
position_calculator = PositionCalculator()
