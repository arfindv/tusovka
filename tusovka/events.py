import hmac
import secrets

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from .db import get_db, transaction
from .security import from_db, login_required, now_db, rate_limit, to_db, utcnow
from .uploads import COVER_NAME_RE, CoverError, delete_cover, save_cover
from .validation import COMMENT_MAX, clean_text, to_local_input, validate_event_form

bp = Blueprint("events", __name__)

PAGE_SIZE = 12
SEARCH_MAX = 100

EVENT_COLS = """e.id, e.public_id, e.author_id, e.title, e.description, e.starts_at, e.capacity,
    e.cover, e.is_private, e.invite_token, e.created_at, u.name AS author_name,
    (SELECT COUNT(*) FROM rsvps r WHERE r.event_id = e.id) AS taken"""


# ---------- допоміжне ----------

def new_public_id():
    return secrets.token_urlsafe(9)  # 72 біти, не перебирається


def new_invite_token():
    return secrets.token_urlsafe(24)


def decorate(row):
    ev = dict(row)
    starts = from_db(ev["starts_at"])
    ev["starts_dt"] = starts
    ev["is_past"] = starts <= utcnow()
    ev["free"] = max(ev["capacity"] - ev["taken"], 0)
    return ev


def _fetch_event(public_id):
    if not isinstance(public_id, str) or len(public_id) > 32:
        abort(404)
    row = get_db().execute(
        f"SELECT {EVENT_COLS} FROM events e JOIN users u ON u.id = e.author_id WHERE e.public_id = ?",
        (public_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return decorate(row)


def _is_participant(event_id, user_id):
    return (
        get_db()
        .execute("SELECT 1 FROM rsvps WHERE event_id = ? AND user_id = ?", (event_id, user_id))
        .fetchone()
        is not None
    )


def _invite_from_request():
    token = request.values.get("invite", "")
    return token if isinstance(token, str) and len(token) <= 64 else ""


def load_visible_event(public_id):
    """Подія, яку поточний користувач має право бачити. Для приватної — лише автор,
    учасник або хто прийшов з дійсним токеном-запрошенням. Інакше 404 (не розкриваємо, що подія існує)."""
    ev = _fetch_event(public_id)
    ev["invite"] = ""
    if not ev["is_private"]:
        return ev
    user = g.user
    token = _invite_from_request()
    token_ok = bool(token and ev["invite_token"] and hmac.compare_digest(token, ev["invite_token"]))
    if user is not None and user["id"] == ev["author_id"]:
        ev["invite"] = ev["invite_token"] or ""
        return ev
    if token_ok:
        ev["invite"] = token
        return ev
    if user is not None and _is_participant(ev["id"], user["id"]):
        return ev
    abort(404)


def load_own_event(public_id):
    ev = _fetch_event(public_id)
    if g.user is None or ev["author_id"] != g.user["id"]:
        # приватну чужу подію не «підтверджуємо» навіть кодом 403
        abort(404 if ev["is_private"] else 403)
    ev["invite"] = ev["invite_token"] or ""
    return ev


def event_link(ev, **extra):
    if ev.get("is_private") and ev.get("invite"):
        extra["invite"] = ev["invite"]
    return url_for("events.detail", public_id=ev["public_id"], **extra)


# ---------- головна ----------

def _escape_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@bp.route("/")
def index():
    q = clean_text(request.args.get("q", ""))[:SEARCH_MAX]
    try:
        page = max(1, min(int(request.args.get("page", 1)), 1000))
    except (TypeError, ValueError):
        page = 1
    params = [now_db()]
    where = "e.is_private = 0 AND e.starts_at > ?"
    if q:
        # NOCASE у SQLite працює лише для латиниці — порівнюємо в нижньому регістрі через Python (Unicode)
        where += " AND ulower(e.title) LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(q.lower())}%")
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM events e WHERE {where}", params).fetchone()[0]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    rows = db.execute(
        f"""SELECT {EVENT_COLS} FROM events e JOIN users u ON u.id = e.author_id
            WHERE {where} ORDER BY e.starts_at ASC, e.id ASC LIMIT ? OFFSET ?""",
        [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    return render_template(
        "events/index.html",
        events=[decorate(r) for r in rows],
        q=q,
        page=page,
        pages=pages,
        total=total,
    )


# ---------- створення / редагування ----------

def _event_form_defaults():
    return {"title": "", "description": "", "starts_at": "", "capacity": "20", "visibility": "public"}


@bp.route("/events/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "GET":
        return render_template("events/form.html", values=_event_form_defaults(), errors={}, ev=None)

    rate_limit("create-event", 20, 3600, per_user=True)
    values, data, errors = validate_event_form(request.form)
    cover = None
    if not errors:
        try:
            cover = save_cover(request.files.get("cover"))
        except CoverError as exc:
            errors["cover"] = str(exc)
    if errors:
        return render_template("events/form.html", values=values, errors=errors, ev=None), 422

    db = get_db()
    public_id = new_public_id()
    invite = new_invite_token() if data["is_private"] else None
    now = now_db()
    try:
        db.execute(
            """INSERT INTO events (public_id, author_id, title, description, starts_at, capacity,
                   cover, is_private, invite_token, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (public_id, g.user["id"], data["title"], data["description"], to_db(data["starts_at"]),
             data["capacity"], cover, data["is_private"], invite, now, now),
        )
    except Exception:
        delete_cover(cover)
        raise
    flash("Подію створено!", "success")
    return redirect(url_for("events.detail", public_id=public_id, invite=invite) if invite
                    else url_for("events.detail", public_id=public_id))


@bp.route("/e/<public_id>/edit", methods=["GET", "POST"])
@login_required
def edit(public_id):
    ev = load_own_event(public_id)
    if ev["is_past"]:
        flash("Подія вже почалася — її не можна редагувати.", "error")
        return redirect(event_link(ev))

    if request.method == "GET":
        values = {
            "title": ev["title"],
            "description": ev["description"],
            "starts_at": to_local_input(ev["starts_dt"]),
            "capacity": str(ev["capacity"]),
            "visibility": "private" if ev["is_private"] else "public",
        }
        return render_template("events/form.html", values=values, errors={}, ev=ev)

    values, data, errors = validate_event_form(request.form, current_participants=ev["taken"])
    new_cover = None
    if not errors:
        try:
            new_cover = save_cover(request.files.get("cover"))
        except CoverError as exc:
            errors["cover"] = str(exc)
    if errors:
        return render_template("events/form.html", values=values, errors=errors, ev=ev), 422

    remove_cover = request.form.get("remove_cover") == "1"
    db = get_db()
    try:
        with transaction(db):
            # перевіряємо ще раз усередині транзакції: за цей час могли записатися нові учасники
            taken = db.execute("SELECT COUNT(*) FROM rsvps WHERE event_id = ?", (ev["id"],)).fetchone()[0]
            if data["capacity"] < taken:
                raise _CapacityConflict(taken)
            cover = new_cover or (None if remove_cover else ev["cover"])
            invite = ev["invite_token"]
            if data["is_private"] and not invite:
                invite = new_invite_token()
            db.execute(
                """UPDATE events SET title = ?, description = ?, starts_at = ?, capacity = ?, cover = ?,
                       is_private = ?, invite_token = ?, updated_at = ?
                   WHERE id = ? AND author_id = ?""",
                (data["title"], data["description"], to_db(data["starts_at"]), data["capacity"], cover,
                 data["is_private"], invite, now_db(), ev["id"], g.user["id"]),
            )
    except _CapacityConflict as exc:
        delete_cover(new_cover)
        errors["capacity"] = f"Уже записалося {exc.taken} — не можна зробити менше місць."
        return render_template("events/form.html", values=values, errors=errors, ev=ev), 409
    except Exception:
        delete_cover(new_cover)
        raise

    if cover != ev["cover"]:
        delete_cover(ev["cover"])
    flash("Зміни збережено.", "success")
    ev["is_private"], ev["invite"] = data["is_private"], invite or ""
    return redirect(event_link(ev))


class _CapacityConflict(Exception):
    def __init__(self, taken):
        self.taken = taken


@bp.route("/e/<public_id>/delete", methods=["POST"])
@login_required
def delete(public_id):
    ev = load_own_event(public_id)
    get_db().execute("DELETE FROM events WHERE id = ? AND author_id = ?", (ev["id"], g.user["id"]))
    delete_cover(ev["cover"])
    flash("Подію видалено.", "success")
    return redirect(url_for("events.profile"))


@bp.route("/e/<public_id>/invite", methods=["POST"])
@login_required
def regenerate_invite(public_id):
    ev = load_own_event(public_id)
    if not ev["is_private"]:
        abort(400)
    token = new_invite_token()
    get_db().execute(
        "UPDATE events SET invite_token = ? WHERE id = ? AND author_id = ?", (token, ev["id"], g.user["id"])
    )
    ev["invite"] = token
    flash("Створено нове посилання-запрошення. Старе більше не працює.", "success")
    return redirect(event_link(ev))


# ---------- сторінка події ----------

@bp.route("/e/<public_id>")
def detail(public_id):
    ev = load_visible_event(public_id)
    db = get_db()
    comments = db.execute(
        """SELECT c.id, c.body, c.created_at, c.user_id, u.name FROM comments c
           JOIN users u ON u.id = c.user_id WHERE c.event_id = ? ORDER BY c.id ASC LIMIT 500""",
        (ev["id"],),
    ).fetchall()
    is_author = g.user is not None and g.user["id"] == ev["author_id"]
    going = g.user is not None and _is_participant(ev["id"], g.user["id"])
    participants = []
    if is_author:
        participants = db.execute(
            """SELECT u.name, r.created_at FROM rsvps r JOIN users u ON u.id = r.user_id
               WHERE r.event_id = ? ORDER BY r.created_at ASC""",
            (ev["id"],),
        ).fetchall()
    invite_url = None
    if is_author and ev["is_private"] and ev["invite_token"]:
        invite_url = url_for("events.detail", public_id=ev["public_id"], invite=ev["invite_token"], _external=True)
    return render_template(
        "events/detail.html",
        ev=ev,
        comments=[{**dict(c), "created_dt": from_db(c["created_at"])} for c in comments],
        is_author=is_author,
        going=going,
        participants=[{**dict(p), "created_dt": from_db(p["created_at"])} for p in participants],
        invite_url=invite_url,
        comment_max=COMMENT_MAX,
    )


@bp.route("/e/<public_id>/join", methods=["POST"])
@login_required
def join(public_id):
    ev = load_visible_event(public_id)
    rate_limit("rsvp", 30, 60, per_user=True)
    db = get_db()
    with transaction(db):
        # усе в одній IMMEDIATE-транзакції: перевірка місць і вставка не можуть «перехреститися»
        row = db.execute("SELECT capacity, starts_at FROM events WHERE id = ?", (ev["id"],)).fetchone()
        if row is None:
            abort(404)
        if row["starts_at"] <= now_db():
            msg = ("Подія вже почалася — записатися не можна.", "error")
        elif db.execute(
            "SELECT 1 FROM rsvps WHERE event_id = ? AND user_id = ?", (ev["id"], g.user["id"])
        ).fetchone():
            msg = ("Ви вже записані на цю подію.", "info")
        else:
            cur = db.execute(
                """INSERT INTO rsvps (event_id, user_id, created_at)
                   SELECT ?, ?, ? WHERE (SELECT COUNT(*) FROM rsvps WHERE event_id = ?) < ?""",
                (ev["id"], g.user["id"], now_db(), ev["id"], row["capacity"]),
            )
            msg = (("Ви записалися! До зустрічі.", "success") if cur.rowcount == 1
                   else ("На жаль, вільних місць не залишилося.", "error"))
    flash(*msg)
    return redirect(event_link(ev))


@bp.route("/e/<public_id>/leave", methods=["POST"])
@login_required
def leave(public_id):
    ev = load_visible_event(public_id)
    rate_limit("rsvp", 30, 60, per_user=True)
    db = get_db()
    with transaction(db):
        row = db.execute("SELECT starts_at FROM events WHERE id = ?", (ev["id"],)).fetchone()
        if row is None:
            abort(404)
        if row["starts_at"] <= now_db():
            msg = ("Подія вже почалася — скасувати запис не можна.", "error")
        else:
            cur = db.execute(
                "DELETE FROM rsvps WHERE event_id = ? AND user_id = ?", (ev["id"], g.user["id"])
            )
            msg = (("Запис скасовано.", "success") if cur.rowcount
                   else ("Ви не були записані на цю подію.", "info"))
    flash(*msg)
    return redirect(event_link(ev))


# ---------- коментарі ----------

@bp.route("/e/<public_id>/comments", methods=["POST"])
@login_required
def add_comment(public_id):
    ev = load_visible_event(public_id)
    body = clean_text(request.form.get("body"), multiline=True)
    if not body:
        flash("Коментар не може бути порожнім.", "error")
    elif len(body) > COMMENT_MAX:
        flash(f"Коментар задовгий — максимум {COMMENT_MAX} символів.", "error")
    else:
        rate_limit("comment", 10, 60, per_user=True)
        get_db().execute(
            "INSERT INTO comments (event_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (ev["id"], g.user["id"], body, now_db()),
        )
        flash("Коментар додано.", "success")
    return redirect(event_link(ev, _anchor="comments"))


@bp.route("/e/<public_id>/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(public_id, comment_id):
    ev = load_visible_event(public_id)
    db = get_db()
    row = db.execute(
        "SELECT user_id FROM comments WHERE id = ? AND event_id = ?", (comment_id, ev["id"])
    ).fetchone()
    if row is None:
        abort(404)
    # видалити може автор коментаря або автор події (модерація)
    if g.user["id"] not in (row["user_id"], ev["author_id"]):
        abort(403)
    db.execute("DELETE FROM comments WHERE id = ? AND event_id = ?", (comment_id, ev["id"]))
    flash("Коментар видалено.", "success")
    return redirect(event_link(ev, _anchor="comments"))


# ---------- профіль ----------

@bp.route("/me")
@login_required
def profile():
    db = get_db()
    created = db.execute(
        f"""SELECT {EVENT_COLS} FROM events e JOIN users u ON u.id = e.author_id
            WHERE e.author_id = ? ORDER BY e.starts_at DESC""",
        (g.user["id"],),
    ).fetchall()
    joined = db.execute(
        f"""SELECT {EVENT_COLS} FROM rsvps r JOIN events e ON e.id = r.event_id
            JOIN users u ON u.id = e.author_id WHERE r.user_id = ? ORDER BY e.starts_at DESC""",
        (g.user["id"],),
    ).fetchall()

    def with_access(rows, own):
        out = []
        for r in rows:
            ev = decorate(r)
            ev["invite"] = ev["invite_token"] if own else ""
            out.append(ev)
        return out

    return render_template(
        "events/profile.html", created=with_access(created, True), joined=with_access(joined, False)
    )


# ---------- файли обкладинок ----------

@bp.route("/uploads/<name>")
def upload(name):
    if not COVER_NAME_RE.match(name):
        abort(404)
    resp = send_from_directory(current_app.config["UPLOAD_DIR"], name, max_age=86400)
    resp.headers["Content-Disposition"] = "inline"
    return resp
