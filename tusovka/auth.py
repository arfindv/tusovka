import re
import sqlite3

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .db import get_db
from .security import login_user, logout_user, now_db, rate_limit, safe_next
from .validation import clean_text

bp = Blueprint("auth", __name__)
ph = PasswordHasher()
_DUMMY_HASH = ph.hash("dummy-password-for-timing")

EMAIL_RE = re.compile(r"^[^@\s\x00-\x1f\x7f]{1,64}@[^@\s\x00-\x1f\x7f]+\.[^@\s\x00-\x1f\x7f]{2,}$")


def _validate_register(form):
    errors = {}
    email = (form.get("email") or "").strip().lower()
    name = clean_text(form.get("name"))
    password = form.get("password") or ""
    password2 = form.get("password2") or ""

    if not EMAIL_RE.match(email) or len(email) > 254:
        errors["email"] = "Вкажіть коректний email."
    if not (2 <= len(name) <= 50):
        errors["name"] = "Ім'я — від 2 до 50 символів."
    if len(password) < 8:
        errors["password"] = "Пароль — щонайменше 8 символів."
    elif len(password) > 128:
        errors["password"] = "Пароль — не довше 128 символів."
    elif password.strip() == "" or password.lower() == email:
        errors["password"] = "Пароль надто простий."
    if password != password2:
        errors["password2"] = "Паролі не збігаються."
    return {"email": email, "name": name}, password, errors


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("events.index"))
    values, errors = {}, {}
    if request.method == "POST":
        # з одного IP (аудиторія/NAT) можуть реєструватися багато людей — ліміт лише проти ботів
        rate_limit("register", 40, 3600)
        values, password, errors = _validate_register(request.form)
        if not errors:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (values["email"], values["name"], ph.hash(password), now_db()),
                )
            except sqlite3.IntegrityError:
                errors["email"] = "Акаунт з таким email уже існує. Спробуйте увійти."
            else:
                login_user(cur.lastrowid)
                flash("Вітаємо у «Тусовці»!", "success")
                return redirect(url_for("events.index"))
        return render_template("auth/register.html", values=values, errors=errors), 422
    return render_template("auth/register.html", values=values, errors=errors)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("events.index"))
    next_url = safe_next(request.values.get("next"))
    email = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:254]
        password = (request.form.get("password") or "")[:128]
        # загальний ліміт на IP (спільний NAT) + жорсткий на пару IP+email проти підбору пароля
        rate_limit("login", 60, 900)
        rate_limit(f"login-email:{email}", 10, 900)
        row = get_db().execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
        ok = False
        try:
            # для неіснуючого email теж рахуємо хеш — однаковий час відповіді
            ok = ph.verify(row["password_hash"] if row else _DUMMY_HASH, password) and row is not None
        except (VerificationError, InvalidHashError):
            ok = False
        if ok:
            if ph.check_needs_rehash(row["password_hash"]):
                get_db().execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?", (ph.hash(password), row["id"])
                )
            login_user(row["id"])
            flash("Ви увійшли.", "success")
            return redirect(next_url or url_for("events.index"))
        flash("Невірний email або пароль.", "error")
        return render_template("auth/login.html", email=email, next=next_url), 401
    return render_template("auth/login.html", email=email, next=next_url)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("Ви вийшли з акаунта.", "success")
    return redirect(url_for("events.index"))
