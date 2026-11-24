import logging
import os
import secrets
from datetime import timedelta

from flask import Flask, g, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, events
from .db import close_db, init_db
from .security import (
    SESSION_DAYS,
    check_csrf,
    csrf_token,
    load_current_user,
    set_security_headers,
)
from .templating import register_filters

ERROR_TEXT = {
    400: ("Некоректний запит", "Перевірте дані та спробуйте ще раз."),
    401: ("Потрібен вхід", "Увійдіть, щоб продовжити."),
    403: ("Немає доступу", "У вас немає прав на цю дію."),
    404: ("Сторінку не знайдено", "Можливо, подію видалено або посилання неправильне."),
    405: ("Дія недоступна", "Цей метод запиту не підтримується."),
    409: ("Конфлікт", "Дані змінилися. Оновіть сторінку."),
    413: ("Файл завеликий", "Максимальний розмір обкладинки — 5 МБ."),
    429: ("Забагато запитів", "Зачекайте трохи й спробуйте знову."),
    500: ("Щось пішло не так", "Ми вже знаємо про проблему. Спробуйте пізніше."),
}


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path=os.path.join(PROJECT_ROOT, ".env")):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _dev_secret(instance_path):
    os.makedirs(instance_path, exist_ok=True)
    path = os.path.join(instance_path, "dev_secret_key")
    try:
        with open(path, encoding="ascii") as fh:
            value = fh.read().strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(value)
    return value


def create_app(test_config=None):
    _load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    env = os.environ.get("TUSOVKA_ENV", "development")
    production = env == "production"

    secret = os.environ.get("SECRET_KEY", "").strip()
    if not secret:
        if production:
            raise RuntimeError("SECRET_KEY must be set in production")
        # dev: випадковий ключ, збережений в instance/ (не в git) — сесії переживають перезапуск
        secret = _dev_secret(app.instance_path)
    elif len(secret) < 32:
        raise RuntimeError("SECRET_KEY must be at least 32 characters")

    instance = app.instance_path

    def env_path(name, default):
        value = os.environ.get(name, "").strip()
        if not value:
            return default
        return value if os.path.isabs(value) else os.path.join(PROJECT_ROOT, value)

    app.config.update(
        SECRET_KEY=secret,
        DATABASE_PATH=env_path("DATABASE_PATH", os.path.join(instance, "tusovka.db")),
        UPLOAD_DIR=env_path("UPLOAD_DIR", os.path.join(instance, "uploads")),
        APP_TIMEZONE=os.environ.get("APP_TIMEZONE", "").strip() or "Europe/Kyiv",
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        MAX_FORM_MEMORY_SIZE=1024 * 1024,
        MAX_FORM_PARTS=50,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_NAME="tusovka_session",
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
        RATELIMIT_ENABLED=True,
        DEBUG=False,
        PROPAGATE_EXCEPTIONS=False,
    )
    if test_config:
        app.config.update(test_config)

    proxies = int(os.environ.get("TRUSTED_PROXIES", "0") or 0)
    if proxies > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE_PATH"])), exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    init_db(app.config["DATABASE_PATH"])

    logging.basicConfig(level=logging.INFO)
    register_filters(app)
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["event_link"] = events.event_link

    app.register_blueprint(auth.bp)
    app.register_blueprint(events.bp)
    app.teardown_appcontext(close_db)

    @app.before_request
    def _before():
        load_current_user()
        check_csrf()

    app.after_request(set_security_headers)

    @app.route("/healthz")
    def healthz():
        from .db import get_db

        get_db().execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        code = exc.code or 500
        title, text = ERROR_TEXT.get(code, (exc.name, ""))
        if code == 400 and exc.description and "Форма" in str(exc.description):
            text = exc.description
        if "user" not in g:
            g.user = None
        return render_template("error.html", code=code, title=title, text=text), code

    @app.errorhandler(Exception)
    def _unhandled(exc):
        # клієнту — лише загальне повідомлення; деталі — в лог сервера
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        if "user" not in g:
            g.user = None
        title, text = ERROR_TEXT[500]
        return render_template("error.html", code=500, title=title, text=text), 500

    return app
