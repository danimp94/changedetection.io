"""Browser session management.

Two modes are supported:

- **Persistent profile** (default): launch a dedicated Chromium with its own
  ``user_data_dir`` so a Riot SSO login persists across runs (set up via ``buybot login``).
- **CDP attach**: connect to an already-running Chrome (the user's own session) via
  ``connect_over_cdp``. Set ``cdp_url`` in ``config.yaml``, e.g.
  ``http://127.0.0.1:9222``. In this mode the bot never closes the user's browser.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright


class BrowserSession:
    """Context manager wrapping either a persistent Chromium profile or a CDP attach."""

    def __init__(
        self,
        profile_dir: str | Path | None = None,
        headless: bool = True,
        cdp_url: str | None = None,
        channel: str | None = None,
        locale: str = "es-ES",
    ) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.headless = headless
        self.cdp_url = cdp_url
        self.channel = channel
        self.locale = locale
        self._pw = None
        self._context: BrowserContext | None = None
        self._browser = None
        self._owns_browser = False
        self._owns_context = False
        self._pages: list[Page] = []

    def __enter__(self) -> BrowserSession:
        self._pw = sync_playwright().start()
        try:
            if self.cdp_url:
                self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
                if self._browser.contexts:
                    # Prefer a context that already has the product open, else first.
                    self._context = self._browser.contexts[0]
                else:
                    self._context = self._browser.new_context(locale=self.locale)
                    self._owns_context = True
                self._owns_browser = False
            else:
                if self.profile_dir is None:
                    raise ValueError("profile_dir is required when cdp_url is not set")
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                kwargs: dict = dict(
                    user_data_dir=str(self.profile_dir),
                    headless=self.headless,
                    viewport={"width": 1280, "height": 900},
                    locale=self.locale,
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                )
                # channel=None uses Playwright's bundled Chromium (works with
                # `playwright install chromium`); only set when explicitly wanted.
                if self.channel:
                    kwargs["channel"] = self.channel
                try:
                    self._context = self._pw.chromium.launch_persistent_context(**kwargs)
                except Exception as exc:
                    msg = str(exc)
                    if "SingletonLock" in msg or "already" in msg.lower():
                        raise RuntimeError(
                            f"Browser profile {self.profile_dir} is already in use; "
                            "close the other browser or wait for the active checkout to finish."
                        ) from exc
                    raise
                self._owns_browser = True
        except Exception:
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        for page in self._pages:
            try:
                page.close()
            except Exception:
                pass
        if self._owns_browser and self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
        elif self._owns_context and self._context is not None:
            # We created this CDP context; clean it up. Otherwise leave the
            # user's own browser contexts alone.
            try:
                self._context.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserSession is not open; use it as a context manager")
        return self._context

    def new_page(self) -> Page:
        page = self.context.new_page()
        self._pages.append(page)
        return page
