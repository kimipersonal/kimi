"""Cloudflare Tunnel manager — runs a free quick tunnel and tracks the URL."""

import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_URL_FILE = Path(__file__).resolve().parents[3] / ".cloudflare_url"


class CloudflareTunnel:
    """Manages a ``cloudflared tunnel --url`` subprocess."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._url: str | None = None
        self._target_port: int = 8080

    @property
    def url(self) -> str | None:
        """Current tunnel public URL (https://…trycloudflare.com)."""
        if self._url:
            return self._url
        # fallback: read from file (survives restarts)
        if _URL_FILE.exists():
            stored = _URL_FILE.read_text().strip()
            if stored:
                return stored
        return None

    async def start(self, port: int = 8080) -> str | None:
        """Start cloudflared quick tunnel pointing at *port*. Returns the URL."""
        self._target_port = port

        # Kill any leftover cloudflared processes
        await self._kill_existing()

        try:
            self._proc = await asyncio.create_subprocess_exec(
                "cloudflared", "tunnel", "--url", f"http://localhost:{port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("cloudflared not installed — tunnel disabled")
            return None

        # Parse stderr for the URL (cloudflared logs there)
        url = await self._wait_for_url(timeout=30)
        if url:
            self._url = url
            _URL_FILE.write_text(url)
            logger.info(f"Cloudflare tunnel active: {url}")
        else:
            logger.error("Failed to obtain Cloudflare tunnel URL in time")
        return url

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
            self._proc = None
        self._url = None
        if _URL_FILE.exists():
            _URL_FILE.unlink()
        logger.info("Cloudflare tunnel stopped")

    async def restart(self) -> str | None:
        """Restart the tunnel (gets a new URL on the free tier)."""
        await self.stop()
        return await self.start(self._target_port)

    async def _wait_for_url(self, timeout: int = 30) -> str | None:
        """Read cloudflared stderr until we find the tunnel URL."""
        if not self._proc or not self._proc.stderr:
            return None

        pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            try:
                line_bytes = await asyncio.wait_for(
                    self._proc.stderr.readline(), timeout=2
                )
            except asyncio.TimeoutError:
                continue

            if not line_bytes:
                if self._proc.returncode is not None:
                    break
                continue

            line = line_bytes.decode(errors="replace")
            logger.debug(f"cloudflared: {line.rstrip()}")
            match = pattern.search(line)
            if match:
                return match.group(0)

        return None

    @staticmethod
    async def _kill_existing() -> None:
        """Kill any lingering cloudflared tunnel processes."""
        proc = await asyncio.create_subprocess_exec(
            "pkill", "-f", "cloudflared tunnel",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        await asyncio.sleep(1)


# Global singleton
cloudflare_tunnel = CloudflareTunnel()
