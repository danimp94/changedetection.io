"""Configuration loading and validation for the buybot."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator

from .signals import DEFAULT_BUY_MARKERS, DEFAULT_OUT_OF_STOCK_MARKERS


class ShippingAddress(BaseModel):
    """Shipping / contact details filled into the checkout form."""

    full_name: str
    email: str
    phone: str = ""
    address_line1: str
    address_line2: str = ""
    city: str
    postal_code: str
    region: str = ""
    country: str


class Payment(BaseModel):
    """Optional card details used only when ``safety_mode`` is disabled."""

    card_number: SecretStr | None = None
    card_holder: str | None = None
    expiry_month: str | None = None
    expiry_year: str | None = None
    cvv: SecretStr | None = None


class AlertTarget(BaseModel):
    """A webhook endpoint to POST status updates to."""

    type: Literal["telegram", "discord", "generic"] = "generic"
    url: str
    chat_id: str | None = None


class BuyConfig(BaseModel):
    """Full configuration for a single product watch + checkout."""

    product_url: str
    sku: str | None = None
    size: str | None = None
    # Equivalent sizes to try in order (EU/US sneaker maps, apparel variants).
    # Example: {"42": ["8.5", "UK 8"], "M": ["Medium"]}. Generic: empty = exact only.
    size_aliases: dict[str, list[str]] = Field(default_factory=dict)
    # Alternate regional URLs for the same product (US/ES/JP). Used by `buybot resolve`.
    watch_urls: list[str] = Field(default_factory=list)
    quantity: int = Field(default=1, ge=1)
    profile_dir: str = "profiles/default"
    cdp_url: str | None = None
    headless: bool = True
    locale: str = "es-ES"
    safety_mode: bool = True
    webhook_secret: SecretStr | None = None
    checkout_timeout_seconds: int = Field(default=60, ge=5)
    manual_completion_timeout_seconds: int = Field(default=900, ge=0)
    shipping: ShippingAddress
    payment: Payment = Field(default_factory=Payment)
    alerts: list[AlertTarget] = Field(default_factory=list)
    selectors: dict[str, str] = Field(default_factory=dict)
    buy_markers: list[str] = Field(default_factory=lambda: list(DEFAULT_BUY_MARKERS))
    out_of_stock_markers: list[str] = Field(default_factory=lambda: list(DEFAULT_OUT_OF_STOCK_MARKERS))
    login_required_markers: list[str] = Field(
        default_factory=lambda: ["Inicia sesión para comprar", "Login to buy", "Sign in to buy"]
    )

    @field_validator("product_url")
    @classmethod
    def _validate_product_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("product_url must be a valid http(s) URL")
        return value

    @property
    def webhook_secret_value(self) -> str | None:
        secret = self.webhook_secret
        if secret is None:
            return None
        if isinstance(secret, str):
            return secret
        return secret.get_secret_value()


def _expand_env(value: object) -> object:
    """Expand ${ENV_VAR} in strings (generic secrets support); leaves other types alone."""
    import os as _os

    if isinstance(value, str):
        return _os.path.expandvars(value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: str | Path) -> BuyConfig:
    """Load and validate a ``BuyConfig`` from a YAML file.

    Supports ``${ENV_VAR}`` interpolation so secrets need not live in plaintext
    (e.g. ``webhook_secret: "${BUYBOT_SECRET}"``). Warns when the file is
    group/world-readable and holds a secret.
    """
    import logging as _logging
    import os as _os

    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _expand_env(data)
    config = BuyConfig.model_validate(data)
    if config.webhook_secret_value or config.payment.card_number:
        try:
            mode = _os.stat(path).st_mode
            if mode & 0o077:
                _logging.getLogger("buybot").warning(
                    "config file %s is readable by group/others; run chmod 600 on it", path
                )
        except OSError:
            pass
    return config


def resolve_profile_dir(config: BuyConfig, config_path: str | Path) -> Path:
    """Resolve ``profile_dir`` to an absolute path anchored to the config file."""
    profile = Path(config.profile_dir)
    if profile.is_absolute():
        return profile
    return Path(config_path).resolve().parent / profile
