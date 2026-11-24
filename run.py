"""Запуск: python run.py  (продакшн-сервер waitress)."""
import os

from waitress import serve

from tusovka import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Тусовка працює на http://{host}:{port}")
    serve(app, host=host, port=port, threads=8)
