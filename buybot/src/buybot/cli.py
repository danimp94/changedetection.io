"""Command-line interface: first-login helper, stock check, and webhook server."""

from __future__ import annotations

import argparse


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", "-c", required=True, help="Path to config.yaml")


def _load_resolved_config(path: str):
    from .config import load_config, resolve_profile_dir

    config = load_config(path)
    config.profile_dir = str(resolve_profile_dir(config, path))
    return config


def _cmd_login(args: argparse.Namespace) -> None:
    import time

    from .browser import BrowserSession
    from .signals import contains_marker

    config = _load_resolved_config(args.config)
    # Visible browser is required for SSO; still honour cdp_url when the user
    # drives their own Chrome instance.
    with BrowserSession(config.profile_dir, headless=False, cdp_url=config.cdp_url, locale=config.locale) as session:
        page = session.new_page()
        page.goto(config.product_url, wait_until="domcontentloaded")
        print("Log in to Riot SSO in the opened browser; waiting for the buy button to appear...")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                text = page.inner_text("body")
            except Exception:
                text = ""
            if contains_marker(text, config.buy_markers) and not contains_marker(
                text, config.login_required_markers
            ):
                print("Login detected.")
                return
            time.sleep(2)
        print("Timed out waiting for login; the session is still saved to the profile.")


def _cmd_check(args: argparse.Namespace) -> None:
    from .browser import BrowserSession
    from .buyer import detect_stock

    config = _load_resolved_config(args.config)
    # Use the configured headless mode so check fingerprints like serve.
    with BrowserSession(
        config.profile_dir, headless=config.headless, cdp_url=config.cdp_url, locale=config.locale
    ) as session:
        page = session.new_page()
        timeout = config.checkout_timeout_seconds * 1000
        response = page.goto(config.product_url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(1500)
        html = ""
        try:
            # Re-read after JS hydration; response.text() is initial HTML only.
            html = page.content() if hasattr(page, "content") else (response.text() if response else "")
            if response is not None and not html:
                html = response.text()
        except Exception:
            html = response.text() if response is not None else ""
        state = detect_stock(html, page.inner_text("body"), config)
    print(f"Stock state: {state.value}")


def _cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .webhook import create_app

    config = _load_resolved_config(args.config)
    if not config.webhook_secret_value:
        print("WARNING: webhook_secret is not set — /buy is unauthenticated. Set webhook_secret in config.yaml.")
    app = create_app(config)
    # Single worker is required: PurchaseManager is a per-process single-flight guard.
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


def _extract_product_urls(html: str, base_url: str) -> list[str]:
    """Generic launch/listing → product URL discovery (any brand)."""
    import re as _re
    from urllib.parse import urljoin as _join

    found: list[str] = []
    # Next.js/JSON-LD offers often embed product URLs; plus plain hrefs to
    # /product/, /products/, /launch/t/ detail pages.
    for pat in (
        r'"url"\s*:\s*"(https?://[^"]+)"',
        r'href="([^"]*(?:/product/|/products/|/launch/t/)[^"]*)"',
        r'href=\'([^\']*(?:/product/|/products/|/launch/t/)[^\']*)\'',
    ):
        for m in _re.findall(pat, html or ""):
            abs_url = _join(base_url, m)
            if abs_url not in found:
                found.append(abs_url)
    return found[:30]


def _cmd_resolve(args: argparse.Namespace) -> None:
    """List purchasable product URLs found on a launch/listing page (generic)."""
    from .browser import BrowserSession

    config = _load_resolved_config(args.config)
    url = args.url or config.product_url
    for candidate in [url, *config.watch_urls]:
        with BrowserSession(
            config.profile_dir, headless=config.headless, cdp_url=config.cdp_url, locale=config.locale
        ) as session:
            page = session.new_page()
            try:
                goto_timeout = config.checkout_timeout_seconds * 1000
                response = page.goto(candidate, wait_until="domcontentloaded", timeout=goto_timeout)
                page.wait_for_timeout(1500)
                html = page.content() if hasattr(page, "content") else (response.text() if response else "")
            except Exception as exc:
                print(f"{candidate}: load failed ({exc})")
                continue
        urls = _extract_product_urls(html, candidate)
        print(f"{candidate}: {len(urls)} product URL(s)")
        for u in urls:
            print(f"  {u}")


def _cmd_sizes(args: argparse.Namespace) -> None:
    """List available sizes without buying (generic; empty = one-size/unlisted)."""
    from .browser import BrowserSession
    from .buyer import _selector

    config = _load_resolved_config(args.config)
    with BrowserSession(
        config.profile_dir, headless=config.headless, cdp_url=config.cdp_url, locale=config.locale
    ) as session:
        page = session.new_page()
        page.goto(config.product_url, wait_until="domcontentloaded", timeout=config.checkout_timeout_seconds * 1000)
        page.wait_for_timeout(1500)
        locator_fn = getattr(page, "locator", None)
        if not callable(locator_fn):
            print("size listing needs a locator-capable page")
            return
        try:
            texts = locator_fn(_selector(config, "size_buttons")).all_inner_texts()
        except Exception as exc:
            print(f"size listing failed: {exc}")
            return
    sizes = [t.strip() for t in texts if t and t.strip()]
    print("Available sizes:" if sizes else "No size buttons found (one-size product or changed markup).")
    for s in sizes:
        print(f"  {s}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="buybot", description="Riot Games merch auto-checkout bot")
    sub = parser.add_subparsers(dest="command", required=True)

    login_parser = sub.add_parser("login", help="Open a browser for one-time Riot SSO login")
    _add_config_arg(login_parser)
    login_parser.add_argument("--timeout", type=int, default=600, help="Seconds to wait for login")
    login_parser.set_defaults(func=_cmd_login)

    check_parser = sub.add_parser("check", help="Dry-run: report the product stock state without buying")
    _add_config_arg(check_parser)
    check_parser.set_defaults(func=_cmd_check)

    resolve_parser = sub.add_parser("resolve", help="List product URLs found on a launch/listing page")
    _add_config_arg(resolve_parser)
    resolve_parser.add_argument("--url", default=None, help="Listing URL (defaults to product_url)")
    resolve_parser.set_defaults(func=_cmd_resolve)

    sizes_parser = sub.add_parser("sizes", help="List available sizes without buying")
    _add_config_arg(sizes_parser)
    sizes_parser.set_defaults(func=_cmd_sizes)

    serve_parser = sub.add_parser("serve", help="Run the webhook server")
    _add_config_arg(serve_parser)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=5001)
    serve_parser.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
