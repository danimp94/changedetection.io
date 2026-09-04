"""FastAPI webhook server that triggers a purchase when changedetection.io alerts us."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .config import BuyConfig


class PurchaseManager:
    """Single-flight guard around the checkout routine.

    NOTE: this guard is per-process. Run uvicorn with ``--workers 1``,
    otherwise N workers allow N concurrent checkouts (double-buy).
    """

    def __init__(self, config: BuyConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._active = False
        self._started_at: float | None = None
        self._ordered_at: float | None = None
        self.last_result: object = None
        self.last_error: str | None = None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def _stale_timeout(self) -> float:
        try:
            return float(self.config.checkout_timeout_seconds) + 60.0
        except Exception:
            return 120.0

    def try_start(self) -> bool:
        import time as _time

        with self._lock:
            if self._ordered_at is not None:
                return False
            if self._active:
                # Watchdog: a hung Playwright run must not brick the sniper.
                if self._started_at is not None and (_time.time() - self._started_at) > self._stale_timeout():
                    self._active = False
                    self._started_at = None
                    self.last_error = "stale checkout auto-cleared by watchdog"
                else:
                    return False
            self._active = True
            import time as _time2

            self._started_at = _time2.time()
            return True

    def reset(self) -> None:
        with self._lock:
            self._active = False
            self._started_at = None
            self._ordered_at = None
            self.last_result = None
            self.last_error = None

    @property
    def completed_successfully(self) -> bool:
        with self._lock:
            return self._ordered_at is not None

    def run(self, run_purchase: Callable[[BuyConfig], object]) -> None:
        # try_start() already set _active; run holds no lock during purchase
        # so a long safety_mode manual wait never blocks the server thread.
        # Mutual exclusion is via the _active flag checked in try_start and
        # cleared here; plus the Chromium profile SingletonLock as backstop.
        import logging as _logging
        import time as _time

        try:
            result = run_purchase(self.config)
            with self._lock:
                self.last_result = result
                self.last_error = None
                if getattr(result, "stage", None) == "ordered":
                    # Success latch: refuse further auto-buys until /reset.
                    self._ordered_at = _time.time()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.last_result = None
                self.last_error = f"{type(exc).__name__}: {exc}"
            _logging.getLogger("buybot.webhook").exception("checkout failed")
            # Swallow after recording: BackgroundTasks would only log noisy tracebacks.
        finally:
            with self._lock:
                self._active = False
                self._started_at = None


def _result_to_json(result: object) -> object:
    import dataclasses

    if dataclasses.is_dataclass(result):
        return dataclasses.asdict(result)
    if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
        return result
    return str(result)


def default_run_purchase(config: BuyConfig) -> object:
    """Open a persistent browser session and run the checkout.

    In safety_mode the browser is closed promptly after reaching checkout and
    NO long sleep holds the worker: the operator reopens the profile to pay
    manually (cart/session persist in the persistent profile). This avoids a
    15-minute blind window where a false ping blocks the real drop.
    Alerts fire AFTER the browser closes so slow webhooks never hold the profile lock.
    """
    from .browser import BrowserSession
    from .buyer import run_checkout
    from .notifier import send_alerts

    result: object = None
    error: Exception | None = None
    with BrowserSession(
        config.profile_dir, headless=config.headless, cdp_url=config.cdp_url, locale=config.locale
    ) as session:
        page = session.new_page()
        try:
            result = run_checkout(page, config)
        except Exception as exc:  # noqa: BLE001
            error = exc
    # Browser closed here — now alert without holding the profile.
    if error is not None:
        send_alerts(config, f"Buybot checkout failed: {error}")
        raise error
    assert result is not None
    send_alerts(config, f"Buybot: {result.message}")  # type: ignore[attr-defined]
    if config.safety_mode and getattr(result, "stage", None) == "awaiting_payment":
        send_alerts(
            config,
            "Stopped at payment — reopen the profile to complete it manually "
            "(browser was closed to free the sniper for the next alert).",
        )
    return result


def create_app(
    config: BuyConfig,
    run_purchase: Callable[[BuyConfig], object] = default_run_purchase,
) -> FastAPI:
    """Build the FastAPI app; ``run_purchase`` is injectable for tests."""
    manager = PurchaseManager(config)
    app = FastAPI(title="Riot merch buybot")
    app.state.manager = manager

    def _check_secret(x_buybot_secret: str | None) -> None:
        expected = config.webhook_secret_value
        if expected and not secrets.compare_digest(x_buybot_secret or "", expected):
            raise HTTPException(status_code=403, detail="invalid secret")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status(x_buybot_secret: str | None = Header(default=None)) -> dict[str, object]:
        _check_secret(x_buybot_secret)
        with manager._lock:
            last_result = manager.last_result
            last_error = manager.last_error
            ordered = manager._ordered_at is not None
        return {
            "active": manager.is_active,
            "ordered": ordered,
            "last_result": _result_to_json(last_result),
            "last_error": last_error,
        }

    def _enqueue() -> None:
        if not manager.try_start():
            if manager.completed_successfully:
                raise HTTPException(status_code=409, detail="order already placed; POST /reset to re-arm")
            raise HTTPException(status_code=409, detail="checkout already in progress")

    @app.post("/buy")
    async def buy(
        background_tasks: BackgroundTasks,
        x_buybot_secret: str | None = Header(default=None),
    ) -> JSONResponse:
        # Lenient trigger for changedetection.io apprise json:// which posts
        # varying bodies. Use /buy-json below when URL/SKU validation is wanted.
        _check_secret(x_buybot_secret)
        import logging as _logging

        _logging.getLogger("buybot.webhook").debug("buy trigger accepted")
        _enqueue()
        try:
            background_tasks.add_task(manager.run, run_purchase)
        except Exception:
            manager.reset()
            raise
        return JSONResponse(status_code=202, content={"status": "accepted"})

    @app.post("/reset")
    def reset(x_buybot_secret: str | None = Header(default=None)) -> dict[str, str]:
        """Re-arm after a latched `ordered` state."""
        _check_secret(x_buybot_secret)
        manager.reset()
        return {"status": "reset"}

    @app.post("/buy-json")
    def buy_json(
        payload: dict,
        background_tasks: BackgroundTasks,
        x_buybot_secret: str | None = Header(default=None),
    ) -> JSONResponse:
        """Strict variant that validates the changedetection payload URL/SKU."""
        _check_secret(x_buybot_secret)
        for key in ("url", "watch_url", "product_url"):
            if key in payload and payload[key] and payload[key] != config.product_url:
                raise HTTPException(status_code=400, detail=f"URL mismatch for {key}")
        if "sku" in payload and payload["sku"] and config.sku and payload["sku"] != config.sku:
            raise HTTPException(status_code=400, detail="SKU mismatch")
        _enqueue()
        try:
            background_tasks.add_task(manager.run, run_purchase)
        except Exception:
            manager.reset()
            raise
        return JSONResponse(status_code=202, content={"status": "accepted"})

    return app
