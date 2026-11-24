"""Запуск: python run.py  (продакшн-сервер waitress)."""
import os

from waitress import serve

from tusovka import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Тусовка працює на http://{host}:{port}")
    if host not in ("127.0.0.1", "localhost", "::1") and not app.config["PRODUCTION"]:
        print("УВАГА: сервер доступний з мережі, але TUSOVKA_ENV не production — "
              "cookie без Secure, немає HSTS. Для справжніх користувачів встановіть TUSOVKA_ENV=production.")
    # тіло запиту обрізаємо на рівні сервера (за замовчуванням waitress приймає до 1 ГБ),
    # повільні/завислі з'єднання закриваються
    serve(app, host=host, port=port, threads=8,
          max_request_body_size=app.config["MAX_CONTENT_LENGTH"] + 1024 * 1024,
          channel_timeout=60, connection_limit=200)
