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
    """Raised when the product is definitively not available."""


class UnknownStateError(CheckoutError):
    """Raised when availability cannot be determined (detector blind).

    Kept distinct from OutOfStockError so operators don't mistake a markup
    change or missing JSON for a real sold-out.
    """


class SizeRequiredError(CheckoutError):
    """Raised when a size must be chosen but none was configured/matched."""


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
    def content(self) -> str: ...  # rendered DOM; FakePage in tests may omit (handled via getattr)


DEFAULT_SELECTORS: dict[str, str] = {
    "size": 'select[aria-label*="talla" i], select[name*="size" i], select[id*="size" i]',
    "size_buttons": 'button[data-testid*="size" i], [data-test*="size"] button, div[class*="size"] button',
    "buy_now": (
        'div[class*="purchase-bar"] button:has-text("Comprar ya"), button[title="Comprar ya"], '
        'button:has-text("Buy now"), button:has-text("Comprar ahora")'
    ),
    "add_to_cart": (
        'div[class*="purchase-bar"] button[aria-label="Añadir a la cesta"], '
        'div[class*="purchase-bar"] button:has-text("Añadir a la cesta"), '
        'button[data-testid*="add-to-bag" i], button[data-test*="add-to-bag" i], '
        'button:has-text("Add to bag"), button:has-text("Add to basket"), button:has-text("Add to cart")'
    ),
    "checkout": (
        'button:has-text("Checkout"), button:has-text("Tramitar pedido"), '
        'button:has-text("Finalizar compra"), button:has-text("View bag"), '
        'a:has-text("Checkout"), a:has-text("View bag")'
    ),
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
    "place_order": (
        'button:has-text("Pagar"), button:has-text("Pay now"), button:has-text("Realizar pedido"), '
        'button:has-text("Place order")'
    ),
    "quantity": 'input[name*="qty" i], input[name*="quantity" i], select[name*="qty" i], select[name*="quantity" i]',
}


def _selector(config: BuyConfig, name: str) -> str:
    return config.selectors.get(name, DEFAULT_SELECTORS.get(name, ""))


def detect_stock(html: str, body_text: str, config: BuyConfig) -> StockState:
    """Resolve availability, preferring the SKU-scoped embedded JSON over text.

    The storefront renders product availability server-side (``"availability": "inStock"``)
    but also keeps a disabled "buy now" element in the DOM when out of stock, so raw text
    is unreliable on its own. JSON wins when present; visible text is only a fallback.
    """
    import re as _re

    from .signals import is_listing_page

    if is_listing_page(html, config.product_url):
        logger.warning("page looks like a launch/listing feed without a purchasable offer; needs product URL")
        return StockState.UNKNOWN
    state = stock_state_from_html(html, sku=config.sku)
    if state is not StockState.UNKNOWN:
        return state
    if not config.sku:
        hits = len(_re.findall(r'"availability"\s*:\s*"[^"]+"', html or ""))
        if hits > 1:
            logger.warning(
                "sku is not set and page contains %d availability blocks; "
                "set sku in config.yaml to avoid related-item false positives.",
                hits,
            )
    return detect_stock_state(body_text, config.buy_markers, config.out_of_stock_markers)


def _fill_optional(page: PageProtocol, selector: str, value: str, timeout: int) -> bool:
    """Fill a text input; returns True on success. Optional fields ignore failure."""
    if not selector or not value:
        return True
    try:
        page.fill(selector, value, timeout=timeout)
        return True
    except Exception:
        logger.debug("fill skipped for selector %s", selector)
        return False


_COUNTRY_ALIASES: dict[str, list[str]] = {
    # configured value (lowercased, accent-stripped) -> candidates to try as label/value.
    "espana": ["Spain", "ES", "España"],
    "spain": ["Spain", "ES", "España"],
    "es": ["ES", "Spain", "España"],
    "deutschland": ["Germany", "DE", "Deutschland"],
    "germany": ["Germany", "DE", "Deutschland"],
    "france": ["France", "FR"],
    "italia": ["Italy", "IT", "Italia"],
    "italy": ["Italy", "IT", "Italia"],
    "portugal": ["Portugal", "PT"],
    "united kingdom": ["United Kingdom", "GB", "UK"],
    "united states": ["United States", "US", "USA"],
}


def _country_candidates(value: str) -> list[str]:
    import unicodedata as _ud

    key = "".join(c for c in _ud.normalize("NFKD", value or "") if not _ud.combining(c)).lower().strip()
    seen = [value]
    for alias in _COUNTRY_ALIASES.get(key, []):
        if alias not in seen:
            seen.append(alias)
    return seen


def _fill_select_or_text(page: PageProtocol, selector: str, value: str, timeout: int) -> bool:
    """Fill country/region which may be a <select> (adidas) or text <input>."""
    if not selector or not value:
        return True
    for candidate in _country_candidates(value):
        for kwargs in ({"label": candidate}, {"value": candidate}):
            try:
                page.select_option(selector, **kwargs)  # type: ignore[arg-type]
                return True
            except Exception:
                continue
    return _fill_optional(page, selector, value, timeout)


def _size_candidates(config: BuyConfig) -> list[str]:
    """Configured size plus generic aliases (EU/US sneaker equivalents + user map)."""
    base = [config.size] if config.size else []
    for alias in config.size_aliases.get(config.size or "", []):
        if alias not in base:
            base.append(alias)
    return base


def _available_sizes_best_effort(page: PageProtocol, config: BuyConfig) -> list[str]:
    """Scrape visible size options when the live page supports locators."""
    locator_fn = getattr(page, "locator", None)
    if not callable(locator_fn):
        return []
    try:
        loc = locator_fn(_selector(config, "size_buttons"))
        texts = loc.all_inner_texts() if hasattr(loc, "all_inner_texts") else []
        return [t.strip() for t in texts if t and t.strip()][:30]
    except Exception:
        return []


def _select_size(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    # No-size products (one-size, accessories): skip entirely.
    if not config.size:
        return
    for wanted in _size_candidates(config):
        selector = _selector(config, "size")
        if selector:
            for kwargs in ({"value": wanted}, {"label": wanted}):
                try:
                    page.select_option(selector, **kwargs)  # type: ignore[arg-type]
                    return
                except Exception:
                    continue
            logger.debug("select size via <select> failed; trying button grid")
        # Button-grid pickers (adidas/Nike): exact text match first
        # (:text-is), because :has-text("M") would also match "XL"/"LM".
        buttons_selector = _selector(config, "size_buttons")
        tries = []
        if buttons_selector:
            tries.append(f'{buttons_selector}:text-is("{wanted}")')
        tries.append(f'button:text-is("{wanted}")')
        for candidate in tries:
            try:
                page.click(candidate, timeout=min(timeout, 5_000))
                return
            except Exception:
                continue
    available = _available_sizes_best_effort(page, config)
    hint = f" Available: {', '.join(available)}." if available else ""
    raise SizeRequiredError(f"Could not select size {config.size!r}.{hint}") from None


def _set_quantity(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    """Best-effort quantity; warns when quantity > 1 cannot be applied."""
    if config.quantity <= 1:
        return
    selector = _selector(config, "quantity")
    if not selector:
        logger.warning("quantity=%d requested but no quantity selector known", config.quantity)
        return
    try:
        page.select_option(selector, str(config.quantity))
        return
    except Exception:
        pass
    try:
        page.fill(selector, str(config.quantity), timeout=min(timeout, 5_000))
    except Exception:
        logger.warning("quantity=%d requested but could not be set", config.quantity)


def _click_cta(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    # Short per-CTA timeouts: full checkout_timeout for goto, but only a few
    # seconds per button attempt so a wrong first selector doesn't cost the race.
    per_click = min(timeout, 5_000)
    for name in ("buy_now", "add_to_cart"):
        selector = _selector(config, name)
        if not selector:
            continue
        try:
            page.click(selector, timeout=per_click)
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


def _verify_cart_best_effort(page: PageProtocol, config: BuyConfig) -> None:
    """Generic post-CTA sanity (brand-agnostic, one-size safe).

    Raises only when the session clearly died (login wall appeared). A stale
    out-of-stock text snippet is only a warning: JSON detection is authoritative
    and body text may not have re-rendered yet after the click.
    """
    try:
        body = page.inner_text("body")
    except Exception:
        return
    if contains_marker(body, config.login_required_markers):
        raise LoginRequiredError("Login wall appeared after add-to-cart; re-run `buybot login`.")
    if contains_marker(body, config.out_of_stock_markers) and not contains_marker(body, config.buy_markers):
        logger.warning("post-CTA page still shows out-of-stock text; cart may be empty")


REQUIRED_SHIPPING_FIELDS = {"email", "full_name", "address_line1", "city", "postal_code", "country"}


def _screenshot_best_effort(page: PageProtocol, name: str) -> None:
    """Save a failure/success screenshot when the live page supports it."""
    shot = getattr(page, "screenshot", None)
    if not callable(shot):
        return
    try:
        import time as _time
        from pathlib import Path as _Path

        outdir = _Path("artifacts")
        outdir.mkdir(parents=True, exist_ok=True)
        shot(path=str(outdir / f"{name}-{int(_time.time())}.png"))
    except Exception:
        logger.debug("screenshot failed", exc_info=True)


def _fill_shipping(page: PageProtocol, config: BuyConfig, timeout: int) -> None:
    shipping = config.shipping
    text_fields = {
        "email": shipping.email,
        "full_name": shipping.full_name,
        "address_line1": shipping.address_line1,
        "address_line2": shipping.address_line2,
        "city": shipping.city,
        "postal_code": shipping.postal_code,
        "phone": shipping.phone,
    }
    missing: list[str] = []
    for name, value in text_fields.items():
        ok = _fill_optional(page, _selector(config, name), value, timeout)
        if not ok and name in REQUIRED_SHIPPING_FIELDS and value:
            missing.append(name)
    # country/region are often <select> elements (adidas) — try select first.
    if not _fill_select_or_text(page, _selector(config, "region"), shipping.region, timeout):
        logger.debug("region fill skipped")
    if not _fill_select_or_text(page, _selector(config, "country"), shipping.country, timeout):
        if shipping.country:
            missing.append("country")
    if missing:
        raise SelectorNotFoundError(f"Could not fill required shipping fields: {', '.join(missing)}")


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
    """Run the full checkout flow and return a :class:`CheckoutResult`.

    NOTE: card fields on modern storefronts (incl. adidas) often live in
    Stripe/Adyen iframes that ``page.fill`` cannot pierce — keep
    ``safety_mode: true`` live until a frame-aware payment step lands.
    """
    timeout = config.checkout_timeout_seconds * 1000

    # Retry goto+detect twice: transient overlays/rate-limits shouldn't kill a drop.
    # Generic across brands; FakePage-compatible (no new protocol methods required).
    response = None
    last_goto_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = page.goto(config.product_url, wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(1500)
            break
        except Exception as exc:
            last_goto_exc = exc
            logger.debug("goto attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                page.wait_for_timeout(1000)
    else:
        raise CheckoutError(f"Could not load product page: {last_goto_exc}") from last_goto_exc

    # response.text() is initial HTML only; prefer rendered DOM for CSR-hydrated JSON-LD.
    html = ""
    content_fn = getattr(page, "content", None)
    if callable(content_fn):
        try:
            html = content_fn() or ""
        except Exception:
            html = ""
    if not html and response is not None:
        try:
            html = response.text()
        except Exception:
            html = ""
    body_text = page.inner_text("body")
    if contains_marker(body_text, config.login_required_markers):
        raise LoginRequiredError("Login required before buying; run `buybot login` first.")
    state = detect_stock(html, body_text, config)
    if state is StockState.OUT_OF_STOCK:
        raise OutOfStockError("Product is not available (detected state: out_of_stock)")
    if state is not StockState.IN_STOCK:
        raise UnknownStateError(
            f"Availability unknown (detected state: {state.value}); "
            "markup may have changed — not treating as sold out."
        )

    logger.info("in stock and logged in; proceeding to add to cart / buy now")
    try:
        _select_size(page, config, timeout)
        # Quantity before CTA: stores apply qty at add-to-cart time.
        _set_quantity(page, config, timeout)
        _click_cta(page, config, timeout)
        logger.info("buy CTA clicked; navigating to checkout")
        _goto_checkout(page, config, timeout)
        _verify_cart_best_effort(page, config)
        logger.info("filling shipping details")
        _fill_shipping(page, config, timeout)

        if config.safety_mode:
            result = CheckoutResult(stage="awaiting_payment", message="Reached checkout; confirm payment manually.")
            _screenshot_best_effort(page, "awaiting-payment")
            return result

        _fill_payment(page, config, timeout)
        _place_order(page, config, timeout)
        result = CheckoutResult(stage="ordered", message="Order placed.")
        _screenshot_best_effort(page, "ordered")
        return result
    except CheckoutError:
        _screenshot_best_effort(page, "checkout-failed")
        raise
    except Exception as exc:  # noqa: BLE001
        _screenshot_best_effort(page, "checkout-failed")
        raise CheckoutError(str(exc)) from exc
