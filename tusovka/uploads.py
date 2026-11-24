import io
import os
import re
import uuid
import warnings

from flask import current_app
from PIL import Image, ImageOps

MAX_COVER_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000  # ~100 МБ у пам'яті на декодування — більше не приймаємо
MAX_SIDE = 1600
ALLOWED = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
COVER_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")

# Сигнатури (magic bytes) — перевіряємо до того, як віддати файл Pillow
_MAGIC = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
)


class CoverError(ValueError):
    pass


def _sniff(head):
    for sig, fmt in _MAGIC:
        if head.startswith(sig):
            return fmt
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def save_cover(file_storage):
    """Перевіряє і перекодовує картинку. Повертає нове ім'я файлу (UUID) або None, якщо файл не вибрано."""
    if file_storage is None or not file_storage.filename:
        return None
    data = file_storage.stream.read(MAX_COVER_BYTES + 1)
    if len(data) > MAX_COVER_BYTES:
        raise CoverError("Обкладинка завелика — максимум 5 МБ.")
    if not data:
        raise CoverError("Файл порожній.")
    fmt = _sniff(data[:16])
    if fmt is None:
        raise CoverError("Підтримуються лише JPEG, PNG або WebP.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                if probe.format != fmt:
                    raise CoverError("Вміст файлу не відповідає формату.")
                if probe.width * probe.height > MAX_PIXELS:
                    raise CoverError("Зображення має завелику роздільність.")
                probe.verify()
            with Image.open(io.BytesIO(data)) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                # перекодування прибирає метадані (EXIF/GPS) і будь-який «вкладений» вміст
                if fmt == "JPEG":
                    img = img.convert("RGB")
                elif img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                out = io.BytesIO()
                save_kwargs = {"quality": 85} if fmt in ("JPEG", "WEBP") else {"optimize": True}
                img.save(out, format=fmt, **save_kwargs)
    except CoverError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise CoverError("Зображення має завелику роздільність.")
    except Exception:
        raise CoverError("Не вдалося прочитати зображення. Спробуйте інший файл.")

    name = f"{uuid.uuid4().hex}.{ALLOWED[fmt]}"
    path = os.path.join(current_app.config["UPLOAD_DIR"], name)
    with open(path, "xb") as fh:
        fh.write(out.getvalue())
    return name


def delete_cover(name):
    if not name or not COVER_NAME_RE.match(name):
        return
    try:
        os.remove(os.path.join(current_app.config["UPLOAD_DIR"], name))
    except FileNotFoundError:
        pass
    except OSError:
        current_app.logger.warning("Could not delete cover %s", name)
