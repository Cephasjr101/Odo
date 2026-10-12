"""Shore — marketplace API for restaurants, pharmacies & stores (Ghana).
Customers browse approved stores, cart, pay via Paystack. Vendors register at /store,
are admin-approved, list items, fulfill orders, earn 85% (platform 15%, admin-settable)."""
import logging, os, uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

import models, notifications, payments, security
from database import Base, engine, get_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shore")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
try:
    COMMISSION = float(os.getenv("COMMISSION_PCT", "0.15"))
except ValueError:
    COMMISSION = 0.15
ALLOWED = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(title="Shore API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED or ["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def _no_cache(request, call_next):
    r = await call_next(request)
    if request.url.path in ("/", "/index.html"):
        r.headers["Cache-Control"] = "no-store, must-revalidate"
    return r

@app.on_event("startup")
def _startup():
    Base.metadata.create_all(bind=engine)
    from sqlalchemy import text as _t
    with engine.begin() as c:
        for ddl in ("ALTER TABLE stores ADD COLUMN lat FLOAT", "ALTER TABLE stores ADD COLUMN lng FLOAT",
                    "ALTER TABLE orders ADD COLUMN delivery_lat FLOAT", "ALTER TABLE orders ADD COLUMN delivery_lng FLOAT"):
            try:
                c.execute(_t(ddl))
            except Exception:
                pass

_bearer = HTTPBearer(auto_error=False)

def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)):
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    p = security.decode_token(creds.credentials)
    if not p:
        raise HTTPException(401, "Invalid or expired token")
    u = db.query(models.User).filter(models.User.id == int(p["sub"])).first()
    if u is None or u.disabled:
        raise HTTPException(401, "Account unavailable")
    return u

def _admin(u):
    if u.role != "admin" and u.email.lower() not in ADMIN_EMAILS:
        raise HTTPException(403, "Admin only")
    return u

def _notify(db, user_id, title, body):
    try:
        db.add(models.Notification(user_id=user_id, title=title, body=body))
        db.commit()
    except Exception:
        log.warning("notify persist failed", exc_info=True)
    try:
        u = db.get(models.User, user_id)
        if u: notifications.notify(u, title, body)
    except Exception:
        pass

def _commission(db):
    row = db.query(models.Setting).filter(models.Setting.key == "commission_pct").first()
    if row:
        try:
            return float(row.value)
        except ValueError:
            pass
    return COMMISSION


# ---------------------------------------------------------------- auth
@app.post("/auth/register")
def register(body: dict, db: Session = Depends(get_db)):
    email = str(body.get("email") or "").strip().lower()
    pw = str(body.get("password") or "")
    name = str(body.get("name") or "").strip()
    phone = str(body.get("phone") or "").strip() or None
    role = str(body.get("role") or "customer").lower()
    if role not in ("customer", "vendor"):
        role = "customer"
    if "@" not in email or len(pw) < 6:
        raise HTTPException(400, "Valid email and a 6+ character password are required")
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(409, "Email already registered")
    if phone and db.query(models.User).filter(models.User.phone == phone).first():
        raise HTTPException(409, "Phone number already registered")
    u = models.User(email=email, password_hash=security.hash_password(pw), role=role, name=name, phone=phone)
    db.add(u); db.commit(); db.refresh(u)
    return {"access_token": security.create_token(u.id, u.role), "token_type": "bearer",
            "user": _user_out(u)}

@app.post("/auth/login")
def login(body: dict, db: Session = Depends(get_db)):
    ident = str(body.get("email") or "").strip().lower()
    u = db.query(models.User).filter(models.User.email == ident).first()
    if u is None and ident.isdigit():
        u = db.query(models.User).filter(models.User.phone == ident).first()
    if u is None or not security.verify_password(str(body.get("password") or ""), u.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    if u.disabled:
        raise HTTPException(401, "Account disabled")
    return {"access_token": security.create_token(u.id, u.role), "token_type": "bearer", "user": _user_out(u)}

def _user_out(u):
    return {"id": u.id, "email": u.email, "phone": u.phone, "role": u.role, "name": u.name,
            "is_admin": u.role == "admin" or u.email.lower() in ADMIN_EMAILS}

@app.get("/me")
def me(user=Depends(get_current_user)):
    return _user_out(user)

@app.delete("/me")
def delete_account(user=Depends(get_current_user), db: Session = Depends(get_db)):
    uid = user.id
    user.email = f"deleted-{uid}@shore.app"
    user.phone = None
    user.password_hash = "!"
    user.name = "Deleted account"
    user.disabled = 1
    db.commit()
    return {"ok": True}

@app.post("/auth/forgot")
def forgot(body: dict, db: Session = Depends(get_db)):
    ident = str(body.get("email") or "").strip().lower()
    u = db.query(models.User).filter(models.User.email == ident).first()
    if u:
        token = uuid.uuid4().hex
        u.reset_token = token
        link = f"{PUBLIC_BASE_URL}/#reset?token={token}"
        notifications.send(u.email, "Shore password reset",
                           f"Reset your password within 1 hour:\n{link}\nIf you didn't request this, ignore this email.")
        db.commit()
    return {"ok": True}

@app.post("/auth/reset")
def reset(body: dict, db: Session = Depends(get_db)):
    token = str(body.get("token") or body.get("email") or "")
    u = db.query(models.User).filter(models.User.reset_token == token).first()
    if u is None:
        raise HTTPException(400, "Reset link invalid or expired")
    u.password_hash = security.hash_password(str(body.get("password") or ""))
    u.reset_token = None
    db.commit()
    return {"ok": True}


    from sqlalchemy import text
    with engine.begin() as c:
        for ddl in ("ALTER TABLE users ADD COLUMN reset_token VARCHAR(80)",
                    "ALTER TABLE orders ADD COLUMN cancel_reason VARCHAR(300) DEFAULT ''"):
            try:
                c.execute(text(ddl))
            except Exception:
                pass

@app.on_event("startup")
def _migrate():
    _ensure_columns()


# ---------------------------------------------------------------- stores
CATEGORIES = ("restaurant", "pharmacy", "store")

@app.post("/stores/register", status_code=201)
def register_store(body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role not in ("vendor", "admin"):
        raise HTTPException(403, "Register a vendor account first (see /store)")
    name = str(body.get("name") or "").strip()
    cat = str(body.get("category") or "").lower().strip()
    if not name or cat not in CATEGORIES:
        raise HTTPException(400, "Store name and category (restaurant|pharmacy|store) are required")
    existing = db.query(models.Store).filter(models.Store.owner_id == user.id).first()
    if existing is not None:
        raise HTTPException(400, "You already have a store registered")
    s = models.Store(owner_id=user.id, name=name[:140], category=cat,
                     description=str(body.get("description") or "")[:500],
                     phone=str(body.get("phone") or user.phone or "")[:40],
                     address=str(body.get("address") or "")[:200])
    try:
        s.lat = float(body.get("lat")) if body.get("lat") not in (None, "") else None
        s.lng = float(body.get("lng")) if body.get("lng") not in (None, "") else None
    except (TypeError, ValueError):
        s.lat = s.lng = None
    db.add(s); db.commit(); db.refresh(s)
    notifications.notify_admin("New store registration",
        f"{name} ({cat}) by {user.email}. Approve it in the admin dashboard.")
    return {"id": s.id, "status": s.status}

@app.get("/stores")
def list_stores(category: str = "", db: Session = Depends(get_db)):
    q = db.query(models.Store).filter(models.Store.status == "approved")
    if category in CATEGORIES:
        q = q.filter(models.Store.category == category)
    return [{"id": s.id, "name": s.name, "category": s.category, "description": s.description,
             "address": s.address, "phone": s.phone, "lat": s.lat, "lng": s.lng} for s in q.order_by(models.Store.name).all()]

@app.get("/stores/{sid}")
def store_detail(sid: int, db: Session = Depends(get_db)):
    s = db.get(models.Store, sid)
    if s is None or s.status != "approved":
        raise HTTPException(404, "Store not found")
    return {"id": s.id, "name": s.name, "category": s.category, "description": s.description,
            "address": s.address, "phone": s.phone}

@app.get("/my/store")
def my_store(user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.query(models.Store).filter(models.Store.owner_id == user.id).first()
    if s is None:
        return {"store": None}
    items = db.query(models.Item).filter(models.Item.store_id == s.id).order_by(models.Item.created_at.desc()).all()
    return {"store": {"id": s.id, "name": s.name, "category": s.category, "status": s.status,
                      "note": s.note, "balance_ghs": s.balance_ghs, "pending_ghs": s.pending_ghs},
            "items": [{"id": i.id, "name": i.name, "description": i.description,
                       "price_ghs": i.price_ghs, "stock": i.stock, "available": bool(i.available)}
                      for i in items]}


# ---------------------------------------------------------------- items
def _my_store(db, user):
    s = db.query(models.Store).filter(models.Store.owner_id == user.id).first()
    if s is None:
        raise HTTPException(404, "Register your store first")
    if s.status != "approved":
        raise HTTPException(403, f"Your store is '{s.status}' - waiting for admin approval")
    return s

@app.post("/stores/{sid}/items", status_code=201)
def add_item(sid: int, body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(models.Store, sid)
    if s is None or s.owner_id != user.id:
        raise HTTPException(403, "Not your store")
    if s.status != "approved":
        raise HTTPException(403, "Store not approved yet")
    name = str(body.get("name") or "").strip()
    price = float(body.get("price_ghs") or 0)
    if not name or price <= 0:
        raise HTTPException(400, "Item name and a positive price are required")
    i = models.Item(store_id=sid, name=name[:160], description=str(body.get("description") or "")[:400],
                    price_ghs=round(price, 2), stock=max(0, int(body.get("stock") or 999)))
    db.add(i); db.commit(); db.refresh(i)
    return {"id": i.id, "name": i.name, "price_ghs": i.price_ghs}

@app.get("/stores/{sid}/items")
def store_items(sid: int, db: Session = Depends(get_db)):
    s = db.get(models.Store, sid)
    if s is None or s.status != "approved":
        raise HTTPException(404, "Store not found")
    rows = db.query(models.Item).filter(models.Item.store_id == sid, models.Item.available == 1).all()
    return [{"id": i.id, "name": i.name, "description": i.description,
             "price_ghs": i.price_ghs, "stock": i.stock} for i in rows]

@app.patch("/items/{iid}")
def update_item(iid: int, body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    i = db.get(models.Item, iid)
    s = db.get(models.Store, i.store_id) if i else None
    if i is None or s is None or s.owner_id != user.id:
        raise HTTPException(404, "Item not found")
    if "name" in body: i.name = str(body["name"])[:160]
    if "price_ghs" in body: i.price_ghs = round(float(body["price_ghs"]), 2)
    if "stock" in body: i.stock = max(0, int(body["stock"]))
    if "available" in body: i.available = 1 if body["available"] else 0
    db.commit()
    return {"ok": True}

@app.delete("/items/{iid}")
def delete_item(iid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    i = db.get(models.Item, iid)
    s = db.get(models.Store, i.store_id) if i else None
    if i is None or s is None or s.owner_id != user.id:
        raise HTTPException(404, "Item not found")
    db.delete(i); db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- orders & payments
@app.post("/orders", status_code=201)
def create_order(body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sid = int(body.get("store_id") or 0)
    s = db.get(models.Store, sid)
    if s is None or s.status != "approved":
        raise HTTPException(404, "Store not found")
    lines = body.get("items") or []
    if not lines:
        raise HTTPException(400, "Your cart is empty")
    total = 0.0
    oi_rows = []
    for ln in lines:
        it = db.get(models.Item, int(ln.get("item_id") or 0))
        if it is None or it.store_id != sid or not it.available:
            raise HTTPException(400, "An item in your cart is no longer available")
        qty = max(1, min(int(ln.get("qty") or 1), it.stock))
        total += it.price_ghs * qty
        oi_rows.append((it, qty))
    total = round(total, 2)
    reference = "SHO-" + uuid.uuid4().hex[:20].upper()
    o = models.Order(reference=reference, customer_id=user.id, store_id=sid, total_ghs=total,
                     delivery_address=str(body.get("delivery_address") or "")[:300])
    try:
        o.delivery_lat = float(body.get("delivery_lat")) if body.get("delivery_lat") not in (None, "") else None
        o.delivery_lng = float(body.get("delivery_lng")) if body.get("delivery_lng") not in (None, "") else None
    except (TypeError, ValueError):
        o.delivery_lat = o.delivery_lng = None
    db.add(o); db.commit(); db.refresh(o)
    for it, qty in oi_rows:
        db.add(models.OrderItem(order_id=o.id, item_id=it.id, name=it.name, price_ghs=it.price_ghs, qty=qty))
        it.stock = max(0, it.stock - qty)
    db.commit()
    url = payments.init_transaction(user.email, total, reference, {"order_id": o.id, "store": s.name})
    if url is None:
        db.delete(o); db.commit()
        raise HTTPException(503, "Payments are not configured - set PAYSTACK_SECRET_KEY")
    return {"authorization_url": url, "reference": reference, "total_ghs": total}

def _settle(db, reference):
    o = db.query(models.Order).filter(models.Order.reference == reference).first()
    if o is None or o.status != "pending":
        return
    if payments.verify_transaction(reference):
        o.status = "paid"
        comm = _commission(db)
        o.commission_ghs = round(o.total_ghs * comm, 2)
        o.store_earning_ghs = round(o.total_ghs - o.commission_ghs, 2)
        s = db.get(models.Store, o.store_id)
        if s is not None:
            s.balance_ghs = round(float(s.balance_ghs or 0) + o.store_earning_ghs, 2)
        db.commit()
        _notify(db, o.customer_id, "Payment confirmed",
                f"Order {o.reference} is paid. The store has been notified.")
        if s is not None:
            _notify(db, s.owner_id, "New paid order",
                    f"Order {o.reference} - GHS {o.total_ghs:,.2f} (you earn {o.store_earning_ghs:,.2f}). Start preparing!")
        notifications.notify_admin("Order paid",
            f"{o.reference}: GHS {o.total_ghs:,.2f} at store {o.store_id}. Commission GHS {o.commission_ghs:,.2f}.")
    else:
        o.status = "cancelled"
        o.cancel_reason = "payment failed"
        db.commit()

@app.get("/payments/callback")
def callback(reference: str = "", db: Session = Depends(get_db)):
    if reference:
        _settle(db, reference)
    return RedirectResponse(f"/#orders?paid={reference}" if reference else "/")

@app.post("/payments/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not payments.verify_webhook(raw, request.headers.get("x-paystack-signature", "")):
        return {"ok": False}
    body = json.loads(raw or b"{}")
    if body.get("event") == "charge.success":
        _settle(db, (body.get("data") or {}).get("reference", ""))
    return {"ok": True}

ORDER_FLOW = ["paid", "accepted", "preparing", "ready", "dispatched", "delivered"]

@app.get("/me/orders")
def my_orders(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(models.Order).filter(models.Order.customer_id == user.id) \
        .order_by(models.Order.created_at.desc()).limit(50).all()
    return [_order_out(db, o) for o in rows]

@app.get("/stores/{sid}/orders")
def store_orders(sid: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(models.Store, sid)
    if s is None or s.owner_id != user.id:
        raise HTTPException(403, "Not your store")
    rows = db.query(models.Order).filter(models.Order.store_id == sid) \
        .order_by(models.Order.created_at.desc()).limit(100).all()
    return [_order_out(db, o) for o in rows]

@app.patch("/orders/{oid}/status")
def set_status(oid: int, body: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    o = db.get(models.Order, oid)
    if o is None:
        raise HTTPException(404, "Order not found")
    s = db.get(models.Store, o.store_id)
    new = str(body.get("status") or "")
    allowed_next = {"paid": ["accepted", "cancelled"], "accepted": ["preparing", "cancelled"],
                    "preparing": ["ready", "cancelled"], "ready": ["dispatched"],
                    "dispatched": ["delivered"], "delivered": [], "cancelled": [], "pending": []}
    if user.role == "admin" or (s and s.owner_id == user.id):
        if new not in allowed_next.get(o.status, []):
            raise HTTPException(400, f"Cannot move order from '{o.status}' to '{new}'")
        if new == "cancelled":
            o.cancel_reason = str(body.get("reason") or "cancelled by store")[:300]
        o.status = new
        db.commit()
        _notify(db, o.customer_id, f"Order {new}",
                f"Order {o.reference} is now '{new}'." + (f" Reason: {o.cancel_reason}" if new == "cancelled" else ""))
        return _order_out(db, o)
    if o.customer_id == user.id and new == "cancelled" and o.status == "paid":
        o.status = "cancelled"
        o.cancel_reason = str(body.get("reason") or "cancelled by customer")[:300]
        db.commit()
        if s:
            _notify(db, s.owner_id, "Order cancelled", f"Order {o.reference} was cancelled. Reason: {o.cancel_reason}")
        return _order_out(db, o)
    raise HTTPException(403, "You cannot update this order")

def _order_out(db, o):
    s = db.get(models.Store, o.store_id)
    items = db.query(models.OrderItem).filter(models.OrderItem.order_id == o.id).all()
    return {"id": o.id, "reference": o.reference, "store": s.name if s else "",
            "store_id": o.store_id, "total_ghs": o.total_ghs, "status": o.status,
            "cancel_reason": o.cancel_reason, "delivery_address": o.delivery_address,
            "items": [{"name": i.name, "price_ghs": i.price_ghs, "qty": i.qty} for i in items],
            "created_at": o.created_at.isoformat() + "Z" if o.created_at else None}


# ---------------------------------------------------------------- admin
@app.get("/admin/stats")
def admin_stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    paid = db.query(models.Order).filter(models.Order.status != "cancelled",
                                         models.Order.status != "pending").count()
    return {
        "users": db.query(models.User).count(),
        "stores": db.query(models.Store).count(),
        "pending_stores": db.query(models.Store).filter(models.Store.status == "pending").count(),
        "items": db.query(models.Item).count(),
        "orders": db.query(models.Order).count(),
        "orders_active": paid,
        "revenue_ghs": round(sum(r[0] for r in db.query(models.Order.commission_ghs)
                                 .filter(models.Order.status != "pending", models.Order.status != "cancelled").all()), 2),
        "stores_balance_ghs": round(sum(float(s.balance_ghs or 0) for s in db.query(models.Store).all()), 2),
    }

@app.get("/admin/stores")
def admin_stores(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    rows = db.query(models.Store).order_by(models.Store.created_at.desc()).limit(200).all()
    out = []
    for s in rows:
        o = db.get(models.User, s.owner_id)
        out.append({"id": s.id, "name": s.name, "category": s.category, "status": s.status,
                    "note": s.note, "owner": (o.email if o else ""), "phone": s.phone,
                    "balance_ghs": s.balance_ghs,
                    "created_at": s.created_at.isoformat() + "Z" if s.created_at else None})
    return out

@app.post("/admin/stores/{sid}/decision")
def admin_store_decision(sid: int, body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    s = db.get(models.Store, sid)
    if s is None:
        raise HTTPException(404, "Store not found")
    action = str(body.get("action") or "")
    if action not in ("approve", "reject", "disable", "enable"):
        raise HTTPException(400, "action must be approve|reject|disable|enable")
    s.status = {"approve": "approved", "reject": "rejected", "disable": "disabled", "enable": "approved"}[action]
    s.note = str(body.get("note") or "")[:300]
    db.commit()
    _notify(db, s.owner_id, f"Store {s.status}",
            f"Your store '{s.name}' is now {s.status}." + (f" Note: {s.note}" if s.note else ""))
    return {"ok": True, "status": s.status}

@app.get("/admin/orders")
def admin_orders(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    rows = db.query(models.Order).order_by(models.Order.created_at.desc()).limit(200).all()
    return [_order_out(db, o) for o in rows]

@app.get("/admin/users")
def admin_users(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    return [{"id": u.id, "email": u.email, "role": u.role, "name": u.name, "disabled": bool(u.disabled)}
            for u in db.query(models.User).order_by(models.User.created_at.desc()).limit(200).all()]

@app.post("/admin/users/{uid}/toggle-disable")
def admin_toggle_user(uid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    u = db.get(models.User, uid)
    if u is None:
        raise HTTPException(404, "User not found")
    u.disabled = 0 if u.disabled else 1
    db.commit()
    return {"ok": True, "disabled": bool(u.disabled)}

@app.get("/admin/pricing")
def get_pricing(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    return {"commission_pct": _commission(db)}

@app.put("/admin/pricing")
def set_pricing(body: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    _admin(_)
    v = float(body.get("commission_pct", 0.15))
    if not (0.05 <= v <= 0.40):
        raise HTTPException(400, "Commission must be between 5% and 40%")
    row = db.query(models.Setting).filter(models.Setting.key == "commission_pct").first()
    if row is None:
        db.add(models.Setting(key="commission_pct", value=str(v)))
    else:
        row.value = str(v)
    db.commit()
    return {"ok": True, "commission_pct": v}


# ---------------------------------------------------------------- misc
@app.post("/newsletter", status_code=201)
def newsletter(body: dict, db: Session = Depends(get_db)):
    email = str(body.get("email") or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Valid email required")
    if db.query(models.NewsletterSubscriber).filter(models.NewsletterSubscriber.email == email).first() is None:
        db.add(models.NewsletterSubscriber(email=email))
        db.commit()
    return {"ok": True}

@app.get("/notifications")
def my_notifications(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(models.Notification).filter(models.Notification.user_id == user.id) \
        .order_by(models.Notification.created_at.desc()).limit(20).all()
    return [{"id": n.id, "title": n.title, "body": n.body,
             "created_at": n.created_at.isoformat() + "Z" if n.created_at else None} for n in rows]

@app.get("/api/config")
def public_config():
    return {"google_maps_key": os.getenv("GOOGLE_MAPS_API_KEY", "")}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "shore", "time": datetime.utcnow().isoformat() + "Z"}
