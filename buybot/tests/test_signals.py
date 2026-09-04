from __future__ import annotations

from buybot.signals import StockState, availability_from_html, detect_stock_state, stock_state_from_html


def test_detect_in_stock() -> None:
    assert detect_stock_state("Haz clic en Comprar ya para continuar") is StockState.IN_STOCK


def test_detect_out_of_stock() -> None:
    assert detect_stock_state("Este producto está agotado") is StockState.OUT_OF_STOCK


def test_detect_unknown_when_no_markers() -> None:
    assert detect_stock_state("Texto irrelevante sobre la marca") is StockState.UNKNOWN


def test_conflicting_markers_yield_unknown() -> None:
    text = "Sin existencias Comprar ya"
    assert detect_stock_state(text) is StockState.UNKNOWN


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


def test_schema_org_urls() -> None:
    assert (
        stock_state_from_html('"availability":"https://schema.org/InStock"') is StockState.IN_STOCK
    )
    assert (
        stock_state_from_html('"availability":"https://schema.org/OutOfStock"') is StockState.OUT_OF_STOCK
    )
    assert (
        stock_state_from_html('"availability":"http://schema.org/LimitedAvailability"') is StockState.IN_STOCK
    )
    assert (
        stock_state_from_html('"availability":"https://schema.org/InStoreOnly"') is StockState.OUT_OF_STOCK
    )


def test_sku_window_bidirectional() -> None:
    # availability BEFORE sku (reversed key order) must still be found.
    html = '"availability":"inStock","name":"x","sku":"RB3864-00-00"'
    assert stock_state_from_html(html, sku="RB3864-00-00") is StockState.IN_STOCK


def test_adidas_markers() -> None:
    assert detect_stock_state("Add to bag") is StockState.IN_STOCK
    assert detect_stock_state("Coming soon — Notify me") is StockState.OUT_OF_STOCK


def test_listing_page_guard() -> None:
    from buybot.signals import is_listing_page

    html = "<div>Upcoming drops</div><a href='/launch/t/x'>details</a>"
    assert is_listing_page(html, "https://www.nike.com/us/launch/upcoming") is True
    assert is_listing_page(html, "https://merch.riotgames.com/product/x/") is False
    assert is_listing_page('"availability":"inStock"', "https://www.nike.com/us/launch/upcoming") is False
