"""Pure functions for detecting product availability from page text or raw HTML."""

from __future__ import annotations

import re
import unicodedata
from enum import Enum


class StockState(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    UNKNOWN = "unknown"


DEFAULT_BUY_MARKERS = [
    "Comprar ahora",
    "Comprar ya",
    "Añadir a la cesta",
    "Add to cart",
    "Add to bag",
    "Add to basket",
    "Buy now",
]

DEFAULT_OUT_OF_STOCK_MARKERS = [
    "Sin existencias",
    "Agotado",
    "No disponible",
    "Sold out",
    "Out of stock",
    "Próximamente",
    "Coming soon",
    "Notify me",
    "Unavailable",
]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def contains_marker(text: str, markers: list[str]) -> bool:
    """Return True if any marker appears in ``text`` (case- and accent-insensitive)."""
    haystack = _normalize(text)
    return any(_normalize(marker) in haystack for marker in markers)


LISTING_PATH_HINTS = ("/launch/", "/collections/", "/category/", "/catalog/", "/search")


def is_listing_page(html: str, url: str = "") -> bool:
    """Heuristic: announcement/listing page without a purchasable offer (brand-agnostic).

    True when the URL looks like a launch/collection feed AND no availability
    JSON is embedded. Callers should treat this as UNKNOWN ("needs product URL")
    and never checkout. Single-product pages (even without size) return False.
    """
    if not re.findall(r'"availability"\s*:\s*"[^"]+"', html or ""):
        path = (url or "").lower()
        if any(hint in path for hint in LISTING_PATH_HINTS):
            return True
    return False


def detect_stock_state(
    text: str,
    buy_markers: list[str] | None = None,
    out_of_stock_markers: list[str] | None = None,
) -> StockState:
    """Classify visible page text as in-stock, out-of-stock, or unknown.

    Returns UNKNOWN when buy and out-of-stock markers coexist (conflicting UI,
    e.g. a persistent "buy now" element alongside "sold out"), so callers can
    distinguish "detector blind / markup changed" from a definitive OOS and
    avoid false-positive checkouts. Matching is case- and accent-insensitive.
    """
    haystack = _normalize(text)
    buy_markers = buy_markers or DEFAULT_BUY_MARKERS
    out_of_stock_markers = out_of_stock_markers or DEFAULT_OUT_OF_STOCK_MARKERS

    has_buy = any(_normalize(marker) in haystack for marker in buy_markers)
    has_oos = any(_normalize(marker) in haystack for marker in out_of_stock_markers)
    if has_buy and has_oos:
        return StockState.UNKNOWN
    if has_buy:
        return StockState.IN_STOCK
    if has_oos:
        return StockState.OUT_OF_STOCK
    return StockState.UNKNOWN


def _normalize_availability(value: str) -> str:
    """Normalize raw availability to a compact token.

    Handles plain tokens (``inStock``), schema.org URLs
    (``https://schema.org/InStock``), and separators (``_``, ``-``, spaces).
    """
    token = value.strip().lower()
    # Take the last path/fragment segment for URLs/URNs.
    for sep in ("/", "#", ":"):
        if sep in token:
            token = token.rsplit(sep, 1)[-1]
    return re.sub(r"[\s_\-]+", "", token)


def availability_from_html(html: str, sku: str | None = None) -> str | None:
    """Extract the raw ``availability`` value embedded in the page HTML.

    Handles both the Riot merch storefront (escaped Next.js JSON like
    ``\\"availability\\":\\"outOfStock\\"``) and generic JSON-LD offers
    (``"availability":"https://schema.org/InStock"``). When ``sku`` is
    provided the search is scoped to a window around that product so
    related-item recommendations don't cause false positives; the window
    extends in both directions because JSON key order is not guaranteed.
    """
    unescaped = html.replace('\\"', '"').replace("&quot;", '"')
    if not sku:
        matches = re.findall(r'"availability"\s*:\s*"([^"]+)"', unescaped)
        return matches[0] if matches else None

    index = unescaped.find(f'"sku":"{sku}"')
    if index == -1:
        sku_match = re.search(rf'"sku"\s*:\s*"{re.escape(sku)}"', unescaped)
        if not sku_match:
            return None
        index = sku_match.start()

    avail_re = re.compile(r'"availability"\s*:\s*"([^"]+)"')
    sku_re = re.compile(r'"sku"\s*:\s*"')
    # Forward first (common order: sku ... availability), bounded by the next
    # product's sku (any whitespace variant) so related items can't leak in.
    next_match = sku_re.search(unescaped, index + 1)
    next_sku = next_match.start() if next_match else len(unescaped)
    forward_end = min(index + 8000, next_sku)
    forward_match = avail_re.search(unescaped, index, forward_end)
    if forward_match:
        return forward_match.group(1)
    # Fallback for reversed order (availability ... sku): take the nearest
    # availability strictly after the previous product boundary, and only when
    # close to our SKU (avoids attributing the previous product's stock).
    prev_matches = list(sku_re.finditer(unescaped, 0, index))
    prev_boundary = prev_matches[-1].start() if prev_matches else index - 8000
    back_start = max(prev_boundary, index - 8000)
    backward_matches = list(avail_re.finditer(unescaped, back_start, index))
    if backward_matches:
        nearest = backward_matches[-1]
        # Require proximity: availability far from SKU likely belongs to prev product.
        if index - nearest.end() <= 2000:
            return nearest.group(1)
    return None


def stock_state_from_html(html: str, sku: str | None = None) -> StockState:
    """Translate the embedded ``availability`` value into a :class:`StockState`."""
    value = availability_from_html(html, sku=sku)
    if value is None:
        return StockState.UNKNOWN
    normalized = _normalize_availability(value)
    if normalized in {"instock", "limitedavailability", "preorder", "backorder", "available"}:
        # Limited/PreOrder/BackOrder are buyable states; treat as in-stock
        # so the bot attempts checkout rather than missing a drop.
        return StockState.IN_STOCK
    if normalized in {
        "outofstock",
        "soldout",
        "discontinued",
        "unavailable",
        "instoreonly",
    }:
        # InStoreOnly means not buyable online, so OOS for this bot.
        return StockState.OUT_OF_STOCK
    return StockState.UNKNOWN
