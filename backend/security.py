"""Token + password helpers."""
import base64, hashlib, hmac, json, os, time

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production").encode()
TTL = 60 * 60 * 24 * 30

def _b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def _b64ud(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def hash_password(pw):
    salt = os.urandom(16).hex()
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 100_000).hex()}"

def verify_password(pw, stored):
    try: salt, digest = stored.split("$", 1)
    except ValueError: return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 100_000).hex(), digest)

def create_token(user_id, role="user"):
    now = int(time.time())
    body = _b64u(json.dumps({"sub": str(user_id), "role": role, "iat": now, "exp": now + TTL}).encode())
    head = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    sig = _b64u(hmac.new(SECRET_KEY, f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"

def decode_token(token):
    try:
        h, b, s = token.split(".")
        if not hmac.compare_digest(_b64u(hmac.new(SECRET_KEY, f"{h}.{b}".encode(), hashlib.sha256).digest()), s):
            return None
        p = json.loads(_b64ud(b))
        return None if p.get("exp", 0) < int(time.time()) else p
    except Exception:
        return None
