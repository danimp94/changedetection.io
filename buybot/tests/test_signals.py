from __future__ import annotations

from buybot.signals import StockState, availability_from_html, detect_stock_state, stock_state_from_html


def test_detect_in_stock() -> None:
    assert detect_stock_state("Haz clic en Comprar ya para continuar") is StockState.IN_STOCK


def test_detect_out_of_stock() -> None:
    assert detect_stock_state("Este producto está agotado") is StockState.OUT_OF_STOCK


def test_detect_unknown_when_no_markers() -> None:
    assert detect_stock_state("Texto irrelevante sobre la marca") is StockState.UNKNOWN


def test_in_stock_takes_precedence() -> None:
    text = "Sin existencias Comprar ya"
    assert detect_stock_state(text) is StockState.IN_STOCK


def test_availability_from_html_scoped_by_sku() -> None:
    html = (
        '\\"sku\\":\\"RB3864-00-00\\",\\"price\\":{\\"amount\\":384},'
        '\\"availability\\":\\"outOfStock\\"'
    )
    assert availability_from_html(html, sku="RB3864-00-00") == "outOfStock"


def test_availability_from_html_ignores_other_products() -> None:
    html = (
        '\\"sku\\":\\"OTHER-01\\",\\"availability\\":\\"inStock\\"'
        '\\"sku\\":\\"RB3864-00-00\\",\\"availability\\":\\"outOfStock\\"'
    )
    assert availability_from_html(html, sku="RB3864-00-00") == "outOfStock"


def test_availability_from_html_missing_sku_returns_none() -> None:
    assert availability_from_html('\\"availability\\":\\"inStock\\"', sku="NOPE") is None


def test_stock_state_from_html() -> None:
    assert stock_state_from_html('\\"availability\\":\\"inStock\\"') is StockState.IN_STOCK
    assert stock_state_from_html('\\"availability\\":\\"outOfStock\\"') is StockState.OUT_OF_STOCK
