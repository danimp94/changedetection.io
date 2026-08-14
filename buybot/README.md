# Riot Merch Buybot

Auto-checkout component that pairs with [changedetection.io](https://changedetection.io) to buy
a Riot Games merch product the moment it comes back in stock.

changedetection.io **detects** the restock and **notifies**; this bot **buys**. The two are kept
as separate components:

- `changedetection.io` (this repo, root) — watches the product page and fires a webhook on the
  `outOfStock → inStock` transition.
- `buybot/` (this folder) — receives the webhook and runs a Playwright checkout using a
  persistent, logged-in Riot SSO profile.

## Components

| Module | Responsibility |
| --- | --- |
| `buybot/config.py` | YAML config loading + validation (pydantic) |
| `buybot/signals.py` | Pure stock-detection functions (unit-testable, no I/O) |
| `buybot/browser.py` | Persistent Playwright Chromium session (keeps SSO login) |
| `buybot/buyer.py` | Checkout steps against a minimal page interface |
| `buybot/notifier.py` | Telegram / Discord / generic JSON status alerts |
| `buybot/webhook.py` | FastAPI server: `POST /buy` trigger + single-flight guard |
| `buybot/cli.py` | `login`, `check`, `serve` subcommands |

## Setup

```powershell
# 1. Create a dedicated virtualenv and install the bot + Playwright browser
py -3.13 -m venv buybot/.venv
& buybot/.venv/Scripts/python.exe -m pip install -e buybot
& buybot/.venv/Scripts/python.exe -m playwright install chromium

# 2. Configure
Copy-Item buybot/config.example.yaml buybot/config.yaml
# edit buybot/config.yaml (product URL, shipping, alerts, secret)

# 3. One-time login (opens a visible browser — sign in to Riot SSO)
& buybot/.venv/Scripts/python.exe -m buybot.cli login -c buybot/config.yaml

# 4. Dry-run: check current stock state without buying
& buybot/.venv/Scripts/python.exe -m buybot.cli check -c buybot/config.yaml

# 5. Start the webhook server
& buybot/.venv/Scripts/python.exe -m buybot.cli serve -c buybot/config.yaml --port 5001
```

The webhook is now reachable at `http://127.0.0.1:5001/buy`.

## Wire up changedetection.io

1. Add a watch for the product URL. Use the **Restock** processor, or a **Trigger on text** /
   JSON filter that only fires when the product flips to in-stock (the page embeds
   `"sku":"RB3864-00-00",...,"availability":"outOfStock"` server-side).
2. Set a sensible recheck interval (30–60s; 10–20s on launch day) and a proxy to avoid bans.
3. Add a notification URL that POSTs to the buybot, e.g. the apprise **JSON custom** handler:

   ```
   json://127.0.0.1:5001/buy
   ```

   If `webhook_secret` is set in `config.yaml`, configure changedetection.io to send an
   `X-Buybot-Secret` header with the same value.

## Safety

- **Default is `safety_mode: true`**: the bot adds to cart, reaches checkout, fills shipping, and
  then *stops* before payment, alerting you to complete the purchase manually.
- Only set `safety_mode: false` and supply `payment` details if you accept storing card data and
  fully automating payment.
- Polling or auto-purchasing limited merch may violate Riot's Terms of Service; use responsibly
  and at your own risk.

## Tests

```powershell
& buybot/.venv/Scripts/python.exe -m pytest buybot/tests -q
& buybot/.venv/Scripts/python.exe -m ruff check buybot/src buybot/tests
```

## How detection works

The storefront is a Next.js SPA. Availability is embedded server-side as
`"sku":"...","availability":"inStock|outOfStock"`, so the bot reads the raw HTTP response
(`response.text()`) and, when `sku` is set in `config.yaml`, scopes detection to that product.
This matters because the page keeps a `COMPRAR AHORA` element in the DOM even when sold out
(and shows related in-stock items), so scanning visible text alone false-positives.

Visible text is only a fallback when no SKU JSON is found. To sanity-check detection against
any other product, point `config.yaml` at it, set its `sku`, and run `buybot check`.

Note: until you run `buybot login`, the primary CTA reads *"Inicia sesión para comprar"* rather
than *"Comprar ahora"*, and the bot aborts with `LoginRequiredError` instead of buying.

