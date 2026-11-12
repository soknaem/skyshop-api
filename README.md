# SkyShop: electronics shop with KHQR (microservices)

One project for two subjects:

| Subject | What satisfies it |
|---|---|
| **Python (microservices)** | 7 separate FastAPI services (**gateway, auth, operation, report, media, chat, notify**), each with its own PostgreSQL database, talking over HTTP and WebSocket. Only the gateway is public. |
| **E-commerce** | Next.js 15 **SSR** storefront + admin, Python API, **KHQR** payment, categories, filters, wishlist, pickup/delivery, live chat, Facebook/Telegram share. |

## Architecture

```
Browser ──► Next.js (SSR, :3000)
              │  server-side fetch + server actions   (browser WebSocket / images / uploads go to the gateway)
              ▼
        API Gateway (:8000)  ◄── the only public API entry
   ┌────────┬─────────┬─────────┬─────────┬─────────┬─────────┐
   ▼        ▼         ▼         ▼         ▼         ▼
 Auth    Operation   Report    Media     Chat     Notify
 :8001   :8002       :8003     :8004     :8005    :8006
 auth_db ops_db      report_db media_db  chat_db  notify_db   (one Postgres, 6 databases)
          │  └─ KHQR ─► Bakong / khqr.dev
          └─ outbox events "order.paid" / "order.updated" ─► Report (live feed to admin)
```

| Service | Responsibility |
|---|---|
| **Gateway** | Routes `/api/auth`, `/api/ops`, `/api/reports`, `/api/media`; serves `/media/*` images; verifies the JWT (RS256, public key only) and the role; request ID, rate limit, CORS, uniform error JSON, aggregated `/health`; proxies the 4 WebSockets. |
| **Auth** | Customer register/login, admin login, **Google / Facebook / Telegram login**, refresh-token rotation, logout (blacklist), profile. |
| **Operation** | Categories, products (filters, sort, facets, related), user carts, wishlist, checkout (pickup or delivery), orders, KHQR payments, stock, popularity, outbox. |
| **Report** | Receives order events, keeps its own copy of paid orders, serves order history, dashboard numbers, and a live admin feed. |
| **Media** | Image upload: validates it is a real image, fixes rotation, resizes, converts to WEBP, makes a thumbnail, serves it with long cache headers. |
| **Notify** | In-app notifications (stored, pushed live over WebSocket) and **Telegram messages** for important operations. Other services send it events; it never blocks them. |
| **Chat** | Live support over WebSocket: customers (logged in or guests) ↔ admins, history, unread counts, typing indicator, online status, offline notice. |

## What is new in version 3

**Look and accessibility**
- **Dark mode** (Settings → Appearance): Light, Dark or System (follows the device, including automatic night mode). A sun/moon button is in the header on desktop and phone. The choice is applied before the first paint, so there is no white flash.
- **Text size**: Small, Default or Large scales the whole interface.
- **Desktop notifications (opt-in)**: when the site is in a background tab, a new notification appears as a system notification; clicking it opens the right page. The browser asks permission when you tick the box.
- **Skeleton loaders** (grey card shapes) on product lists, account, wishlist and admin lists; **Back to top** button; press **`/`** to jump to search and **Esc** to leave it.
- **Accessibility**: visible keyboard focus everywhere, "Skip to main content" link, animations switch off when the operating system asks for reduced motion.

**Accounts and security**
- **Settings** page (`/settings`): profile (name, phone, **profile picture**), security, appearance, notifications. Admins have the same under `/admin/settings`.
- **Change password** (for accounts created with email and password): checks the current password, other devices are signed out, this one stays signed in.
- **Forgot password**: emailed link that works once and expires after 30 minutes. Only a hash of the link is stored. The answer is the same for unknown emails, so nobody can find out who has an account.
- Passwords need at least 8 characters, with a strength meter. Access tokens now last 15 minutes (refreshed silently), so disabling a user or changing a password takes effect quickly.

**Admin user management** (`/admin/users`): summary cards, search and filters (role, status, sign-in method), create users, edit details, make admin or customer, **disable/enable**, reset password (temporary password shown once), delete. You cannot disable, demote or delete yourself, nor the last admin.

**Notifications and Telegram alerts**
- A **bell** with unread count and a list: customers see "Payment received", "Your order is ready for pickup"…; admins see "New paid order", "Low stock", "New customer"…
- **Telegram alerts to the shop owner** for important operations: new paid order, low stock, new customer, admin sign-in, admin password change or reset, user management actions, product or category created or deleted, and a customer writing in the chat while no admin is online. Customers who logged in with Telegram can also get order updates as Telegram messages (they can switch it off).

### Telegram alerts setup (2 minutes)
1. In Telegram, **@BotFather** → `/newbot` → put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Open your bot, press Start, send **`/id`**. The bot answers with your chat id.
3. Put it in `TELEGRAM_ALERT_CHAT_IDS` (several people: comma separated). Restart `start_all.py`.
4. Admin → Settings → Telegram alerts → **Send a test message**.

### Forgot-password email (SMTP, optional)
Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` in `.env` (for Gmail use an app password). Without SMTP, the reset link is printed in the **auth** window of `start_all.py`. For a classroom demo you can set `DEV_RETURN_RESET_LINK=true`, which also shows the link on the page. **Never use that setting on a real website.**

### Upgrading from version 2
Run `pip install -r requirements.txt` and `npm install`, copy the new settings from `.env.example` into your `.env`, and start as usual. Tables are upgraded automatically and `notify_db` is created for you. Existing accounts keep working. Passwords you set earlier stay valid; only new or changed passwords need 8 characters.

## Features

**Customer**
- Modern responsive UI (phone, tablet, desktop): bottom tab bar on phones, filter sheet, scroll animations, skeleton loading.
- Home with hero, categories, popular, hot deals, new arrivals. Instant search with suggestions.
- Product list: **category, brand, price range, in stock, on sale, sort** (popular, newest, price, discount), active-filter chips, pagination.
- Product page: gallery with zoom, price and discount, stock, specifications table, warranty, share (Facebook, Telegram, copy link), related products, Open Graph + JSON-LD for SEO. **The whole product card is clickable.**
- **Browsing is open to everyone; buying needs a login** (cart, checkout, wishlist, orders).
- Login/sign-up with email, **Google, Facebook, Telegram**.
- Checkout: **Pickup in store** (asks for nothing) or **Delivery** (phone + address required; name and email optional). KHQR payment page with live status.
- Order confirmation with progress timeline (preparing, ready/out for delivery, completed). My orders, wishlist, profile.
- **Live chat** bubble on every page.
- **Toast messages** for every result (no inline message boxes).

**Admin** (`/admin`)
- Dashboard: revenue, orders, average, pickup vs delivery, 14-day chart, top products, latest orders, all live.
- Products: add/edit/delete, **image upload (drag and drop, several images, choose main picture)** or paste a link, category, brand, old price (discount), stock, warranty, popular flag, specifications editor.
- Categories: add/edit/delete with icon picker and order.
- Orders: filter by pickup/delivery/status, **click any row**, update order status (customer sees it instantly).
- **Live chat inbox** with unread badges, typing indicator, resolve button. Toast + badge when a customer writes or pays.

## Setup (Windows, PyCharm or VS Code)

Requirements: Python 3.12, Node.js 20+, Docker Desktop.

```bash
# 1. Database (Postgres in Docker, host port 5433)
docker compose up -d

# 2. Python environment (project root)
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 3. Settings
copy .env.example .env            # macOS/Linux: cp .env.example .env

# 4. Start the 7 services together
python scripts/start_all.py

# 5. Frontend (new terminal)
cd frontend
copy .env.example .env.local      # macOS/Linux: cp .env.example .env.local
npm install
npm run dev
```

Open **http://localhost:3000** (use `localhost`, not `127.0.0.1`, so cookies and CORS match).
Admin: http://localhost:3000/admin/login → `admin` / `admin123` (change in `.env`).
Swagger per service: :8000/docs (gateway), :8001 (auth), :8002 (operation), :8003 (report), :8004 (media), :8005 (chat), :8006 (notify).

**PyCharm:** open the root folder, set the interpreter to `.venv`, add a Run configuration → Python → script `scripts/start_all.py`.
**VS Code:** pick the `.venv` interpreter; the Run panel has "All Python services" and one entry per service.

### Upgrading from the first version
The services migrate their tables automatically on start, and the missing databases (`media_db`, `chat_db`) are created for you, so your old products and orders stay. Old products have no category; assign them in Admin → Products.
For a clean start with the new electronics demo catalogue (23 products, 8 categories), **stop the services** and run:
```bash
python scripts/reset_db.py        # deletes ALL shop data, type YES to confirm
```
Then start the services again; the demo data is created on first start.

## Settings you will want to change (`.env`)

| Setting | Meaning |
|---|---|
| `SHOP_CURRENCY` | `KHR` (prices like 120,000៛) or `USD`. Seed prices adapt. |
| `MOCK_MODE` | `true` = fake QR + "Simulate payment" button (demo). `false` = real KHQR. |
| `KHQR_TOKEN`, `KHQR_ACCOUNT_ID`, `KHQR_MERCHANT_NAME`… | Real KHQR. NBC token (`eyJ…`) needs a Cambodian IP; KHQR.dev token (`rbk…`) works anywhere. Use **your own** Bakong account for the final submission. |
| `SHOP_ADDRESS`, `SHOP_HOURS` | Shown for pickup. |
| `DELIVERY_FEE` | In shop currency. `0` = free delivery. |
| `ADMIN_PASSWORD` | First admin password (only used when the admin is first created). |

## Social login setup (optional, each is independent)

The buttons are always shown; if a provider is not configured, clicking it shows a friendly message. Callback addresses use `SITE_URL` (default `http://localhost:3000`).

**Google**
1. https://console.cloud.google.com → APIs & Services → Credentials → Create OAuth client ID → *Web application*.
2. Authorised redirect URI: `http://localhost:3000/auth/callback/google`
3. Put the values in `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. Restart `start_all.py`.

**Facebook**
1. https://developers.facebook.com → Create app → add *Facebook Login* → Settings.
2. Valid OAuth Redirect URI: `http://localhost:3000/auth/callback/facebook`
3. `.env`: `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET`. (While the app is in Development mode only you and added testers can log in.)

**Telegram** (works on localhost, no domain needed)
1. In Telegram open **@BotFather** → `/newbot` → copy the token and the bot username.
2. `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (without the @).
3. How it works: the site gives a one-time link `t.me/<bot>?start=<token>`; the customer presses START; the auth service (long-polling the Telegram Bot API) sees it and signs the customer in on the website. The server needs internet access to `api.telegram.org`.

Accounts are linked by email only when Google says the email is verified, and never to the admin account.

## Tests

```bash
python scripts/smoke_test.py          # whole backend through the gateway (services running, MOCK_MODE=true)
python scripts/smoke_test_v3.py       # passwords, user management, notifications, avatars (needs DEV_RETURN_RESET_LINK=true)
python scripts/test_telegram_alerts.py # who receives which Telegram message (Telegram replaced by a recorder)
python scripts/ui_flow_test.py        # the whole website (needs `npm run build` + `npm run start` running)
python scripts/test_social_login.py   # Google/Facebook/Telegram server logic with simulated provider answers
cd frontend && npm run lint           # TypeScript check
```

## API (all through the gateway, port 8000)

| Area | Endpoints |
|---|---|
| Public (no login) | `GET /api/ops/products` (`q, category, brand, min_price, max_price, in_stock, on_sale, featured, sort, page`), `/products/facets`, `/products/{id}`, `/products/{id}/related`, `/categories`, `/config`; `GET /media/{file}`; `POST /api/auth/register`, `/login`, `/refresh`, `/logout`; `GET /api/auth/providers` |
| Customer (login) | `GET/POST/PATCH/DELETE /api/ops/cart…`, `POST /checkout`, `GET /orders/{no}`, `GET /my/orders`, `/wishlist…`, `GET /payments/{id}/status`, `POST /payments/{id}/regenerate`, `POST /payments/{id}/mock-pay` (demo), `GET/PATCH /api/auth/me` |
| Admin | `/api/ops/admin/products…`, `/admin/categories…`, `PATCH /admin/orders/{no}/status`; `/api/reports/orders…`, `/summary`, `/sales-by-day`, `/top-products`; `POST /api/media/upload`, `GET /api/media/images` |
| Accounts | `POST /api/auth/forgot-password`, `/reset-password`, `GET /reset-password/check`; customer: `POST /api/auth/change-password`, `PATCH /api/auth/me`, `POST /api/media/avatar`; admin: `/api/auth/admin/users…` (list, summary, create, edit, reset password, delete) |
| Notifications | `GET /api/notify/notifications`, `POST /notifications/{id}/read`, `/read-all`, `DELETE /notifications`, `GET/PUT /preferences`; admin: `GET /admin/telegram-status`, `POST /admin/telegram-test` |
| WebSocket | `/ws/payments/{id}`, `/ws/chat?token=&guest=`, `/ws/notifications?token=`, `/ws/admin/orders?token=`, `/ws/admin/chat?token=` |

Errors always look like `{"error": {"code", "message", "request_id"}}`.

## Good to know

- **Buying needs a login.** To allow guest buying again you would remove `cart`, `checkout`, … from `OPS_LOGIN_REQUIRED` in `gateway/gateway_app/main.py` and make carts anonymous (not built in this version).
- **Share previews** on Facebook/Telegram need a public https address (not localhost) and PNG/JPG images. Demo pictures are SVG; images you upload are WEBP, which Facebook supports.
- **Stock** is deducted when payment arrives, not reserved at checkout.
- **Uploaded images** are stored on disk in `media_service/storage/`; back that folder up with the database.
- **Chat** connects when a visitor first opens the bubble. Admins appear "online" while any admin page is open.
- Tokens travel in WebSocket URLs (`?token=`) because browsers cannot set headers there; the gateway's access log is switched off for that reason (it logs its own safe lines).
- Do **not** commit `.env` (it holds your KHQR token and OAuth secrets).

## Folders

```
gateway/ auth_service/ ops_service/ report_service/ media_service/ chat_service/ notify_service/   FastAPI services
frontend/          Next.js app (app/ pages, components/, lib/ actions + API helpers)
db/init.sql        creates the 6 databases
scripts/           start_all.py, smoke_test.py, test_social_login.py, reset_db.py, gen_product_images.py
docker-compose.yml Postgres only
```
# skyshop-api
