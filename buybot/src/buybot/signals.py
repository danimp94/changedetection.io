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
    "Buy now",
]

DEFAULT_OUT_OF_STOCK_MARKERS = [
    "Sin existencias",
    "Agotado",
    "No disponible",
    "Sold out",
    "Out of stock",
    "Próximamente",
]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def contains_marker(text: str, markers: list[str]) -> bool:
    """Return True if any marker appears in ``text`` (case- and accent-insensitive)."""
    haystack = _normalize(text)
    return any(_normalize(marker) in haystack for marker in markers)


def detect_stock_state(
    text: str,
    buy_markers: list[str] | None = None,
    out_of_stock_markers: list[str] | None = None,
) -> StockState:
    """Classify visible page text as in-stock, out-of-stock, or unknown.

    In-stock markers take precedence because a "sold out" label can coexist with
    a "buy now" button during transient UI states. Matching is case- and
    accent-insensitive.
    """
    haystack = _normalize(text)
    buy_markers = buy_markers or DEFAULT_BUY_MARKERS
    out_of_stock_markers = out_of_stock_markers or DEFAULT_OUT_OF_STOCK_MARKERS

    for marker in buy_markers:
        if _normalize(marker) in haystack:
            return StockState.IN_STOCK
    for marker in out_of_stock_markers:
        if _normalize(marker) in haystack:
            return StockState.OUT_OF_STOCK
    return StockState.UNKNOWN


def availability_from_html(html: str, sku: str | None = None) -> str | None:
    """Extract the raw ``availability`` value embedded in the Next.js HTML.

    The Riot merch storefront renders product data server-side as escaped JSON
    (``\\"availability\\":\\"outOfStock\\"``). When ``sku`` is provided the search
    is scoped to that product so related-item recommendations don't cause false
    positives.
    """
    unescaped = html.replace('\\"', '"')
    if sku:
        index = unescaped.find(f'"sku":"{sku}"')
        if index == -1:
            return None
        window = unescaped[index : index + 4000]
    else:
        window = unescaped

    match = re.search(r'"availability"\s*:\s*"([a-zA-Z]+)"', window)
    return match.group(1) if match else None


def stock_state_from_html(html: str, sku: str | None = None) -> StockState:
    """Translate the embedded ``availability`` value into a :class:`StockState`."""
    value = availability_from_html(html, sku=sku)
    if value is None:
        return StockState.UNKNOWN
    normalized = value.lower()
    if normalized == "instock":
        return StockState.IN_STOCK
    if normalized in {"outofstock", "out_of_stock", "soldout"}:
        return StockState.OUT_OF_STOCK
    return StockState.UNKNOWN
