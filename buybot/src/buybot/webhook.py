"""FastAPI webhook server that triggers a purchase when changedetection.io alerts us."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .config import BuyConfig


class PurchaseManager:
    """Single-flight guard around the checkout routine."""

    def __init__(self, config: BuyConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._active = False
        self.last_result = None
        self.last_error: str | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    def try_start(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self._active = True
        return True

    def run(self, run_purchase: Callable[[BuyConfig], object]) -> None:
        try:
            self.last_result = run_purchase(self.config)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise
        finally:
            self._active = False
            self._lock.release()


def default_run_purchase(config: BuyConfig) -> object:
    """Open a persistent browser session and run the checkout."""
    import time

    from .browser import BrowserSession
    from .buyer import run_checkout
    from .notifier import send_alerts

    with BrowserSession(config.profile_dir, headless=config.headless, cdp_url=config.cdp_url) as session:
        page = session.new_page()
        try:
            result = run_checkout(page, config)
            send_alerts(config, f"Buybot: {result.message}")
            if config.safety_mode and getattr(result, "stage", None) == "awaiting_payment":
                send_alerts(config, "Stopped at payment — complete it manually in the open browser window.")
                time.sleep(config.manual_completion_timeout_seconds)
            return result
        except Exception as exc:  # noqa: BLE001
            send_alerts(config, f"Buybot checkout failed: {exc}")
            raise


def create_app(
    config: BuyConfig,
    run_purchase: Callable[[BuyConfig], object] = default_run_purchase,
) -> FastAPI:
    """Build the FastAPI app; ``run_purchase`` is injectable for tests."""
    manager = PurchaseManager(config)
    app = FastAPI(title="Riot merch buybot")
    app.state.manager = manager

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict[str, object]:
        return {
            "active": manager.is_active,
            "last_result": manager.last_result,
            "last_error": manager.last_error,
        }

    @app.post("/buy")
    def buy(
        background_tasks: BackgroundTasks,
        x_buybot_secret: str | None = Header(default=None),
    ) -> JSONResponse:
        expected = config.webhook_secret_value
        if expected and not secrets.compare_digest(x_buybot_secret or "", expected):
            raise HTTPException(status_code=403, detail="invalid secret")

        if not manager.try_start():
            raise HTTPException(status_code=409, detail="checkout already in progress")

        background_tasks.add_task(manager.run, run_purchase)
        return JSONResponse(status_code=202, content={"status": "accepted"})

    return app
