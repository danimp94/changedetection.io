from __future__ import annotations

import pytest

from buybot.config import BuyConfig, ShippingAddress


@pytest.fixture
def sample_config() -> BuyConfig:
    return BuyConfig(
        product_url="https://merch.riotgames.com/es-es/product/riftbound-t1-2025-worlds-signature-edition/",
        sku="RB3864-00-00",
        shipping=ShippingAddress(
            full_name="Ada Lovelace",
            email="ada@example.com",
            phone="+34600000000",
            address_line1="Calle Falsa 123",
            city="Madrid",
            postal_code="28001",
            region="Madrid",
            country="España",
        ),
    )
