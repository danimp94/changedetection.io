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
| `buybot/webhook.py` | FastAPI server: `POST /buy` + `POST /buy-json` triggers, single-flight guard (single worker) |
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
   JSON filter that only fires when the product flips to in-stock. Riot embeds
   `"sku":"RB3864-00-00",...,"availability":"outOfStock"` server-side; adidas uses
   JSON-LD `"availability":"https://schema.org/InStock"` — both are understood.
2. Set a sensible recheck interval (30–60s; 10–20s on launch day) and a proxy to avoid bans.
3. Add a notification URL that POSTs to the buybot, e.g. the apprise **JSON custom** handler:

   ```
   json://127.0.0.1:5001/buy
   ```

   If `webhook_secret` is set in `config.yaml` (recommended), configure changedetection.io
   to send an `X-Buybot-Secret` header with the same value. `serve` warns if unset.

4. Strict variant: `POST /buy-json` validates the payload — send
   `{"url": "<product_url>", "sku": "<sku>"}` and mismatches get `400` instead of a
   wasted browser launch. `/buy` stays lenient for apprise bodies.
5. The server runs with `workers=1` internally — the single-flight guard is per-process.
   A second trigger while one is active gets `409 checkout already in progress`.
   `GET /status` (same secret header) shows `active`, `last_result`, `last_error`.

## Safety

- **Default is `safety_mode: true`**: the bot adds to cart, reaches checkout, fills shipping, and
  then *stops* before payment, alerting you to complete the purchase manually.
- Only set `safety_mode: false` and supply `payment` details if you accept storing card data and
  fully automating payment.
- Polling or auto-purchasing limited merch may violate the store's Terms of Service; use responsibly
  and at your own risk.

## Anti-ban tips (no ban-free guarantee, esp. adidas/Akamai)

1. **Go slow**: recheck 30–60s (10–20s launch day only); single worker, one browser per profile.
2. **Look human**: `login` once into a persistent profile and reuse it; prefer `cdp_url` with your
   own Chrome; keep `locale`/region consistent with the store.
3. **Avoid datacenter fingerprints**: use a residential IP/proxy on launch day, add recheck jitter,
   and stop polling after `ordered` (the server latches until `POST /reset`).
4. **Back off on blocks**: `unknown` state, HTTP 403, or captcha means pause the watch — inspect
   `artifacts/*.png` and `GET /status` (`last_error`) instead of retrying harder.
5. **Stay in `safety_mode: true`** until frame-aware payment lands; full auto-pay multiplies ban and
   double-charge risk.

## Tests

```powershell
& buybot/.venv/Scripts/python.exe -m pytest buybot/tests -q
& buybot/.venv/Scripts/python.exe -m ruff check buybot/src buybot/tests
```

## How detection works

The Riot storefront is a Next.js SPA. Availability is embedded server-side as
`"sku":"...","availability":"inStock|outOfStock"`, so the bot prefers rendered DOM
(`page.content()`, falling back to `response.text()`) and, when `sku` is set in
`config.yaml`, scopes detection to that product (bounded by neighbouring SKUs, both key
orders). Generic JSON-LD `https://schema.org/InStock|OutOfStock|LimitedAvailability|…`
is understood too. This matters because the page keeps a `COMPRAR AHORA` element in the
DOM even when sold out (and shows related in-stock items), so scanning visible text alone
false-positives.

Visible text is only a fallback when no SKU JSON is found, and conflicting buy+OOS markers
yield `UNKNOWN` (raising `UnknownStateError`, distinct from `OutOfStockError`) instead of a
false-positive buy. To sanity-check detection against any other product (e.g. adidas),
point `config.yaml` at it, set its `sku`, and run `buybot check`.

Note: until you run `buybot login`, the primary CTA reads *"Inicia sesión para comprar"* rather
than *"Comprar ahora"*, and the bot aborts with `LoginRequiredError` instead of buying
(login is checked before stock so an expired SSO isn't masked as OOS).

