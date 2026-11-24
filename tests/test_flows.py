"""Сценарії користувача, які раніше ламались (якість)."""
import re

from conftest import create_event, db, post, register


def test_invited_newcomer_registers_and_returns_to_private_event(client, app, make_client):
    register(client)
    pid, inv = create_event(client, app, visibility="private", title="Закрита вечірка")
    guest = make_client()
    page = guest.get(f"/e/{pid}?invite={inv}").get_data(as_text=True)
    reg = re.search(r'href="(/register\?next=[^"]+)"', page).group(1).replace("&amp;", "&")
    assert guest.get(reg).status_code == 200
    next_val = re.search(r"next=([^\"&]+(?:&amp;invite=[^\"&]+)?)", reg)
    r = post(guest, reg, {"email": "new@example.com", "name": "Newcomer",
                          "password": "new-pass-123", "password2": "new-pass-123"})
    assert r.status_code == 302
    assert r.headers["Location"].startswith(f"/e/{pid}") and inv in r.headers["Location"]
    assert guest.get(r.headers["Location"]).status_code == 200
    assert next_val is not None


def test_login_page_links_to_register_with_next(client):
    page = client.get("/login?next=/e/abc").get_data(as_text=True)
    assert 'href="/register?next=/e/abc"' in page


def test_expired_session_on_post_returns_to_event_not_405(client, app, make_client):
    register(client)
    pid, inv = create_event(client, app, visibility="private")
    register(make_client(), email="bob@example.com", name="Bob")
    anon = make_client()
    r = post(anon, f"/e/{pid}/join", {"invite": inv})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    login_url = r.headers["Location"]
    r = post(anon, login_url, {"email": "bob@example.com", "password": "correct-horse-1"})
    target = r.headers["Location"]
    assert target.startswith(f"/e/{pid}") and inv in target
    assert anon.get(target).status_code == 200
    # нічого не зроблено без явної дії
    assert db(app).execute("SELECT COUNT(*) FROM rsvps").fetchone()[0] == 0


def test_validation_error_keeps_entered_values(client, app):
    register(client)
    r = post(client, "/events/new", {"title": "Моя назва", "description": "Мій опис",
                                     "starts_at": "", "capacity": "0", "visibility": "private"})
    body = r.get_data(as_text=True)
    assert r.status_code == 422
    assert 'value="Моя назва"' in body and "Мій опис" in body
    assert 'value="private" checked' in body


def test_event_page_shows_register_link_for_anonymous(client, app, make_client):
    register(client)
    pid, _ = create_event(client, app)
    page = make_client().get(f"/e/{pid}").get_data(as_text=True)
    assert "Зареєструйтеся" in page and "Увійдіть, щоб записатися" in page


def test_full_user_journey(client, app, make_client):
    """Автор створює → гість записується, коментує, скасовує → автор редагує й видаляє."""
    register(client)
    pid, _ = create_event(client, app, capacity="2", title="Пікнік")
    g = make_client()
    register(g, email="g@example.com", name="Гість")
    assert "Ви записалися" in post(g, f"/e/{pid}/join", follow_redirects=True).get_data(as_text=True)
    assert "1</b> / 2" in g.get(f"/e/{pid}").get_data(as_text=True)
    post(g, f"/e/{pid}/comments", {"body": "Буду з пледом!"})
    assert "Буду з пледом!" in client.get(f"/e/{pid}").get_data(as_text=True)
    assert "Пікнік" in g.get("/me").get_data(as_text=True)
    assert "Запис скасовано" in post(g, f"/e/{pid}/leave", follow_redirects=True).get_data(as_text=True)
    assert "2</b> / 2" in g.get(f"/e/{pid}").get_data(as_text=True)
    assert post(client, f"/e/{pid}/delete").status_code == 302
    assert g.get(f"/e/{pid}").status_code == 404
    assert "Пікнік" not in g.get("/me").get_data(as_text=True)
