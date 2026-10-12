"""Paystack integration (urllib only). Needs PAYSTACK_SECRET_KEY + PAYSTACK_CALLBACK_URL."""
import hashlib, hmac, json, logging, os, urllib.error, urllib.request

log = logging.getLogger("shore.payments")
KEY = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
CALLBACK = os.getenv("PAYSTACK_CALLBACK_URL", "")
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Shore/1.0", "Accept": "application/json"}

def configured(): return bool(KEY)

def init_transaction(email, amount_ghs, reference, metadata):
    if not KEY: return None
    payload = {"email": email, "amount": int(round(amount_ghs * 100)), "reference": reference, "metadata": metadata or {}}
    if CALLBACK: payload["callback_url"] = CALLBACK
    req = urllib.request.Request("https://api.paystack.co/transaction/initialize",
        data=json.dumps(payload).encode(),
        headers={**_HEADERS, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return d["data"]["authorization_url"] if d.get("status") else None
    except Exception as e:
        log.warning("paystack initialize failed: %s", e)
        return None

def verify_transaction(reference):
    if not KEY: return False
    req = urllib.request.Request(f"https://api.paystack.co/transaction/verify/{reference}",
        headers={**_HEADERS, "Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return bool(d.get("status")) and d["data"]["status"] == "success"
    except Exception as e:
        log.warning("paystack verify failed: %s", e)
        return False

def verify_webhook(raw, sig):
    if not KEY: return False
    return hmac.compare_digest(hmac.new(KEY.encode(), raw, hashlib.sha512).hexdigest(), sig or "")
