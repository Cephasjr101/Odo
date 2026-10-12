"""models.py — Shore marketplace data model."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey
from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(160), unique=True, index=True, nullable=False)
    phone = Column(String(40), unique=True, index=True)
    password_hash = Column(String(300), nullable=False)
    role = Column(String(20), default="customer")      # customer | vendor | admin
    reset_token = Column(String(80), nullable=True)
    name = Column(String(120), default="")
    disabled = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(140), nullable=False)
    category = Column(String(20), nullable=False)      # restaurant | pharmacy | store
    description = Column(Text, default="")
    phone = Column(String(40), default="")
    address = Column(String(200), default="")
    status = Column(String(20), default="pending")     # pending | approved | rejected | disabled
    note = Column(String(300), default="")
    balance_ghs = Column(Float, default=0.0)           # withdrawable (85% earnings)
    pending_ghs = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    name = Column(String(160), nullable=False)
    description = Column(String(400), default="")
    price_ghs = Column(Float, nullable=False)
    stock = Column(Integer, default=999)
    available = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    reference = Column(String(40), unique=True, index=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    total_ghs = Column(Float, nullable=False)
    commission_ghs = Column(Float, default=0.0)        # platform cut (admin-settable, default 15%)
    store_earning_ghs = Column(Float, default=0.0)     # store's share
    status = Column(String(20), default="pending")     # pending|paid|accepted|preparing|ready|dispatched|delivered|cancelled
    delivery_address = Column(String(300), default="")
    cancel_reason = Column(String(300), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True, nullable=False)
    item_id = Column(Integer, nullable=False)
    name = Column(String(160), default="")
    price_ghs = Column(Float, default=0)
    qty = Column(Integer, default=1)


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(60), unique=True, nullable=False, index=True)
    value = Column(String(60), nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, index=True, nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(String(500), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"
    id = Column(Integer, primary_key=True)
    email = Column(String(160), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
