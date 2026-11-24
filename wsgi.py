"""WSGI-точка входу для хостингу (PythonAnywhere, gunicorn тощо): `wsgi:application`."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tusovka import create_app  # noqa: E402

application = app = create_app()
