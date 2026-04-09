"""API authentication dependencies."""

import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import get_settings

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    api_key: Annotated[str | None, Depends(_api_key_header)] = None,
) -> str | None:
    """Verify the API key if one is configured.

    When ``settings.api_key`` is empty the check is skipped (dev mode).
    """
    settings = get_settings()
    if not settings.api_key:
        return None  # no key configured — open access (dev mode)

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    if not hmac.compare_digest(api_key, settings.api_key):
        logger.warning("Invalid API key attempt")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key


async def verify_webhook_secret(request: Request) -> None:
    """Verify incoming webhook HMAC signature.

    Expects header ``X-Webhook-Signature: sha256=<hex>``.
    When ``settings.webhook_secret`` is empty the check is skipped.
    """
    settings = get_settings()
    if not settings.webhook_secret:
        return  # no secret configured — skip validation

    signature = request.headers.get("X-Webhook-Signature")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Webhook-Signature header",
        )

    body = await request.body()
    expected = "sha256=" + hmac.new(
        settings.webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.warning("Invalid webhook signature")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )
