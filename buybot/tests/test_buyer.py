from __future__ import annotations

import pytest

from buybot.buyer import LoginRequiredError, OutOfStockError, run_checkout
from buybot.config import BuyConfig, Payment, ShippingAddress


class FakeResponse:
    def __init__(self, html: str) -> None:
        self.html = html

    def text(self) -> str:
        return self.html


class FakePage:
    def __init__(self, body_text: str, html: str = "") -> None:
        self.body_text = body_text
        self.html = html
        self.calls: list[tuple] = []
        self.filled: dict[str, str] = {}

    def goto(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("goto", url))
        return FakeResponse(self.html)

    def inner_text(self, selector: str) -> str:
        return self.body_text

    def click(self, selector: str, **kwargs) -> None:
        self.calls.append(("click", selector))

    def fill(self, selector: str, value: str, **kwargs) -> None:
        self.calls.append(("fill", selector, value))
        self.filled[selector] = value

    def select_option(self, selector: str, value=None, *, label=None) -> None:
        self.calls.append(("select_option", selector, value))

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.calls.append(("wait", milliseconds))


def make_config(**overrides) -> BuyConfig:
    base = dict(
        product_url="https://merch.riotgames.com/es-es/product/x/",
        sku="RB3864-00-00",
        shipping=ShippingAddress(
            full_name="Ada Lovelace",
            email="ada@example.com",
            phone="+34600000000",
            address_line1="Calle Falsa 123",
            city="Madrid",
            postal_code="28001",
            country="España",
        ),
    )
    base.update(overrides)
    return BuyConfig(**base)


def test_out_of_stock_aborts_without_clicking() -> None:
    page = FakePage("Este producto está agotado. Sin existencias.")
    with pytest.raises(OutOfStockError):
        run_checkout(page, make_config())
    assert not any(call[0] == "click" for call in page.calls)


def test_safety_mode_stops_before_payment() -> None:
    page = FakePage("Comprar ya")
    config = make_config(size="M", safety_mode=True)
    result = run_checkout(page, config)
    assert result.stage == "awaiting_payment"
    click_selectors = [call[1] for call in page.calls if call[0] == "click"]
    assert any("Comprar ya" in s for s in click_selectors)
    assert any("Tramitar pedido" in s for s in click_selectors)
    select_call = (
        "select_option",
        'select[aria-label*="talla" i], select[name*="size" i], select[id*="size" i]',
        "M",
    )
    assert select_call in page.calls
    assert "input[type=\"email\"]" in page.filled
    assert page.filled["input[type=\"email\"]"] == "ada@example.com"


def test_full_order_when_auto_pay() -> None:
    page = FakePage("Añadir a la cesta")
    config = make_config(
        safety_mode=False,
        payment=Payment(
            card_number="4242424242424242",
            card_holder="Ada Lovelace",
            expiry_month="12",
            expiry_year="2030",
            cvv="123",
        ),
    )
    result = run_checkout(page, config)
    assert result.stage == "ordered"
    click_selectors = [call[1] for call in page.calls if call[0] == "click"]
    assert any("Pagar" in s for s in click_selectors)
    assert page.filled.get("input[autocomplete=\"cc-number\"], input[name*=\"cardnumber\" i]") == "4242424242424242"


def test_unknown_state_aborts() -> None:
    page = FakePage("Página de producto sin botón de compra")
    with pytest.raises(OutOfStockError):
        run_checkout(page, make_config())


def test_embedded_json_out_of_stock_wins_over_text() -> None:
    html = (
        '\\"sku\\":\\"RB3864-00-00\\",\\"price\\":{\\"amount\\":384},'
        '\\"availability\\":\\"outOfStock\\"'
    )
    page = FakePage("COMPRAR AHORA", html=html)
    with pytest.raises(OutOfStockError):
        run_checkout(page, make_config(sku="RB3864-00-00"))


def test_embedded_json_in_stock_proceeds() -> None:
    html = (
        '\\"sku\\":\\"RB3864-00-00\\",\\"price\\":{\\"amount\\":384},'
        '\\"availability\\":\\"inStock\\"'
    )
    page = FakePage("Sin existencias", html=html)
    result = run_checkout(page, make_config(sku="RB3864-00-00", safety_mode=True))
    assert result.stage == "awaiting_payment"


def test_login_required_aborts_before_clicking() -> None:
    html = '\\"sku\\":\\"RB3864-00-00\\",\\"availability\\":\\"inStock\\"'
    page = FakePage("Inicia sesión para comprar", html=html)
    with pytest.raises(LoginRequiredError):
        run_checkout(page, make_config(sku="RB3864-00-00"))
    assert not any(call[0] == "click" for call in page.calls)
