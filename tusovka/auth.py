import re
import sqlite3

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .db import get_db
from .security import login_user, logout_user, now_db, rate_limit, rate_limit_global, safe_next
from .validation import clean_text

bp = Blueprint("auth", __name__)
ph = PasswordHasher()
_DUMMY_HASH = ph.hash("dummy-password-for-timing")

# Частини домену не перетинаються (крапка не входить у [^...]), тож регулярка лінійна — без ReDoS.
_ATOM = r"[^@\s.\x00-\x1f\x7f]+"
EMAIL_RE = re.compile(rf"^[^@\s\x00-\x1f\x7f]{{1,64}}@{_ATOM}(?:\.{_ATOM})+$")
EMAIL_MAX = 254
NAME_MAX = 50
PASSWORD_MIN, PASSWORD_MAX = 8, 128
COMMON_PASSWORDS = {
    "password", "password1", "password123", "qwerty123", "qwertyuiop", "12345678", "123456789",
    "1234567890", "11111111", "00000000", "iloveyou", "admin123", "administrator", "letmein1",
    "welcome1", "abc12345", "qwerty12", "1q2w3e4r", "1qaz2wsx", "zaq12wsx", "passw0rd", "p@ssw0rd",
    "йцукенгш", "пароль123", "aaaaaaaa", "asdfghjk", "superman", "football", "baseball",
}


def valid_email(email):
    # спершу довжина, потім регулярка: величезний рядок не дійде до regex
    return isinstance(email, str) and 3 <= len(email) <= EMAIL_MAX and EMAIL_RE.match(email) is not None


def password_problem(password, email):
    if len(password) < PASSWORD_MIN:
        return f"Пароль — щонайменше {PASSWORD_MIN} символів."
    if len(password) > PASSWORD_MAX:
        return f"Пароль — не довше {PASSWORD_MAX} символів."
    low = password.lower()
    if (low in COMMON_PASSWORDS or len(set(password)) < 4 or low == email
            or low == email.split("@")[0]):
        return "Пароль надто простий — його легко вгадати."
    if not (any(ch.isalpha() for ch in password) and any(ch.isdigit() for ch in password)):
        return "Пароль має містити хоча б одну літеру й одну цифру."
    return None


def _validate_register(form):
    errors = {}
    raw_email = form.get("email") or ""
    raw_name = form.get("name") or ""
    password = form.get("password") or ""
    password2 = form.get("password2") or ""
    email = raw_email.strip().lower() if len(raw_email) <= EMAIL_MAX + 10 else ""
    name = clean_text(raw_name[:NAME_MAX * 4])

    if not valid_email(email):
        errors["email"] = "Вкажіть коректний email."
    if not (2 <= len(name) <= NAME_MAX):
        errors["name"] = f"Ім'я — від 2 до {NAME_MAX} символів."
    problem = password_problem(password, email)
    if problem:
        errors["password"] = problem
    if password != password2:
        errors["password2"] = "Паролі не збігаються."
    return {"email": email[:EMAIL_MAX], "name": name[:NAME_MAX]}, password, errors


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("events.index"))
    values, errors = {}, {}
    next_url = safe_next(request.values.get("next"))
    if request.method == "POST":
        # з одного IP (аудиторія/NAT) можуть реєструватися багато людей — ліміт лише проти ботів
        rate_limit("register", 60, 3600)
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
                return redirect(next_url or url_for("events.index"))
        return render_template("auth/register.html", values=values, errors=errors, next=next_url), 422
    return render_template("auth/register.html", values=values, errors=errors, next=next_url)


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
        # розподілений підбір з багатьох IP на один акаунт
        rate_limit_global(f"login-account:{email}", 50, 900)
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
