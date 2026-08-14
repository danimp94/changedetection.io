from __future__ import annotations

import pytest
from pydantic import ValidationError

from buybot.config import BuyConfig, load_config


def test_load_config_from_yaml(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
product_url: "https://merch.riotgames.com/es-es/product/riftbound-t1-2025-worlds-signature-edition/"
sku: "RB3864-00-00"
shipping:
  full_name: "Ada Lovelace"
  email: "ada@example.com"
  address_line1: "Calle Falsa 123"
  city: "Madrid"
  postal_code: "28001"
  country: "España"
safety_mode: true
""".strip(),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.product_url.startswith("https://merch.riotgames.com")
    assert config.sku == "RB3864-00-00"
    assert config.safety_mode is True
    assert config.shipping.city == "Madrid"


def test_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError):
        BuyConfig(
            product_url="not-a-url",
            shipping={
                "full_name": "Ada",
                "email": "ada@example.com",
                "address_line1": "x",
                "city": "x",
                "postal_code": "1",
                "country": "x",
            },
        )


def test_secret_value_roundtrip(sample_config: BuyConfig) -> None:
    config = sample_config.model_copy(update={"webhook_secret": "s3cret"})
    assert config.webhook_secret_value == "s3cret"
    assert sample_config.webhook_secret_value is None
