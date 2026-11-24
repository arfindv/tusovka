import io
import threading
from datetime import timedelta

from conftest import create_event, db, event_form, future, image_bytes, post, register, set_start
from tusovka.security import utcnow
from tusovka.validation import to_local_input


def test_create_and_view_event(client, app):
    register(client)
    pid, invite = create_event(client, app)
    assert invite is None
    r = client.get(f"/e/{pid}")
    assert r.status_code == 200
    assert "Настолки" in r.get_data(as_text=True)
    assert "Настолки" in client.get("/").get_data(as_text=True)


def test_event_ids_are_not_sequential(client, app):
    register(client)
    pid, _ = create_event(client, app)
    assert not pid.isdigit() and len(pid) >= 12
    assert client.get("/e/1").status_code == 404


def test_create_requires_login(client):
    r = client.get("/events/new")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def _create_raw(client, app, **over):
    data = event_form(**over)
    if data["starts_at"] is None:
        with app.app_context():
            data["starts_at"] = to_local_input(future())
    return post(client, "/events/new", data, content_type="multipart/form-data")


def test_validation_rejects_bad_events(client, app):
    register(client)
    with app.app_context():
        past = to_local_input(utcnow() - timedelta(days=1))
        far = to_local_input(utcnow() + timedelta(days=365 * 10))
    cases = [
        {"starts_at": past},
        {"starts_at": far},
        {"starts_at": "garbage"},
        {"capacity": "0"},
        {"capacity": "-10"},
        {"capacity": "999999999"},
        {"capacity": "1.5"},
        {"capacity": "abc"},
        {"title": "   "},
        {"title": "x" * 500},
        {"description": ""},
        {"description": "y" * 6000},
        {"visibility": "secret"},
    ]
    for over in cases:
        r = _create_raw(client, app, **over)
        assert r.status_code == 422, over
    assert db(app).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_mass_assignment_ignored(client, app):
    register(client, email="a@a.aa", name="Author")
    r = _create_raw(client, app, author_id="999", invite_token="mine", id="5")
    assert r.status_code == 302
    row = db(app).execute("SELECT author_id, invite_token FROM events").fetchone()
    assert row[0] == 1 and row[1] is None


def test_other_user_cannot_edit_or_delete(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app)
    mallory = make_client()
    register(mallory, email="mallory@example.com", name="Mallory")
    with app.app_context():
        when = to_local_input(future())
    assert mallory.get(f"/e/{pid}/edit").status_code == 403
    r = post(mallory, f"/e/{pid}/edit", event_form(title="Hacked", starts_at=when),
             content_type="multipart/form-data")
    assert r.status_code == 403
    assert post(mallory, f"/e/{pid}/delete").status_code == 403
    assert db(app).execute("SELECT title FROM events").fetchone()[0] == "Настолки в п'ятницю"


def test_participants_visible_only_to_author(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app)
    bob = make_client()
    register(bob, email="bob@example.com", name="Bobby Secret")
    post(bob, f"/e/{pid}/join")
    page = bob.get(f"/e/{pid}").get_data(as_text=True)
    assert "Учасники (" not in page
    author_page = client.get(f"/e/{pid}").get_data(as_text=True)
    assert "Учасники (1)" in author_page and "Bobby Secret" in author_page


def test_emails_not_exposed(client, app, make_client):
    register(client, email="secret-author@example.com")
    pid, _ = create_event(client, app)
    anon = make_client()
    assert "secret-author@example.com" not in anon.get(f"/e/{pid}").get_data(as_text=True)
    assert "secret-author@example.com" not in anon.get("/").get_data(as_text=True)


def test_author_can_edit_and_delete(client, app):
    register(client)
    pid, _ = create_event(client, app)
    with app.app_context():
        when = to_local_input(future(72))
    r = post(client, f"/e/{pid}/edit", event_form(title="Нова назва", starts_at=when),
             content_type="multipart/form-data")
    assert r.status_code == 302
    assert "Нова назва" in client.get(f"/e/{pid}").get_data(as_text=True)
    assert post(client, f"/e/{pid}/delete").status_code == 302
    assert client.get(f"/e/{pid}").status_code == 404


def test_cannot_reduce_capacity_below_participants(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app, capacity="5")
    for i in range(3):
        c = make_client()
        register(c, email=f"u{i}@example.com", name=f"User {i}")
        post(c, f"/e/{pid}/join")
    with app.app_context():
        when = to_local_input(future())
    r = post(client, f"/e/{pid}/edit", event_form(capacity="2", starts_at=when),
             content_type="multipart/form-data")
    assert r.status_code == 422
    assert db(app).execute("SELECT capacity FROM events").fetchone()[0] == 5


# ---------- запис ----------

def test_join_and_leave(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app, capacity="2")
    bob = make_client()
    register(bob, email="bob@example.com", name="Bob")
    post(bob, f"/e/{pid}/join")
    assert "1</b> / 2" in bob.get(f"/e/{pid}").get_data(as_text=True)
    post(bob, f"/e/{pid}/join")  # повторно — не дублюється
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 1
    post(bob, f"/e/{pid}/leave")
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 0


def test_cannot_join_full_event(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app, capacity="1")
    a, b = make_client(), make_client()
    register(a, email="a@example.com", name="Ann")
    register(b, email="b@example.com", name="Ben")
    post(a, f"/e/{pid}/join")
    r = post(b, f"/e/{pid}/join", follow_redirects=True)
    assert "вільних місць не залишилося" in r.get_data(as_text=True)
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 1


def test_race_condition_never_overbooks(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app, capacity="3")
    clients = []
    for i in range(20):
        c = make_client()
        register(c, email=f"r{i}@example.com", name=f"Racer {i}")
        clients.append(c)
    barrier = threading.Barrier(len(clients))

    def go(c):
        barrier.wait()
        post(c, f"/e/{pid}/join")

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 3


def test_cannot_join_or_leave_past_event(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app)
    bob = make_client()
    register(bob, email="bob@example.com", name="Bob")
    post(bob, f"/e/{pid}/join")
    set_start(app, pid, utcnow() - timedelta(hours=1))
    post(bob, f"/e/{pid}/leave")
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 1
    carol = make_client()
    register(carol, email="carol@example.com", name="Carol")
    post(carol, f"/e/{pid}/join")
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 1
    assert "Настолки" not in client.get("/").get_data(as_text=True)  # минулих немає на головній


def test_join_requires_login_and_post(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app)
    anon = make_client()
    assert anon.get(f"/e/{pid}/join").status_code == 405
    r = post(anon, f"/e/{pid}/join")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


# ---------- приватні події ----------

def test_private_event_hidden(client, app, make_client):
    register(client)
    pid, invite = create_event(client, app, title="Таємна вечірка", visibility="private")
    assert invite and len(invite) >= 24
    stranger = make_client()
    register(stranger, email="s@example.com", name="Stranger")
    assert "Таємна" not in stranger.get("/").get_data(as_text=True)
    assert "вечірка" not in stranger.get("/?q=Таємна").get_data(as_text=True)
    assert stranger.get(f"/e/{pid}").status_code == 404
    assert stranger.get(f"/e/{pid}?invite=wrong").status_code == 404
    assert post(stranger, f"/e/{pid}/join").status_code == 404
    assert post(stranger, f"/e/{pid}/comments", {"body": "hi"}).status_code == 404
    assert stranger.get(f"/e/{pid}/edit").status_code == 404
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 0


def test_private_event_with_invite(client, app, make_client):
    register(client)
    pid, invite = create_event(client, app, title="Таємна вечірка", visibility="private")
    guest = make_client()
    register(guest, email="g@example.com", name="Guest")
    assert guest.get(f"/e/{pid}?invite={invite}").status_code == 200
    post(guest, f"/e/{pid}/join", {"invite": invite})
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 1
    # учасник бачить подію і без токена (у профілі)
    assert guest.get(f"/e/{pid}").status_code == 200
    assert "Таємна" in guest.get("/me").get_data(as_text=True)


def test_regenerated_invite_revokes_old(client, app, make_client):
    register(client)
    pid, invite = create_event(client, app, visibility="private")
    post(client, f"/e/{pid}/invite")
    other = make_client()
    assert other.get(f"/e/{pid}?invite={invite}").status_code == 404


# ---------- коментарі та XSS ----------

def test_comment_xss_escaped(client, app):
    register(client)
    pid, _ = create_event(client, app, title="<script>alert(1)</script>")
    post(client, f"/e/{pid}/comments", {"body": '<img src=x onerror=alert(1)>'})
    html = client.get(f"/e/{pid}").get_data(as_text=True)
    assert "<img src=x onerror" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_comment_validation(client, app):
    register(client)
    pid, _ = create_event(client, app)
    post(client, f"/e/{pid}/comments", {"body": "     "})
    post(client, f"/e/{pid}/comments", {"body": "a" * 1001})
    assert db(app).execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_cannot_delete_foreign_comment(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app)
    bob = make_client()
    register(bob, email="bob@example.com", name="Bob")
    post(bob, f"/e/{pid}/comments", {"body": "Привіт"})
    cid = db(app).execute("SELECT id FROM comments").fetchone()[0]
    eve = make_client()
    register(eve, email="eve@example.com", name="Eve")
    assert post(eve, f"/e/{pid}/comments/{cid}/delete").status_code == 403
    assert post(client, f"/e/{pid}/comments/{cid}/delete").status_code == 302  # автор події модерує
    assert db(app).execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


# ---------- обкладинки ----------

def _upload(client, app, filename, content, **over):
    data = event_form(**over)
    with app.app_context():
        data["starts_at"] = to_local_input(future())
    data["cover"] = (io.BytesIO(content), filename)
    return post(client, "/events/new", data, content_type="multipart/form-data")


def test_valid_cover_is_reencoded_with_random_name(client, app):
    register(client)
    r = _upload(client, app, "../../evil.png", image_bytes("PNG"))
    assert r.status_code == 302
    cover = db(app).execute("SELECT cover FROM events").fetchone()[0]
    assert cover.endswith(".png") and "evil" not in cover and len(cover) == 36
    img = client.get(f"/uploads/{cover}")
    assert img.status_code == 200 and img.headers["X-Content-Type-Options"] == "nosniff"


def test_svg_and_html_covers_rejected(client, app):
    register(client)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert _upload(client, app, "x.svg", svg).status_code == 422
    assert _upload(client, app, "x.png", b"<html><script>alert(1)</script></html>").status_code == 422
    # PNG-сигнатура + сміття
    assert _upload(client, app, "x.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 10).status_code == 422
    assert db(app).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_oversized_upload_rejected(client, app):
    register(client)
    # 7 МБ — помилка біля поля, форма зберігається
    r = _upload(client, app, "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (7 * 1024 * 1024))
    assert r.status_code == 422
    body = r.get_data(as_text=True)
    assert "максимум 5 МБ" in body and "Настолки" in body
    # 20 МБ — запит відхиляється одразу, без читання в пам'ять
    r = _upload(client, app, "huge.png", b"\x89PNG\r\n\x1a\n" + b"0" * (20 * 1024 * 1024))
    assert r.status_code == 413
    assert db(app).execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_upload_path_traversal(client):
    assert client.get("/uploads/..%2f..%2frun.py").status_code == 404
    assert client.get("/uploads/../run.py").status_code == 404


# ---------- загальне ----------

def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_errors_do_not_leak_internals(app, client):
    @app.route("/boom")
    def boom():
        raise RuntimeError("SELECT * FROM secret_table")

    r = client.get("/boom")
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert "secret_table" not in body and "Traceback" not in body


def test_search_wildcards_and_paging(client, app):
    register(client)
    create_event(client, app, title="100% веселощів")
    create_event(client, app, title="Інша подія")
    body = client.get("/?q=%25").get_data(as_text=True)
    assert "100% веселощів" in body and "Інша подія" not in body
    assert client.get("/?page=abc").status_code == 200
    assert client.get("/?page=-5").status_code == 200


def test_search_is_case_insensitive_for_cyrillic(client, app):
    register(client)
    create_event(client, app, title="Настолки у Львові")
    assert "Настолки у Львові" in client.get("/?q=НАСТОЛКИ").get_data(as_text=True)
    assert "Настолки у Львові" in client.get("/?q=львові").get_data(as_text=True)


def test_shared_ip_can_register_many_users(app, make_client):
    # перевіряльники в одній аудиторії мають спільний IP — реєстрація не повинна блокуватись
    app.config["RATELIMIT_ENABLED"] = True
    for i in range(15):
        assert register(make_client(), email=f"grader{i}@example.com", name=f"Grader {i}").status_code == 302


def test_profile_lists(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app, title="Моя подія")
    bob = make_client()
    register(bob, email="bob@example.com", name="Bob")
    post(bob, f"/e/{pid}/join")
    assert "Моя подія" in client.get("/me").get_data(as_text=True)
    me = bob.get("/me").get_data(as_text=True)
    assert "Я йду (1)" in me and "Моя подія" in me
