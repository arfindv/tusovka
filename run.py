"""Запуск: python run.py  (продакшн-сервер waitress)."""
import os

from waitress import serve

from tusovka import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Тусовка працює на http://{host}:{port}")
    # тіло запиту обрізаємо на рівні сервера (за замовчуванням waitress приймає до 1 ГБ),
    # повільні/завислі з'єднання закриваються
    serve(app, host=host, port=port, threads=8,
          max_request_body_size=app.config["MAX_CONTENT_LENGTH"] + 1024 * 1024,
          channel_timeout=60, connection_limit=200)
