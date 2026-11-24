import io
import re
import secrets
from datetime import timedelta

import pytest
from PIL import Image

from tusovka import create_app
from tusovka.db import connect
from tusovka.security import limiter, to_db, utcnow
from tusovka.validation import to_local_input


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("TUSOVKA_ENV", "development")
    limiter.reset()
    app = create_app({
        "DATABASE_PATH": str(tmp_path / "test.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "RATELIMIT_ENABLED": False,
    })
    import os
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_client(app):
    return app.test_client


def csrf(client):
    with client.session_transaction() as s:
        if "csrf" not in s:
            s["csrf"] = secrets.token_urlsafe(32)
        return s["csrf"]


def post(client, url, data=None, **kw):
    data = dict(data or {})
    data.setdefault("csrf_token", csrf(client))
    return client.post(url, data=data, **kw)


def register(client, email="alice@example.com", name="Alice", password="correct-horse-1"):
    return post(client, "/register", {
        "email": email, "name": name, "password": password, "password2": password,
    })


def future(hours=48):
    with_app_tz = utcnow() + timedelta(hours=hours)
    return with_app_tz


def event_form(**over):
    data = {
        "title": "Настолки в п'ятницю",
        "description": "Граємо в настільні ігри.",
        "starts_at": None,
        "capacity": "10",
        "visibility": "public",
    }
    data.update(over)
    return data


def create_event(client, app, **over):
    data = event_form(**over)
    if data["starts_at"] is None:
        with app.app_context():
            data["starts_at"] = to_local_input(future())
    resp = post(client, "/events/new", data, content_type="multipart/form-data")
    assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
    loc = resp.headers["Location"]
    m = re.search(r"/e/([A-Za-z0-9_-]+)(?:\?invite=([A-Za-z0-9_-]+))?", loc)
    return m.group(1), m.group(2)


def db(app):
    return connect(app.config["DATABASE_PATH"])


def set_start(app, public_id, dt):
    conn = db(app)
    conn.execute("UPDATE events SET starts_at = ? WHERE public_id = ?", (to_db(dt), public_id))
    conn.close()


def image_bytes(fmt="PNG", size=(64, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 212, 0)).save(buf, format=fmt)
    return buf.getvalue()
