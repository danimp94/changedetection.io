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
        channel: str = "chrome",
    ) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.headless = headless
        self.cdp_url = cdp_url
        self.channel = channel
        self._pw = None
        self._context: BrowserContext | None = None
        self._browser = None
        self._owns_browser = False
        self._pages: list[Page] = []

    def __enter__(self) -> BrowserSession:
        self._pw = sync_playwright().start()
        if self.cdp_url:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            self._owns_browser = False
        else:
            if self.profile_dir is None:
                raise ValueError("profile_dir is required when cdp_url is not set")
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel=self.channel,
                headless=self.headless,
                viewport={"width": 1280, "height": 900},
                locale="es-ES",
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            self._owns_browser = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self._owns_browser:
            for page in self._pages:
                try:
                    page.close()
                except Exception:
                    pass
            if self._context is not None:
                self._context.close()
        else:
            for page in self._pages:
                try:
                    page.close()
                except Exception:
                    pass
        if self._pw is not None:
            self._pw.stop()

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserSession is not open; use it as a context manager")
        return self._context

    def new_page(self) -> Page:
        page = self.context.new_page()
        self._pages.append(page)
        return page
