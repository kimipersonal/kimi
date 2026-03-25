"""Browser Service — Playwright-based web browsing for agent research.

Provides controlled web browsing with:
- SSRF prevention (blocks internal IPs)
- Text extraction with size limits
- Screenshot capabilities
- Shared browser instance with max concurrent pages
"""

import asyncio
import ipaddress
import logging
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page, Playwright

logger = logging.getLogger(__name__)

MAX_CONCURRENT_PAGES = 5
PAGE_TIMEOUT_MS = 15_000
MAX_TEXT_LENGTH = 10_000
SCREENSHOT_WIDTH = 1280
SCREENSHOT_HEIGHT = 720

# Block internal/private IP ranges (SSRF prevention)
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}


def _is_internal_url(url: str) -> bool:
    """Check if a URL points to an internal/private address."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""

        if host in _BLOCKED_HOSTS:
            return True

        # Check for private IP ranges
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        except ValueError:
            pass

        # Block cloud metadata endpoints
        if host.endswith(".internal") or host.startswith("169.254."):
            return True

        # Only allow http/https schemes
        if parsed.scheme not in ("http", "https"):
            return True

        return False
    except Exception:
        return True  # Block if we can't parse


class BrowserService:
    """Managed browser instance for agent web browsing."""

    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        """Lazy-init browser on first use."""
        if self._browser and self._browser.is_connected():
            return

        async with self._lock:
            if self._browser and self._browser.is_connected():
                return

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                ],
            )
            logger.info("Browser instance started")

    async def browse_url(self, url: str) -> dict:
        """Navigate to URL and extract page text content.

        Returns dict with title, text, url, and status.
        """
        if _is_internal_url(url):
            return {"error": "Access to internal/private URLs is not allowed", "url": url}

        await self._ensure_browser()
        assert self._browser is not None

        async with self._semaphore:
            page: Page | None = None
            try:
                page = await self._browser.new_page(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                    java_script_enabled=True,
                )
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                response = await page.goto(url, wait_until="domcontentloaded")
                status = response.status if response else 0

                title = await page.title()

                # Extract visible text content
                text = await page.evaluate("""
                    () => {
                        // Remove script and style elements
                        const scripts = document.querySelectorAll('script, style, noscript');
                        scripts.forEach(s => s.remove());
                        return document.body ? document.body.innerText : '';
                    }
                """)

                # Truncate text
                text = text[:MAX_TEXT_LENGTH] if text else ""
                # Clean up excessive whitespace
                text = re.sub(r'\n{3,}', '\n\n', text)
                text = re.sub(r'[ \t]+', ' ', text)

                return {
                    "title": title or "",
                    "text": text.strip(),
                    "url": page.url,
                    "status": status,
                }

            except Exception as e:
                logger.warning(f"Browse failed for {url}: {e}")
                return {"error": str(e)[:500], "url": url}
            finally:
                if page:
                    await page.close()

    async def screenshot_url(self, url: str) -> dict:
        """Take a screenshot of a URL.

        Returns dict with screenshot path (base64-encoded PNG), title, url.
        """
        if _is_internal_url(url):
            return {"error": "Access to internal/private URLs is not allowed", "url": url}

        await self._ensure_browser()
        assert self._browser is not None

        async with self._semaphore:
            page: Page | None = None
            try:
                page = await self._browser.new_page(
                    viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT},
                )
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                await page.goto(url, wait_until="domcontentloaded")
                title = await page.title()

                import base64
                screenshot_bytes = await page.screenshot(full_page=False, type="png")
                screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")

                return {
                    "title": title or "",
                    "screenshot_base64": screenshot_b64,
                    "url": page.url,
                    "width": SCREENSHOT_WIDTH,
                    "height": SCREENSHOT_HEIGHT,
                }

            except Exception as e:
                logger.warning(f"Screenshot failed for {url}: {e}")
                return {"error": str(e)[:500], "url": url}
            finally:
                if page:
                    await page.close()

    async def shutdown(self):
        """Close browser and playwright."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser service shut down")

    @property
    def is_available(self) -> bool:
        """Check if browser can be launched."""
        try:
            import shutil
            return shutil.which("chromium") is not None or shutil.which("chromium-browser") is not None
        except Exception:
            return False


# Singleton
browser_service = BrowserService()
