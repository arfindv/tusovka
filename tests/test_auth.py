from conftest import csrf, post, register


def test_register_login_logout(client):
    r = register(client)
    assert r.status_code == 302
    assert "Alice" in client.get("/").get_data(as_text=True)
    post(client, "/logout")
    assert client.get("/me").status_code == 302
    r = post(client, "/login", {"email": "ALICE@example.com", "password": "correct-horse-1"})
    assert r.status_code == 302
    assert client.get("/me").status_code == 200


def test_password_hash_not_plaintext(client, app):
    register(client)
    from conftest import db
    row = db(app).execute("SELECT password_hash FROM users").fetchone()
    assert row[0].startswith("$argon2")
    assert "correct-horse-1" not in row[0]


def test_register_validation(client):
    r = post(client, "/register", {"email": "not-an-email", "name": "A", "password": "123", "password2": "456"})
    assert r.status_code == 422
    body = r.get_data(as_text=True)
    assert "коректний email" in body
    assert "8 символів" in body
    assert "не збігаються" in body


def test_duplicate_email(client, make_client):
    register(client)
    other = make_client()
    r = register(other, email="Alice@Example.com")
    assert r.status_code == 422
    assert "вже існує" in r.get_data(as_text=True) or "уже існує" in r.get_data(as_text=True)


def test_wrong_password_and_unknown_email_same_message(client, make_client):
    register(client)
    c = make_client()
    r1 = post(c, "/login", {"email": "alice@example.com", "password": "wrong-password"})
    r2 = post(c, "/login", {"email": "nobody@example.com", "password": "wrong-password"})
    assert r1.status_code == r2.status_code == 401
    assert "Невірний email або пароль" in r1.get_data(as_text=True)
    assert "Невірний email або пароль" in r2.get_data(as_text=True)


def test_csrf_required(client):
    csrf(client)
    r = client.post("/register", data={"email": "a@b.co", "name": "Bob", "password": "x" * 10, "password2": "x" * 10})
    assert r.status_code == 400
    r = client.post("/register", data={"csrf_token": "forged", "email": "a@b.co", "name": "Bob",
                                       "password": "x" * 10, "password2": "x" * 10})
    assert r.status_code == 400


def test_cross_origin_post_rejected(client):
    r = post(client, "/login", {"email": "a@b.co", "password": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_logout_invalidates_server_session(client, make_client, app):
    register(client)
    # «крадемо» cookie сесії
    cookie = client.get_cookie("tusovka_session").value
    post(client, "/logout")
    attacker = make_client()
    attacker.set_cookie("tusovka_session", cookie)
    assert attacker.get("/me").status_code == 302  # стара cookie більше не дає доступу


def test_session_cookie_flags(client):
    r = register(client)
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_open_redirect_blocked(client):
    register(client)
    post(client, "/logout")
    r = post(client, "/login?next=//evil.example", {"email": "alice@example.com", "password": "correct-horse-1"})
    assert r.headers["Location"] == "/"


def test_login_rate_limit(app, client):
    app.config["RATELIMIT_ENABLED"] = True
    codes = [post(client, "/login", {"email": "x@y.zz", "password": "nope-nope"}).status_code for _ in range(12)]
    assert 429 in codes
