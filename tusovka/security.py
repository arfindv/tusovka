import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, g, redirect, request, session, url_for

from .db import get_db

SESSION_DAYS = 14
DT_FMT = "%Y-%m-%d %H:%M:%S"


# ---------- час ----------

def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_db(dt):
    return dt.astimezone(timezone.utc).strftime(DT_FMT)


def from_db(value):
    return datetime.strptime(value, DT_FMT).replace(tzinfo=timezone.utc)


def now_db():
    return to_db(utcnow())


# ---------- сесії ----------

def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def login_user(user_id):
    db = get_db()
    # нова сесія при кожному вході: захист від session fixation
    session.clear()
    token = secrets.token_urlsafe(32)
    expires = to_db(utcnow() + timedelta(days=SESSION_DAYS))
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash_token(token), user_id, expires),
    )
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_db(),))
    session.permanent = True
    session["sid"] = token
    session["csrf"] = secrets.token_urlsafe(32)


def logout_user():
    token = session.get("sid")
    if token:
        get_db().execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    session.clear()


def load_current_user():
    g.user = None
    token = session.get("sid")
    if not isinstance(token, str):
        return
    row = get_db().execute(
        """SELECT u.id, u.email, u.name FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at > ?""",
        (_hash_token(token), now_db()),
    ).fetchone()
    if row is None:
        session.pop("sid", None)
        return
    g.user = row


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped


def safe_next(target):
    """Лише відносні шляхи цього сайту — без open redirect."""
    if not target or not isinstance(target, str):
        return None
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    return target


# ---------- CSRF ----------

def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("Origin")
    if origin and origin != "null":
        if urlparse(origin).netloc != request.host:
            abort(403)
    expected = session.get("csrf")
    sent = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(str(expected), str(sent)):
        abort(400, description="Форма застаріла. Оновіть сторінку й спробуйте ще раз.")


# ---------- rate limit ----------

class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key, limit, window):
        """True — дозволено; False — ліміт вичерпано."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50000:  # захист пам'яті
                for k in [k for k, v in self._hits.items() if not v][:10000]:
                    del self._hits[k]
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(bucket, limit, window, per_user=False):
    if not current_app.config.get("RATELIMIT_ENABLED", True):
        return
    who = f"u{g.user['id']}" if per_user and g.get("user") else request.remote_addr or "?"
    if not limiter.hit(f"{bucket}:{who}", limit, window):
        abort(429)


# ---------- заголовки ----------

def set_security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    # no-referrer: посилання-запрошення не витікає через Referer на інші сайти
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if current_app.config.get("SESSION_COOKIE_SECURE"):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if g.get("user") is not None:
        resp.headers["Cache-Control"] = "no-store"
    return resp
