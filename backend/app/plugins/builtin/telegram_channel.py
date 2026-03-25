"""Telegram Channel Plugin — wraps existing Telegram bot as a plugin."""

from app.plugins.base import ChannelPlugin


class TelegramChannel(ChannelPlugin):
    """Telegram channel — existing bot wrapped as a plugin."""

    @property
    def name(self) -> str:
        return "telegram"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Telegram bot — 18 commands, owner notifications, agent chat"

    async def start(self):
        from app.services.telegram_bot import telegram_bot
        await telegram_bot.start()

    async def stop(self):
        from app.services.telegram_bot import telegram_bot
        await telegram_bot.stop()

    async def send_message(self, recipient: str, text: str, **kwargs) -> bool:
        from app.services.telegram_bot import telegram_bot
        try:
            if telegram_bot._running and telegram_bot._app:
                await telegram_bot._app.bot.send_message(
                    chat_id=int(recipient), text=text
                )
                return True
        except Exception:
            pass
        return False

    async def notify(self, event: str, data: dict, agent_id: str | None = None):
        from app.services.telegram_bot import telegram_bot
        await telegram_bot.notify(event, data, agent_id)


def register(registry):
    """Register the Telegram channel plugin."""
    registry.register(TelegramChannel())
