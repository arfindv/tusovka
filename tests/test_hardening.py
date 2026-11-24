"""Регресійні тести на знахідки аудиту безпеки."""
import io
import os
import re
import time

from conftest import create_event, db, image_bytes, post, register


def test_huge_email_does_not_hang_server(client):
    # раніше регулярка для email мала квадратичний час — 1 МБ «крапок» вішав потік на години
    evil = "a@" + "." * 900_000 + "@"
    started = time.perf_counter()
    r = post(client, "/register", {"email": evil, "name": "Evil", "password": "abcd1234xyz", "password2": "abcd1234xyz"})
    assert r.status_code == 422
    assert time.perf_counter() - started < 2


def test_huge_login_payload_fast(client):
    started = time.perf_counter()
    r = post(client, "/login", {"email": "a" * 900_000, "password": "b" * 900_000})
    assert r.status_code == 401
    assert time.perf_counter() - started < 2


def test_valid_emails_still_accepted(make_client):
    for i, email in enumerate(["first.last+tag@sub.example.co.uk", "x@y.zz", "ім'я@приклад.укр"]):
        r = register(make_client(), email=email, name=f"User {i}")
        assert r.status_code == 302, email


def test_weak_passwords_rejected(make_client):
    for pw in ["12345678", "password", "aaaaaaaa", "abcdefgh", "11112222", "qwerty123", "alice@example.com"]:
        c = make_client()
        r = post(c, "/register", {"email": "alice@example.com", "name": "Alice", "password": pw, "password2": pw})
        assert r.status_code == 422, pw


def test_no_admin_role_or_privileged_fields(client, app):
    # спроба «масового присвоєння» ролі при реєстрації ігнорується — такої ролі немає взагалі
    post(client, "/register", {"email": "eve@example.com", "name": "Eve", "password": "eve-pass-123",
                               "password2": "eve-pass-123", "role": "admin", "is_admin": "1", "id": "1"})
    cols = [r[1] for r in db(app).execute("PRAGMA table_info(users)").fetchall()]
    assert cols == ["id", "email", "name", "password_hash", "created_at"]
    for path in ["/admin", "/admin/", "/api/users", "/users", "/debug", "/console"]:
        assert client.get(path).status_code == 404, path


def test_password_hash_is_argon2id_and_never_rendered(client, app):
    register(client)
    h = db(app).execute("SELECT password_hash FROM users").fetchone()[0]
    assert h.startswith("$argon2id$")
    for path in ["/", "/me", "/events/new"]:
        assert h not in client.get(path).get_data(as_text=True)


def test_author_does_not_take_a_seat(client, app):
    register(client)
    pid, _ = create_event(client, app, capacity="1")
    r = post(client, f"/e/{pid}/join", follow_redirects=True)
    assert "організатор" in r.get_data(as_text=True)
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 0


def test_huge_ids_are_404_not_500(client, app):
    register(client)
    pid, _ = create_event(client, app)
    assert post(client, f"/e/{pid}/comments/{10**30}/delete").status_code == 404


def test_html_not_cacheable(client, app):
    register(client)
    pid, invite = create_event(client, app, visibility="private")
    assert client.get(f"/e/{pid}?invite={invite}").headers["Cache-Control"] == "no-store"
    assert "no-store" in client.application.test_client().get("/").headers["Cache-Control"]


def test_distributed_bruteforce_limited_per_account(app, make_client):
    app.config["RATELIMIT_ENABLED"] = True
    register(make_client(), email="target@example.com", name="Target")
    codes = []
    for i in range(55):
        c = make_client()
        # кожен «атакувальник» з іншого IP
        codes.append(post(c, "/login", {"email": "target@example.com", "password": f"guess{i}"},
                          environ_base={"REMOTE_ADDR": f"10.0.{i // 250}.{i % 250 + 1}"}).status_code)
    assert 429 in codes


def test_open_redirect_variants_blocked(client):
    register(client)
    post(client, "/logout")
    for nxt in ["//evil.example", "/\\evil.example", "/\t/evil.example", "https://evil.example", "/%0a/evil"]:
        c = client.application.test_client()
        r = post(c, "/login", {"email": "alice@example.com", "password": "correct-horse-1", "next": nxt})
        loc = r.headers["Location"]
        assert loc.startswith("/") and not loc.startswith("//") and "evil.example" not in loc.split("?")[0].replace("/%0a/evil", ""), (nxt, loc)


def test_static_traversal_blocked(client):
    for path in ["/static/../instance/tusovka.db", "/static/..%2f..%2finstance%2ftusovka.db", "/static/%2e%2e/run.py"]:
        assert client.get(path).status_code == 404, path


def test_dev_secret_persists_across_restarts(tmp_path, monkeypatch):
    from tusovka import _dev_secret
    a = _dev_secret(str(tmp_path))
    b = _dev_secret(str(tmp_path))
    assert a == b and len(a) == 64


# ---------- після другого аудиту ----------

def _private_with_cover(client, app):
    from conftest import event_form, to_local_input, future
    data = event_form(visibility="private")
    with app.app_context():
        data["starts_at"] = to_local_input(future())
    data["cover"] = (io.BytesIO(image_bytes("PNG")), "c.png")
    r = post(client, "/events/new", data, content_type="multipart/form-data")
    assert r.status_code == 302
    pid, inv = re.search(r"/e/([\w-]+)\?invite=([\w-]+)", r.headers["Location"]).groups()
    cover = db(app).execute("SELECT cover FROM events WHERE public_id = ?", (pid,)).fetchone()[0]
    return pid, inv, cover


def test_private_cover_needs_access(client, app, make_client):
    register(client)
    pid, inv, cover = _private_with_cover(client, app)
    assert client.get(f"/uploads/{cover}").status_code == 200  # автор
    outsider = make_client()
    assert outsider.get(f"/uploads/{cover}").status_code == 404
    assert outsider.get(f"/uploads/{cover}?invite=wrong").status_code == 404
    assert outsider.get(f"/uploads/{cover}?invite={inv}").status_code == 200
    # сторінка події за запрошенням посилається на обкладинку з токеном
    page = outsider.get(f"/e/{pid}?invite={inv}").get_data(as_text=True)
    assert f"/uploads/{cover}?invite={inv}" in page
    # після перевипуску запрошення старий токен до обкладинки не пускає
    post(client, f"/e/{pid}/invite")
    assert outsider.get(f"/uploads/{cover}?invite={inv}").status_code == 404


def test_orphan_upload_not_served(client, app):
    name = "0" * 32 + ".png"
    with open(os.path.join(app.config["UPLOAD_DIR"], name), "wb") as fh:
        fh.write(image_bytes("PNG"))
    assert client.get(f"/uploads/{name}").status_code == 404


def test_participant_leaving_private_event_from_profile_not_404(client, app, make_client):
    register(client)
    pid, inv = create_event(client, app, visibility="private")
    guest = make_client()
    register(guest, email="guest@example.com", name="Guest")
    post(guest, f"/e/{pid}/join", {"invite": inv})
    # гість відкриває подію з профілю (без токена) і скасовує запис
    assert guest.get(f"/e/{pid}").status_code == 200
    r = post(guest, f"/e/{pid}/leave")
    assert r.status_code == 302 and r.headers["Location"].endswith("/me")
    assert guest.get("/me").status_code == 200
    # а з токеном — повертаємося на подію
    post(guest, f"/e/{pid}/join", {"invite": inv})
    r = post(guest, f"/e/{pid}/leave", {"invite": inv})
    assert f"/e/{pid}?invite={inv}" in r.headers["Location"]


def test_rate_limiter_purges_stale_keys(monkeypatch):
    from tusovka.security import RateLimiter
    rl = RateLimiter()
    monkeypatch.setattr(RateLimiter, "MAX_KEYS", 100)
    clock = [1000.0]
    monkeypatch.setattr("tusovka.security.time.monotonic", lambda: clock[0])
    for i in range(100):
        assert rl.hit(f"k{i}", 5, 60)
    clock[0] += 120
    for i in range(100, 110):
        rl.hit(f"k{i}", 5, 60)
    assert len(rl._hits) <= 11
