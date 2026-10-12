"""Email: SendGrid HTTPS API (EMAIL_MODE=api, key in SMTP_PASS) or SMTP relay. Logs otherwise."""
import json, logging, os, smtplib, urllib.request
from email.message import EmailMessage

log = logging.getLogger("shore.notify")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER or "noreply@shore.app")
ADMIN_EMAIL = os.getenv("ADMIN_EMAILS", "")
EMAIL_MODE = os.getenv("EMAIL_MODE", "api").lower()

def _api(to, subject, body):
    payload = {"personalizations": [{"to": [{"email": to}]}],
               "from": {"email": MAIL_FROM}, "subject": subject,
               "content": [{"type": "text/plain", "value": body}]}
    req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": "Bearer " + SMTP_PASS, "Content-Type": "application/json", "User-Agent": "Shore/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception:
        log.warning("sendgrid api failed", exc_info=True)
        return False

def send(to, subject, body):
    log.info("[email] to=%s | %s | %s", to, subject, body[:150].replace("\n", " / "))
    if not to:
        return False
    if EMAIL_MODE == "api":
        return _api(to, subject, body) if SMTP_PASS else False
    if not SMTP_HOST:
        return False
    try:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = MAIL_FROM, to, subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            if SMTP_USER: s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception:
        log.warning("email failed", exc_info=True)
        return False

def notify(user, title, body):
    email = getattr(user, "email", None)
    if email and getattr(user, "disabled", 0) == 0:
        send(email, title, body)

def notify_admin(title, body):
    for a in [x.strip() for x in ADMIN_EMAIL.split(",") if x.strip()]:
        send(a, title, body)
