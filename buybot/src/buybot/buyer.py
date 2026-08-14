"""Checkout orchestration for the Riot Games merch storefront.

The flow is broken into small, individually testable steps that operate against a
minimal page interface (see :class:`PageProtocol`), so the live Playwright page can
be swapped for a fake in tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from .config import BuyConfig
from .signals import StockState, contains_marker, detect_stock_state, stock_state_from_html

logger = logging.getLogger("buybot.buyer")


class CheckoutError(Exception):
    """Base error for any checkout failure."""


class OutOfStockError(CheckoutError):
    """Raised when the product is not available at checkout time."""


class LoginRequiredError(CheckoutError):
    """Raised when the Riot session is not authenticated and buying is blocked."""


class SelectorNotFoundError(CheckoutError):
    """Raised when a required element cannot be located."""


@dataclass
class CheckoutResult:
    stage: str
    message: str


class ResponseProtocol(Protocol):
    def text(self) -> str: ...


class PageProtocol(Protocol):
    """Narrow view of a Playwright page used by the checkout steps."""

    def goto(self, url: str, *, wait_until: str = "load", timeout: int = 30_000) -> ResponseProtocol | None: ...
    def inner_text(self, selector: str) -> str: ...
    def click(self, selector: str, *, timeout: int = 30_000) -> None: ...
    def fill(self, selector: str, value: str, *, timeout: int = 30_000) -> None: ...
    def select_option(self, selector: str, value: str | None = None, *, label: str | None = None) -> None: ...
    def wait_for_timeout(self, milliseconds: int) -> None: ...


DEFAULT_SELECTORS: dict[str, str] = {
    "size": 'select[aria-label*="talla" i], select[name*="size" i], select[id*="size" i]',
    "buy_now": 'div[class*="purchase-bar"] button:has-text("Comprar ya"), button[title="Comprar ya"]',
    "add_to_cart": (
        'div[class*="purchase-bar"] button[aria-label="Añadir a la cesta"], '
        'div[class*="purchase-bar"] button:has-text("Añadir a la cesta")'
    ),
    "checkout": 'button:has-text("Checkout"), button:has-text("Tramitar pedido"), button:has-text("Finalizar compra")',
    "email": 'input[type="email"]',
    "full_name": 'input[autocomplete="name"], input[name*="name" i]',
    "address_line1": 'input[autocomplete="address-line1"], input[name*="address1" i]',
    "address_line2": 'input[autocomplete="address-line2"], input[name*="address2" i]',
    "city": 'input[autocomplete="address-level2"], input[name*="city" i]',
    "postal_code": 'input[autocomplete="postal-code"], input[name*="zip" i], input[name*="postal" i]',
    "region": 'input[autocomplete="address-level1"], select[name*="region" i], select[name*="state" i]',
    "country": 'select[name*="country" i]',
    "phone": 'input[type="tel"], input[name*="phone" i]',
    "card_number": 'input[autocomplete="cc-number"], input[name*="cardnumber" i]',
    "card_holder": 'input[autocomplete="cc-name"], input[name*="cardholder" i]',
    "expiry_month": 'input[autocomplete="cc-exp-month"], select[name*="expmonth" i]',
    "expiry_year": 'input[autocomplete="cc-exp-year"], select[name*="expyear" i]',
    "cvv": 'input[autocomplete="cc-csc"], input[name*="cvv" i]',
    "place_order": 'button:has-text("Pagar"), button:has-text("Pay now"), button:has-text("Realizar pedido")',
}


def _selector(config: BuyConfig, name: str) -> str:
    return config.selectors.get(name, DEFAULT_SELECTORS.get(name, ""))


def detect_stock(html: str, body_text: str, config: BuyConfig) -> StockState:
    """Resolve availability, preferring the SKU-scoped embedded JSON over text.

    The storefront renders product availability server-side (``"availability": "inStock"``)
    but also keeps a disabled "buy now" element in the DOM when out of stock, so raw text
    is unreliable on its own. JSON wins when present; visible text is only a fallback.
    """
    state = stock_state_from_html(html, sku=config.sku)
    if state is not StockState.UNKNOWN:
        return state
    return detect_stock_state(body_text, config.buy_markers, config.out_of_stock_markers)


def _fill_optional(page: PageProtocol, selector: str, value: str, timeout: int) -> None:
    if not selector or not value:
        return
    try:
        page.fill(selector, value, timeout=timeout)
    except Exception:
        logger.debug("fill skipped for selector %s", selector)


def _select_size(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    selector = _selector(config, "size")
    if not selector or not config.size:
        return
    page.select_option(selector, config.size)


def _click_cta(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    for name in ("buy_now", "add_to_cart"):
        selector = _selector(config, name)
        if not selector:
            continue
        try:
            page.click(selector, timeout=timeout)
            return
        except Exception:
            continue
    raise SelectorNotFoundError("Could not locate a buy/add-to-cart button")


def _goto_checkout(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    selector = _selector(config, "checkout")
    if selector:
        try:
            page.click(selector, timeout=min(timeout, 10_000))
        except Exception:
            logger.debug("no checkout button found; assuming buy-now already navigated to checkout")
    page.wait_for_timeout(1000)


def _fill_shipping(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    shipping = config.shipping
    fields = {
        "email": shipping.email,
        "full_name": shipping.full_name,
        "address_line1": shipping.address_line1,
        "address_line2": shipping.address_line2,
        "city": shipping.city,
        "postal_code": shipping.postal_code,
        "region": shipping.region,
        "country": shipping.country,
        "phone": shipping.phone,
    }
    for name, value in fields.items():
        _fill_optional(page, _selector(config, name), value, timeout)


def _fill_payment(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    payment = config.payment
    if payment.card_number is None:
        raise CheckoutError("Payment details are required when safety_mode is disabled")
    fields = {
        "card_number": payment.card_number.get_secret_value(),
        "card_holder": payment.card_holder or "",
        "expiry_month": payment.expiry_month or "",
        "expiry_year": payment.expiry_year or "",
        "cvv": (payment.cvv.get_secret_value() if payment.cvv else ""),
    }
    for name, value in fields.items():
        _fill_optional(page, _selector(config, name), value, timeout)


def _place_order(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    selector = _selector(config, "place_order")
    if not selector:
        raise SelectorNotFoundError("Could not locate a place-order button")
    page.click(selector, timeout=timeout)


def run_checkout(page: PageProtocol, config: BuyConfig) -> CheckoutResult:
    """Run the full checkout flow and return a :class:`CheckoutResult`."""
    timeout = config.checkout_timeout_seconds * 1000

    response = page.goto(config.product_url, wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_timeout(1500)

    html = response.text() if response is not None else ""
    body_text = page.inner_text("body")
    state = detect_stock(html, body_text, config)
    if state is not StockState.IN_STOCK:
        raise OutOfStockError(f"Product is not available (detected state: {state.value})")

    if contains_marker(body_text, config.login_required_markers):
        raise LoginRequiredError("Riot login required before buying; run `buybot login` first.")

    logger.info("in stock and logged in; proceeding to add to cart / buy now")
    _select_size(page, config, timeout)
    _click_cta(page, config, timeout)
    logger.info("buy CTA clicked; navigating to checkout")
    _goto_checkout(page, config, timeout)
    logger.info("filling shipping details")
    _fill_shipping(page, config, timeout)

    if config.safety_mode:
        return CheckoutResult(stage="awaiting_payment", message="Reached checkout; confirm payment manually.")

    _fill_payment(page, config, timeout)
    _place_order(page, config, timeout)
    return CheckoutResult(stage="ordered", message="Order placed.")
