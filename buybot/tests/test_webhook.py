from __future__ import annotations

from fastapi.testclient import TestClient

from buybot.config import BuyConfig
from buybot.webhook import create_app


def test_health(sample_config: BuyConfig) -> None:
    client = TestClient(create_app(sample_config))
    assert client.get("/health").json() == {"status": "ok"}


def test_buy_accepted_and_runs_purchase(sample_config: BuyConfig) -> None:
    calls: list[str] = []

    def fake_purchase(config: BuyConfig) -> str:
        calls.append(config.product_url)
        return "ordered"

    client = TestClient(create_app(sample_config, run_purchase=fake_purchase))
    response = client.post("/buy")
    assert response.status_code == 202
    assert calls == [sample_config.product_url]
    status = client.get("/status").json()
    assert status["active"] is False
    assert status["last_result"] == "ordered"


def test_secret_enforced(sample_config: BuyConfig) -> None:
    config = sample_config.model_copy(update={"webhook_secret": "s3cret"})
    client = TestClient(create_app(config, run_purchase=lambda c: "ok"))

    assert client.post("/buy").status_code == 403
    assert client.post("/buy", headers={"X-Buybot-Secret": "wrong"}).status_code == 403
    assert client.post("/buy", headers={"X-Buybot-Secret": "s3cret"}).status_code == 202


def test_single_flight_guard(sample_config: BuyConfig) -> None:
    app = create_app(sample_config, run_purchase=lambda c: "ok")
    manager = app.state.manager
    assert manager.try_start() is True
    client = TestClient(app)
    assert client.post("/buy").status_code == 409
    manager.run(lambda c: "done")
    assert client.post("/buy").status_code == 202


def test_status_requires_secret_when_configured(sample_config: BuyConfig) -> None:
    config = sample_config.model_copy(update={"webhook_secret": "s3cret"})
    client = TestClient(create_app(config, run_purchase=lambda c: "ok"))
    assert client.get("/status").status_code == 403
    assert client.get("/status", headers={"X-Buybot-Secret": "s3cret"}).status_code == 200


def test_status_serializes_dataclass_result(sample_config: BuyConfig) -> None:
    from buybot.buyer import CheckoutResult

    client = TestClient(
        create_app(sample_config, run_purchase=lambda c: CheckoutResult(stage="ordered", message="Order placed."))
    )
    assert client.post("/buy").status_code == 202
    status = client.get("/status").json()
    assert status["last_result"] == {"stage": "ordered", "message": "Order placed."}
    assert status["last_error"] is None


def test_buy_json_validates_url_and_sku(sample_config: BuyConfig) -> None:
    client = TestClient(create_app(sample_config, run_purchase=lambda c: "ok"))
    assert client.post("/buy-json", json={"url": "https://other.example/x"}).status_code == 400
    assert client.post("/buy-json", json={"sku": "WRONG-SKU"}).status_code == 400
    assert client.post("/buy-json", json={"url": sample_config.product_url}).status_code == 202
    assert client.post("/buy-json", json={}).status_code == 202


def test_ordered_latches_until_reset(sample_config: BuyConfig) -> None:
    from buybot.buyer import CheckoutResult

    app = create_app(
        sample_config,
        run_purchase=lambda c: CheckoutResult(stage="ordered", message="Order placed."),
    )
    client = TestClient(app)
    assert client.post("/buy").status_code == 202
    assert client.post("/buy").status_code == 409
    assert client.post("/reset").status_code == 200
    assert client.post("/buy").status_code == 202


def test_failed_run_does_not_latch(sample_config: BuyConfig) -> None:
    def boom(config: BuyConfig) -> str:
        raise RuntimeError("nope")

    app = create_app(sample_config, run_purchase=boom)
    client = TestClient(app)
    assert client.post("/buy").status_code == 202
    status = client.get("/status").json()
    assert status["active"] is False
    assert "nope" in (status["last_error"] or "")
    # Failed runs must not latch — next trigger is accepted.
    assert client.post("/buy").status_code == 202
