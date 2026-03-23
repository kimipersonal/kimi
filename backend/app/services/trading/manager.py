"""Trading platform manager — instantiates connectors and runs evaluations."""

import logging
from dataclasses import asdict

from app.config import get_settings
from app.services.trading.base import BaseTradingConnector

logger = logging.getLogger(__name__)


def get_configured_connectors() -> list[BaseTradingConnector]:
    """Return connector instances for all platforms that have credentials configured."""
    settings = get_settings()
    connectors: list[BaseTradingConnector] = []

    # MetaAPI (MT5)
    if settings.metaapi_token and settings.metaapi_account_id:
        from app.services.trading.metaapi_connector import MetaAPIConnector

        connectors.append(
            MetaAPIConnector(
                token=settings.metaapi_token,
                account_id=settings.metaapi_account_id,
            )
        )

    # Binance Testnet
    if settings.binance_testnet_api_key and settings.binance_testnet_api_secret:
        from app.services.trading.binance_connector import BinanceTestnetConnector

        connectors.append(
            BinanceTestnetConnector(
                api_key=settings.binance_testnet_api_key,
                api_secret=settings.binance_testnet_api_secret,
            )
        )

    # Capital.com
    if (
        settings.capital_api_key
        and settings.capital_email
        and settings.capital_password
    ):
        from app.services.trading.capital_connector import CapitalComConnector

        connectors.append(
            CapitalComConnector(
                api_key=settings.capital_api_key,
                email=settings.capital_email,
                password=settings.capital_password,
            )
        )

    # OANDA
    if settings.oanda_api_key and settings.oanda_account_id:
        from app.services.trading.oanda_connector import OandaConnector

        connectors.append(
            OandaConnector(
                api_key=settings.oanda_api_key,
                account_id=settings.oanda_account_id,
                api_url=settings.oanda_api_url,
            )
        )

    return connectors


async def evaluate_all_platforms() -> list[dict]:
    """Evaluate all configured trading platforms and return results sorted by score."""
    connectors = get_configured_connectors()
    if not connectors:
        return [{"error": "No trading platforms configured. Set API keys in .env"}]

    results = []
    for connector in connectors:
        logger.info(f"Evaluating {connector.platform_name}...")
        try:
            evaluation = await connector.evaluate()
            results.append(asdict(evaluation))
        except Exception as e:
            results.append(
                {
                    "platform": connector.platform_name,
                    "is_connected": False,
                    "error": str(e)[:200],
                    "score": 0,
                }
            )
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass

    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


async def get_best_connector() -> BaseTradingConnector | None:
    """Return the connector with the highest evaluation score."""
    connectors = get_configured_connectors()
    if not connectors:
        return None

    best_score: float = -1
    best_connector = None

    for connector in connectors:
        try:
            evaluation = await connector.evaluate()
            if evaluation.score > best_score:
                best_score = evaluation.score
                if best_connector:
                    await best_connector.disconnect()
                best_connector = connector
            else:
                await connector.disconnect()
        except Exception:
            try:
                await connector.disconnect()
            except Exception:
                pass

    return best_connector
