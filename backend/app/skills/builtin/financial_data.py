"""Financial Data skill — market data, economic calendar, and sentiment.

Provides financial information via free APIs (Alpha Vantage, Exchange Rate API).
Agents can query stock/forex quotes, economic calendar events, and market sentiment.
"""

import json
import logging
from datetime import datetime, timezone

from app.skills.base import Skill, SkillCategory, SkillMetadata

logger = logging.getLogger(__name__)


class FinancialDataSkill(Skill):
    """Financial market data and economic information."""

    @property
    def name(self) -> str:
        return "financial_data"

    @property
    def display_name(self) -> str:
        return "Financial Data"

    @property
    def description(self) -> str:
        return "Access financial market data including forex/stock quotes, economic calendar events, and market sentiment indicators."

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def category(self) -> SkillCategory:
        return SkillCategory.FINANCE

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            author="system",
            tags=["finance", "market", "forex", "stocks", "economic"],
            requires_config=["ALPHA_VANTAGE_API_KEY"],
            icon="📈",
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_stock_quote",
                    "description": "Get the latest stock or forex quote for a symbol (e.g., AAPL, MSFT, EURUSD).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "Stock or forex symbol (e.g., AAPL, EURUSD)"},
                        },
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_market_overview",
                    "description": "Get a broad market overview with major indices, top gainers, and losers.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_economic_calendar",
                    "description": "Get upcoming economic calendar events (interest rates, GDP, employment data, etc.).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "country": {
                                "type": "string",
                                "description": "Country code filter (e.g., US, EU, GB). Default: all.",
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_market_sentiment",
                    "description": "Get market sentiment indicators including fear/greed index and volatility.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        match tool_name:
            case "get_stock_quote":
                return await self._get_stock_quote(arguments.get("symbol", ""))
            case "get_market_overview":
                return await self._get_market_overview()
            case "get_economic_calendar":
                return await self._get_economic_calendar(arguments.get("country"))
            case "get_market_sentiment":
                return await self._get_market_sentiment()
            case _:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})

    async def _get_stock_quote(self, symbol: str) -> str:
        """Fetch stock/forex quote via Alpha Vantage."""
        import os
        api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "demo")
        symbol = symbol.upper().strip()

        if not symbol:
            return json.dumps({"error": "Symbol is required"})

        try:
            import httpx
            # Determine if forex pair
            is_forex = len(symbol) == 6 and symbol.isalpha()

            if is_forex:
                from_currency = symbol[:3]
                to_currency = symbol[3:]
                url = (
                    f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE"
                    f"&from_currency={from_currency}&to_currency={to_currency}&apikey={api_key}"
                )
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                    data = resp.json()

                rate_data = data.get("Realtime Currency Exchange Rate", {})
                if not rate_data:
                    return json.dumps({"symbol": symbol, "error": "No data available", "raw": data})

                return json.dumps({
                    "symbol": symbol,
                    "type": "forex",
                    "rate": rate_data.get("5. Exchange Rate"),
                    "bid": rate_data.get("8. Bid Price"),
                    "ask": rate_data.get("9. Ask Price"),
                    "last_updated": rate_data.get("6. Last Refreshed"),
                })
            else:
                url = (
                    f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE"
                    f"&symbol={symbol}&apikey={api_key}"
                )
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(url)
                    data = resp.json()

                quote = data.get("Global Quote", {})
                if not quote:
                    return json.dumps({"symbol": symbol, "error": "No data available", "raw": data})

                return json.dumps({
                    "symbol": symbol,
                    "type": "stock",
                    "price": quote.get("05. price"),
                    "open": quote.get("02. open"),
                    "high": quote.get("03. high"),
                    "low": quote.get("04. low"),
                    "volume": quote.get("06. volume"),
                    "change": quote.get("09. change"),
                    "change_percent": quote.get("10. change percent"),
                    "previous_close": quote.get("08. previous close"),
                })

        except ImportError:
            return json.dumps({"error": "httpx not installed. Run: pip install httpx"})
        except Exception as e:
            logger.error(f"Stock quote failed for {symbol}: {e}")
            return json.dumps({"error": f"Failed to fetch quote: {str(e)[:200]}"})

    async def _get_market_overview(self) -> str:
        """Get market overview — top gainers/losers and major indices."""
        import os
        api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "demo")

        try:
            import httpx
            url = f"https://www.alphavantage.co/query?function=TOP_GAINERS_LOSERS&apikey={api_key}"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                data = resp.json()

            top_gainers = data.get("top_gainers", [])[:5]
            top_losers = data.get("top_losers", [])[:5]
            most_active = data.get("most_actively_traded", [])[:5]

            return json.dumps({
                "last_updated": data.get("last_updated", datetime.now(timezone.utc).isoformat()),
                "top_gainers": [{"ticker": g.get("ticker"), "price": g.get("price"), "change_percent": g.get("change_percentage")} for g in top_gainers],
                "top_losers": [{"ticker": l.get("ticker"), "price": l.get("price"), "change_percent": l.get("change_percentage")} for l in top_losers],
                "most_active": [{"ticker": a.get("ticker"), "price": a.get("price"), "volume": a.get("volume")} for a in most_active],
            })
        except ImportError:
            return json.dumps({"error": "httpx not installed"})
        except Exception as e:
            logger.error(f"Market overview failed: {e}")
            return json.dumps({"error": str(e)[:200]})

    async def _get_economic_calendar(self, country: str | None = None) -> str:
        """Return a summary of key economic events.

        Since free economic calendar APIs are limited, we provide
        well-known regular events as a reference.
        """
        events = [
            {"event": "US Non-Farm Payrolls", "frequency": "Monthly (1st Friday)", "country": "US", "impact": "high"},
            {"event": "US CPI (Inflation)", "frequency": "Monthly (mid-month)", "country": "US", "impact": "high"},
            {"event": "FOMC Interest Rate Decision", "frequency": "8x per year", "country": "US", "impact": "critical"},
            {"event": "US GDP", "frequency": "Quarterly", "country": "US", "impact": "high"},
            {"event": "US Unemployment Claims", "frequency": "Weekly (Thursday)", "country": "US", "impact": "medium"},
            {"event": "ECB Interest Rate Decision", "frequency": "6x per year", "country": "EU", "impact": "critical"},
            {"event": "Eurozone CPI", "frequency": "Monthly", "country": "EU", "impact": "high"},
            {"event": "BOE Interest Rate Decision", "frequency": "8x per year", "country": "GB", "impact": "critical"},
            {"event": "UK CPI", "frequency": "Monthly", "country": "GB", "impact": "high"},
            {"event": "BOJ Interest Rate Decision", "frequency": "8x per year", "country": "JP", "impact": "critical"},
            {"event": "China GDP", "frequency": "Quarterly", "country": "CN", "impact": "high"},
            {"event": "US Retail Sales", "frequency": "Monthly", "country": "US", "impact": "medium"},
            {"event": "US ISM Manufacturing PMI", "frequency": "Monthly (1st workday)", "country": "US", "impact": "medium"},
        ]

        if country:
            events = [e for e in events if e["country"].upper() == country.upper()]

        return json.dumps({
            "note": "Key recurring economic events. Use web_search for exact upcoming dates.",
            "events": events,
            "count": len(events),
        })

    async def _get_market_sentiment(self) -> str:
        """Get market sentiment indicators."""
        try:
            import httpx
            # CNN Fear & Greed Index API (free, no key)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata")
                data = resp.json()

            fg = data.get("fear_and_greed", {})
            return json.dumps({
                "fear_and_greed": {
                    "score": fg.get("score"),
                    "rating": fg.get("rating"),
                    "previous_close": fg.get("previous_close"),
                    "previous_1_week": fg.get("previous_1_week"),
                    "previous_1_month": fg.get("previous_1_month"),
                    "previous_1_year": fg.get("previous_1_year"),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            # Fallback: provide general guidance
            return json.dumps({
                "note": "Live sentiment data unavailable. Use web_search for current Fear & Greed index.",
                "indicators": [
                    "CNN Fear & Greed Index — measures market sentiment 0-100",
                    "VIX (CBOE Volatility Index) — VIX > 30 = fear, < 15 = complacency",
                    "Put/Call Ratio — > 1.0 = bearish, < 0.7 = bullish",
                ],
                "error": str(e)[:200],
            })


def register(registry):
    """Register this skill with the skill registry."""
    registry.register(FinancialDataSkill())
