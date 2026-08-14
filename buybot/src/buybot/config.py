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
    quantity: int = Field(default=1, ge=1)
    profile_dir: str = "profiles/default"
    cdp_url: str | None = None
    headless: bool = True
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


def load_config(path: str | Path) -> BuyConfig:
    """Load and validate a ``BuyConfig`` from a YAML file."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BuyConfig.model_validate(data)


def resolve_profile_dir(config: BuyConfig, config_path: str | Path) -> Path:
    """Resolve ``profile_dir`` to an absolute path anchored to the config file."""
    profile = Path(config.profile_dir)
    if profile.is_absolute():
        return profile
    return Path(config_path).resolve().parent / profile
