"""Minimal status notifier for Telegram, Discord, and generic JSON webhooks."""

from __future__ import annotations

import httpx

from .config import BuyConfig


def send_alerts(config: BuyConfig, text: str, timeout: float = 10.0) -> None:
    """Best-effort status broadcast; individual failures are swallowed and logged."""
    for target in config.alerts:
        try:
            if target.type == "telegram":
                httpx.post(target.url, data={"chat_id": target.chat_id, "text": text}, timeout=timeout)
            elif target.type == "discord":
                httpx.post(target.url, json={"content": text}, timeout=timeout)
            else:
                httpx.post(target.url, json={"text": text}, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger("buybot").warning("Alert to %s failed: %s", target.url, exc)
