# Shore — marketplace webapp for restaurants, pharmacies & stores

## Stack
FastAPI + SQLite (persistent disk on Render), Paystack (MoMo/card), SendGrid email
(EMAIL_MODE=api), single-file mobile-first SPA (Bolt Food / Walmart style).

## Layout
- `backend/` — API (main.py, models.py, security.py, payments.py, database.py, notifications.py)
- `static/index.html` — customer app: stores, menu, cart, Paystack checkout, orders, vendor dashboard, admin
- `static/store.html` — vendor recruitment landing served at `/store`
- `render.yaml` — one-click Render Blueprint

## Flows
- **Customers** register → browse approved stores → cart → Paystack → track order status with notifications.
- **Vendors** register at `/store` → admin approves → list items with prices → receive paid orders →
  advance status (accepted → preparing → ready → dispatched → delivered) → earn 85%.
- **Platform** keeps 15% per order (admin-adjustable 5–40% in the admin dashboard).
- **Admin** (email in ADMIN_EMAILS or role=admin): KPIs, store approvals/rejections/disables,
  commission setting, all orders, users, revenue & store balances.

## Deploy
1. Push repo → Render → New → Blueprint → this repo.
2. Env vars (Render dashboard): PAYSTACK_SECRET_KEY, PAYSTACK_CALLBACK_URL
   (= https://YOUR-API.onrender.com/payments/callback), PUBLIC_BASE_URL, ADMIN_EMAILS,
   SMTP_PASS (SendGrid key), MAIL_FROM, DATABASE_URL (= sqlite:////var/data/shore.db after adding a disk).
3. Add a 1GB disk mounted at `/var/data` + daily snapshots.
4. In Paystack dashboard set webhook to https://YOUR-API.onrender.com/payments/webhook.
5. Static site: host `static/` (Netlify or Render static) or serve via the API.

## Policies
Terms, Privacy and Refund pages ship in the SPA footer (insurance clause included:
coverage up to GHS 1,000,000 with evidence from both parties; refunds within 72h / 3 business days).
