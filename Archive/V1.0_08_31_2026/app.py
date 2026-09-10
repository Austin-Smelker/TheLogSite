"""
app.py — Flask application for the running log.

Pages:
  /                 My home — my own run log, editable
  /u/<username>     Someone else's run log — read-only, commentable
  /shoes            My shoe rack
  /team             My Team — weekly grid across everyone
  /team/day/<date>  Day view — everyone's runs on one date
  /login /register  Auth

Run with:  python app.py
"""

import base64
import io
import json
import math
import re
import secrets
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from functools import wraps
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db

# pywebpush is an optional dependency: the rest of the site works fine
# without it, but push notifications silently no-op (each send just logs
# and returns False) until it's installed — see requirements.txt.
try:
    from pywebpush import WebPushException, webpush

    PYWEBPUSH_AVAILABLE = True
except ImportError:
    PYWEBPUSH_AVAILABLE = False

# Pillow validates that an uploaded file is actually a real, decodable
# image (not just something with a .jpg extension) and re-encodes it,
# which also strips out any non-image data riding along in the file.
try:
    from PIL import Image, UnidentifiedImageError

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

app = Flask(__name__)

# =============================================================================
# SITE APPEARANCE / OPERATOR CONFIGURATION
# -----------------------------------------------------------------------------
# Edit the value below to rebrand the site — it feeds the <title> tags and
# the wordmark in the top bar everywhere. For colors (header background,
# page background, text color), see the commented block of CSS custom
# properties near the top of static/style.css — each one says what it
# controls, and there's a separate block for dark mode.
# =============================================================================
SITE_NAME = "TheLogSite.com"

SECRET_KEY_PATH = Path(__file__).parent / ".secret_key"
VAPID_PRIVATE_KEY_PATH = Path(__file__).parent / ".vapid_private_key.pem"
VAPID_PUBLIC_KEY_PATH = Path(__file__).parent / ".vapid_public_key.txt"
VAPID_CLAIM_EMAIL = "admin@example.com"  # only seen by push services, not shown to users

UPLOAD_DIR = Path(__file__).parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 6 * 1024 * 1024  # 6MB per file, enforced both client-side (hint) and here
ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


def get_or_create_secret_key():
    """A per-install secret, generated once and reused across restarts so
    logins survive a server restart. Never checked into the delivered zip."""
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key)
    return key


def get_or_create_vapid_keys():
    """VAPID keys authenticate this server to push services (so they know
    pushes are coming from us) — generated once per install, like the
    session secret. Returns (private_key_pem_path, public_key_b64url)."""
    if VAPID_PRIVATE_KEY_PATH.exists() and VAPID_PUBLIC_KEY_PATH.exists():
        return str(VAPID_PRIVATE_KEY_PATH), VAPID_PUBLIC_KEY_PATH.read_text().strip()

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("utf-8")

    VAPID_PRIVATE_KEY_PATH.write_text(private_pem)
    VAPID_PUBLIC_KEY_PATH.write_text(public_b64)
    return str(VAPID_PRIVATE_KEY_PATH), public_b64


app.secret_key = get_or_create_secret_key()
VAPID_PRIVATE_KEY_PATH_STR, VAPID_PUBLIC_KEY = get_or_create_vapid_keys()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # A little headroom over MAX_UPLOAD_BYTES*3 (three photo slots) for
    # the rest of the multipart form fields.
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES * 3 + 200_000,
)


db.init_db()

MAX_WEEKS = 10
QUALITY_CHOICES = list(range(1, 11))
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\- ]{3,30}$")


def normalize_username(raw):
    """Trim and collapse repeated whitespace so 'john   doe' and leading/
    trailing spaces don't create confusing near-duplicate usernames."""
    return re.sub(r"\s+", " ", (raw or "").strip())


def validate_username(raw, current_user_id=None):
    """Returns (clean_username, error). Spaces are allowed; only checks
    shape and uniqueness (excluding the given user, for renames)."""
    username = normalize_username(raw)
    if not USERNAME_RE.match(username):
        return None, "Username must be 3–30 characters: letters, numbers, spaces, underscore, period, or hyphen."
    existing = db.get_user_by_username(username)
    if existing is not None and existing["id"] != current_user_id:
        return None, "That username is taken."
    return username, None


# ---------------------------------------------------------------------------
# Auth / session helpers
# ---------------------------------------------------------------------------

def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.get_user(uid)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def safe_next(raw, fallback):
    """Only ever redirect to a same-site relative path — blocks open-redirect
    tricks via a crafted `next` value."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return fallback


def get_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "csrf_token": get_csrf_token(),
        "site_name": SITE_NAME,
        "vapid_public_key": VAPID_PUBLIC_KEY,
    }


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("csrf_token")
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not token or not supplied or not secrets.compare_digest(token, supplied):
            abort(400, description="Your session expired or the form was tampered with. Go back and try again.")


# ---------------------------------------------------------------------------
# Small formatting / parsing helpers
# ---------------------------------------------------------------------------

def parse_time_to_seconds(value):
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    parts = value.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        raise ValueError("Time must look like mm:ss or h:mm:ss (e.g. 48:30 or 1:15:00).")
    if any(p < 0 for p in parts):
        raise ValueError("Time can't be negative.")
    if len(parts) == 1:
        return parts[0] * 60
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise ValueError("Time must look like mm:ss or h:mm:ss (e.g. 48:30 or 1:15:00).")


def format_seconds(total_seconds):
    if total_seconds is None:
        return ""
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_month_day(d):
    return f"{d.strftime('%b')} {d.day}"


def week_start(d):
    return d - timedelta(days=d.weekday())


UPLOAD_PATH_RE = re.compile(r"^/static/uploads/[a-f0-9]{32}\.(?:jpg|jpeg|png|gif|webp)$")


def clean_image_url(raw):
    """Accepts an http(s) link, or one of our own /static/uploads/ paths
    (so editing a run that already has an uploaded photo round-trips
    correctly). Blocks javascript:/data: URI injection via an <img src>.
    Returns (clean_url_or_None, error_or_None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if len(raw) > 2000:
        return None, "Image links must be under 2000 characters."
    if UPLOAD_PATH_RE.match(raw):
        return raw, None
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        return None, "Image links must start with http:// or https://"
    return raw, None


def save_uploaded_photo(file_storage):
    """Validate + re-encode an uploaded photo, save it under
    static/uploads/, and return (url_path, error). Re-encoding via
    Pillow both confirms the file is a genuine, decodable image (not
    just something with a .jpg extension) and strips out anything else
    that might be riding along in the file."""
    if not PILLOW_AVAILABLE:
        return None, "Photo uploads aren't available on this install (missing Pillow)."

    raw = file_storage.read()
    if not raw:
        return None, None
    if len(raw) > MAX_UPLOAD_BYTES:
        return None, f"Photos must be under {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."

    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        # verify() leaves the file object unusable for a second decode —
        # reopen it fresh to actually re-encode.
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None, "That file doesn't look like a valid image."

    fmt = (img.format or "").upper()
    ext_by_format = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
    ext = ext_by_format.get(fmt)
    if ext is None:
        return None, "Photos must be JPEG, PNG, GIF, or WEBP."

    if img.mode not in ("RGB", "L") and ext == "jpg":
        img = img.convert("RGB")

    filename = f"{secrets.token_hex(16)}.{ext}"
    out_path = UPLOAD_DIR / filename
    save_kwargs = {"quality": 88, "optimize": True} if ext == "jpg" else {"optimize": True}
    try:
        img.save(out_path, **save_kwargs)
    except (OSError, ValueError):
        img.save(out_path)

    return f"/static/uploads/{filename}", None


def quality_color(avg_quality):
    """Red (low quality) -> green (high quality) on a 1-10 scale, as a soft
    pastel HSL fill so the numbers on top stay readable."""
    if avg_quality is None:
        return None
    q = max(1.0, min(10.0, avg_quality))
    hue = (q - 1) / 9 * 120
    return f"hsl({hue:.0f}, 55%, 83%)"


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def parse_hhmm(value):
    if not value:
        return None
    try:
        h, m = value.split(":")
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        return None


def in_silent_hours(user_row, now=None):
    """Silent hours are compared against this SERVER's local clock — the
    simplest correct behavior for a team on one shared timezone, which is
    the expected use case here. A range that crosses midnight (e.g. 22:00
    to 06:00) wraps correctly."""
    start = parse_hhmm(user_row["silent_start"])
    end = parse_hhmm(user_row["silent_end"])
    if start is None or end is None:
        return False
    now_t = (now or datetime.now()).time()
    if start <= end:
        return start <= now_t <= end
    return now_t >= start or now_t <= end


def send_push_to_user(user_id, title, body, url):
    """Send to every device the user has subscribed. Returns True if at
    least one push was actually delivered."""
    if not PYWEBPUSH_AVAILABLE:
        return False
    delivered = False
    for sub in db.list_push_subscriptions(user_id):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=json.dumps({"title": title, "body": body, "url": url}),
                vapid_private_key=VAPID_PRIVATE_KEY_PATH_STR,
                vapid_claims={"sub": f"mailto:{VAPID_CLAIM_EMAIL}"},
            )
            delivered = True
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                # The browser/OS says this subscription is dead — stop
                # trying to send to it.
                db.remove_push_subscription(sub["endpoint"])
    return delivered


NOTIFICATION_PREF_COLUMN = {
    "comment": "notify_comments",
    "reply": "notify_replies",
    "starred_post": "notify_starred_posts",
}


def notify_user(recipient_id, actor_id, kind, message, url):
    """Gate a notification on the recipient's own preferences and premium
    status, then log it and push it. Never notifies someone about their
    own action. Notifications are a premium feature — a preference left
    over from before a downgrade should never actually fire."""
    if recipient_id == actor_id:
        return
    recipient = db.get_user(recipient_id)
    if recipient is None:
        return
    if not recipient["premium"]:
        return
    pref_col = NOTIFICATION_PREF_COLUMN[kind]
    if not recipient[pref_col]:
        return
    if in_silent_hours(recipient):
        return

    delivered = send_push_to_user(recipient_id, SITE_NAME, message, url)
    db.log_notification(recipient_id, kind, message, url, delivered, datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Building the weekly-grouped log
# ---------------------------------------------------------------------------

def run_row_to_dict(row):
    shoe_label = None
    if row["shoe_id"]:
        shoe_label = row["shoe_nickname"] or f'{row["shoe_brand"]} {row["shoe_model"]}'
    images = [row[f"image_url_{i}"] for i in (1, 2, 3) if row[f"image_url_{i}"]]
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "owner_username": row["owner_username"],
        "date": row["date"],
        "am_pm": row["am_pm"],
        "activity_type": row["activity_type"],
        "shoe_id": row["shoe_id"],
        "shoe_label": shoe_label,
        "distance": row["distance"],
        "time_seconds": row["time_seconds"],
        "time_display": format_seconds(row["time_seconds"]),
        "sleep_hours": row["sleep_hours"],
        "resting_hr": row["resting_hr"],
        "description": row["description"] or "",
        "quality": row["quality"],
        "images": images,
        "image_url_1": row["image_url_1"] or "",
        "image_url_2": row["image_url_2"] or "",
        "image_url_3": row["image_url_3"] or "",
        "comments": [],
    }


def build_weekly_log(rows, max_weeks=MAX_WEEKS):
    ampm_rank = {"AM": 0, "PM": 1}
    parsed = [(date.fromisoformat(r["date"]), r) for r in rows]
    parsed.sort(key=lambda t: (t[0], ampm_rank.get(t[1]["am_pm"], 2)))

    weeks = {}
    order = []
    for d, r in parsed:
        ws = week_start(d)
        if ws not in weeks:
            weeks[ws] = []
            order.append(ws)
        weeks[ws].append((d, r))

    week_keys = sorted(weeks.keys(), reverse=True)[:max_weeks]

    result = []
    for ws in week_keys:
        we = ws + timedelta(days=6)
        day_entries = []
        total_distance = 0.0
        total_seconds = 0
        for d, r in weeks[ws]:
            entry = run_row_to_dict(r)
            entry["weekday"] = d.strftime("%a")
            day_entries.append(entry)
            if entry["distance"]:
                total_distance += entry["distance"]
            if entry["time_seconds"]:
                total_seconds += entry["time_seconds"]
        result.append(
            {
                "start": ws,
                "end": we,
                "label": f"{fmt_month_day(ws)} – {fmt_month_day(we)}, {we.year}",
                "runs": day_entries,
                "total_distance": round(total_distance, 2),
                "total_seconds": total_seconds,
                "total_time_display": format_seconds(total_seconds) if total_seconds else "0:00",
            }
        )
    return result


def chart_data_from_weeks(weeks):
    ordered = sorted(weeks, key=lambda w: w["start"])
    return {
        "labels": [w["label"] for w in ordered],
        "mileage": [w["total_distance"] for w in ordered],
        "hours": [round(w["total_seconds"] / 3600, 2) for w in ordered],
    }


# ---------------------------------------------------------------------------
# Charts — rendered as plain server-side SVG (no charting library / CDN),
# stacked by activity type so each bar shows its mix of workouts. Each
# activity type gets a distinct fill (a flat color or a repeating pattern)
# rather than relying on hue alone.
# ---------------------------------------------------------------------------

def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


ACTIVITY_STYLES = {
    "Aerobic Development Run": {"fill": "#b14a2e", "swatch": "solid", "swatch_color": "#b14a2e"},
    "Long run": {"fill": "#3f6b4a", "swatch": "solid", "swatch_color": "#3f6b4a"},
    "tempo workout": {"fill": "#1e2a24", "swatch": "solid", "swatch_color": "#1e2a24"},
    "interval workout": {"fill": "#c9891f", "swatch": "solid", "swatch_color": "#c9891f"},
    "recovery run": {"fill": "url(#pat-stripes-clay)", "swatch": "stripes", "swatch_color": "#b14a2e"},
    "fartlek": {"fill": "url(#pat-stripes-turf)", "swatch": "stripes", "swatch_color": "#3f6b4a"},
    "shakeout": {"fill": "url(#pat-stripes-ink)", "swatch": "stripes", "swatch_color": "#1e2a24"},
    "cross training": {"fill": "url(#pat-stripes-amber)", "swatch": "stripes", "swatch_color": "#c9891f"},
    "off day": {"fill": "url(#pat-dots-grey)", "swatch": "dots", "swatch_color": "#8a8f80"},
}

CHART_W, CHART_H = 640, 260
CHART_MARGIN = {"left": 42, "right": 10, "top": 14, "bottom": 30}


def nice_axis_max(value):
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * magnitude
        if candidate >= value - 1e-9:
            return candidate
    return magnitude * 10


def format_axis_value(v):
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"


def build_activity_breakdown(weeks):
    """Oldest-to-newest list of {label, short_label, distance_by_type, hours_by_type}."""
    ordered = sorted(weeks, key=lambda w: w["start"])
    breakdown = []
    for week in ordered:
        dist_by_type = defaultdict(float)
        secs_by_type = defaultdict(int)
        for r in week["runs"]:
            if r["distance"]:
                dist_by_type[r["activity_type"]] += r["distance"]
            if r["time_seconds"]:
                secs_by_type[r["activity_type"]] += r["time_seconds"]
        breakdown.append(
            {
                "label": week["label"],
                "short_label": fmt_month_day(week["start"]),
                "distance_by_type": dict(dist_by_type),
                "hours_by_type": {k: v / 3600 for k, v in secs_by_type.items()},
            }
        )
    return breakdown


def build_stacked_chart(breakdown, value_key):
    plot_w = CHART_W - CHART_MARGIN["left"] - CHART_MARGIN["right"]
    plot_h = CHART_H - CHART_MARGIN["top"] - CHART_MARGIN["bottom"]
    plot_top = CHART_MARGIN["top"]
    plot_bottom = CHART_MARGIN["top"] + plot_h
    plot_left = CHART_MARGIN["left"]
    plot_right = CHART_W - CHART_MARGIN["right"]

    n = len(breakdown)
    totals = [sum(week[value_key].values()) for week in breakdown]
    y_max = nice_axis_max(max(totals) if totals else 0)

    slot_w = plot_w / n if n else plot_w
    bar_w = max(slot_w * 0.58, 4)

    bars = []
    types_used = []
    for i, week in enumerate(breakdown):
        x = plot_left + i * slot_w + (slot_w - bar_w) / 2
        y_cursor = plot_bottom
        segments = []
        for activity_type in db.ACTIVITY_TYPES:
            val = week[value_key].get(activity_type, 0)
            if val <= 0:
                continue
            if activity_type not in types_used:
                types_used.append(activity_type)
            h = (val / y_max) * plot_h if y_max else 0
            y_cursor -= h
            segments.append(
                {
                    "y": round(y_cursor, 1),
                    "height": round(max(h, 0.5), 1),
                    "fill": ACTIVITY_STYLES.get(activity_type, {}).get("fill", "#8a8f80"),
                    "activity_type": activity_type,
                    "value": round(val, 2),
                }
            )
        bars.append(
            {
                "x": round(x, 1),
                "width": round(bar_w, 1),
                "label": week["short_label"],
                "full_label": week["label"],
                "segments": segments,
                "total": round(sum(week[value_key].values()), 2),
            }
        )

    gridlines = []
    steps = 4
    for s in range(steps + 1):
        val = y_max * s / steps
        y = plot_bottom - (val / y_max * plot_h if y_max else 0)
        gridlines.append({"y": round(y, 1), "label": format_axis_value(val)})

    return {
        "width": CHART_W,
        "height": CHART_H,
        "plot_left": plot_left,
        "plot_right": plot_right,
        "plot_top": plot_top,
        "plot_bottom": plot_bottom,
        "bars": bars,
        "gridlines": gridlines,
        "types_used": types_used,
    }


def build_charts(weeks):
    breakdown = build_activity_breakdown(weeks)
    mileage_chart = build_stacked_chart(breakdown, "distance_by_type")
    time_chart = build_stacked_chart(breakdown, "hours_by_type")
    legend_types = []
    for t in mileage_chart["types_used"] + time_chart["types_used"]:
        if t not in legend_types:
            legend_types.append(t)
    legend = [{"activity_type": t, **ACTIVITY_STYLES.get(t, {})} for t in legend_types]
    return mileage_chart, time_chart, legend


def thread_comments(flat_comments):
    """Turn a flat, created_at-ordered list of comment rows (with a
    parent_id column) into top-level comments each carrying a `replies`
    list. Only one level deep — a reply to a reply still attaches to the
    original top-level comment, which keeps the UI simple."""
    by_id = {}
    top_level = []
    for c in flat_comments:
        entry = dict(c)
        entry["replies"] = []
        by_id[c["id"]] = entry
    for c in flat_comments:
        entry = by_id[c["id"]]
        parent_id = c["parent_id"]
        if parent_id and parent_id in by_id:
            by_id[parent_id]["replies"].append(entry)
        else:
            top_level.append(entry)
    return top_level


def attach_comments(weeks):
    run_ids = [r["id"] for week in weeks for r in week["runs"]]
    comments_by_run = db.list_comments_for_runs(run_ids)
    for week in weeks:
        for r in week["runs"]:
            r["comments"] = thread_comments(comments_by_run.get(r["id"], []))
            r["comment_count"] = len(comments_by_run.get(r["id"], []))


# ---------------------------------------------------------------------------
# Run form parsing (shared by add + edit)
# ---------------------------------------------------------------------------

def parse_run_form(form, files=None):
    errors = []
    data = {}
    files = files or {}

    run_date = form.get("date", "").strip()
    try:
        date.fromisoformat(run_date)
        data["date"] = run_date
    except ValueError:
        errors.append("Enter a valid date.")

    am_pm = form.get("am_pm", "").strip().upper()
    if am_pm not in ("AM", "PM"):
        errors.append("Choose AM or PM.")
    data["am_pm"] = am_pm

    activity_type = form.get("activity_type", "").strip()
    if activity_type not in db.ACTIVITY_TYPES:
        errors.append("Choose a valid activity type.")
    data["activity_type"] = activity_type

    shoe_id = form.get("shoe_id", "").strip()
    data["shoe_id"] = int(shoe_id) if shoe_id else None

    distance = form.get("distance", "").strip()
    if distance:
        try:
            data["distance"] = float(distance)
        except ValueError:
            errors.append("Distance must be a number.")
            data["distance"] = None
    else:
        data["distance"] = None

    try:
        data["time_seconds"] = parse_time_to_seconds(form.get("time", ""))
    except ValueError as e:
        errors.append(str(e))
        data["time_seconds"] = None

    sleep_hours = form.get("sleep_hours", "").strip()
    if sleep_hours:
        try:
            data["sleep_hours"] = float(sleep_hours)
        except ValueError:
            errors.append("Sleep must be a number of hours.")
            data["sleep_hours"] = None
    else:
        data["sleep_hours"] = None

    resting_hr = form.get("resting_hr", "").strip()
    if resting_hr:
        try:
            data["resting_hr"] = int(resting_hr)
        except ValueError:
            errors.append("Resting heart rate must be a whole number.")
            data["resting_hr"] = None
    else:
        data["resting_hr"] = None

    description = form.get("description", "").strip()
    if len(description) > db.MAX_DESCRIPTION_LENGTH:
        errors.append(f"Description must be under {db.MAX_DESCRIPTION_LENGTH} characters.")
    data["description"] = description

    quality = form.get("quality", "").strip()
    if quality:
        try:
            q = int(quality)
            if q not in QUALITY_CHOICES:
                raise ValueError
            data["quality"] = q
        except ValueError:
            errors.append("Run quality must be between 1 and 10.")
            data["quality"] = None
    else:
        data["quality"] = None

    for i in (1, 2, 3):
        uploaded = files.get(f"image_file_{i}")
        if uploaded is not None and uploaded.filename:
            url, err = save_uploaded_photo(uploaded)
            if err:
                errors.append(err)
            data[f"image_url_{i}"] = url
        else:
            url, err = clean_image_url(form.get(f"image_url_{i}", ""))
            if err:
                errors.append(err)
            data[f"image_url_{i}"] = url

    return data, errors


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if get_current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        errors = []
        if not db.EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        username, username_error = validate_username(request.form.get("username", ""))
        if username_error:
            errors.append(username_error)
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if not errors and db.email_taken(email):
            errors.append("An account with that email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html", email=email, username=request.form.get("username", ""))

        pw_hash = generate_password_hash(password)
        try:
            user_id = db.create_user(email, username, pw_hash, datetime.now(timezone.utc).isoformat())
        except sqlite3.IntegrityError:
            flash("That email or username is already in use.", "error")
            return render_template("register.html", email=email, username=username)

        session.clear()
        session["user_id"] = user_id
        flash(f"Welcome, {username}!", "success")
        return redirect(url_for("home"))

    return render_template("register.html", email="", username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_username(username)
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.", "error")
            return render_template("login.html", username=username)

        session.clear()
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['username']}!", "success")
        return redirect(safe_next(request.args.get("next"), url_for("home")))

    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/settings")
@login_required
def settings():
    user = get_current_user()
    return render_template(
        "settings.html",
        active_tab="settings",
        username_value=user["username"],
        max_stars=db.MAX_STARS,
        star_count=db.count_stars(user["id"]),
        pywebpush_available=PYWEBPUSH_AVAILABLE,
        has_push_subscription=db.has_push_subscription(user["id"]),
    )


@app.route("/settings/premium", methods=["POST"])
@login_required
def update_premium_route():
    user = get_current_user()
    enable = request.form.get("premium") == "on"
    db.set_premium(user["id"], enable)
    flash("Premium enabled." if enable else "Premium turned off.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/username", methods=["POST"])
@login_required
def update_username_route():
    user = get_current_user()
    new_username, error = validate_username(request.form.get("username", ""), current_user_id=user["id"])
    if error:
        flash(error, "error")
    else:
        db.update_username(user["id"], new_username)
        flash("Username updated.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/theme", methods=["POST"])
@login_required
def update_theme_route():
    user = get_current_user()
    theme = request.form.get("theme", "").strip()
    if theme not in ("light", "dark"):
        flash("Choose light or dark.", "error")
        return redirect(url_for("settings"))
    db.update_theme(user["id"], theme)
    return redirect(safe_next(request.form.get("next"), url_for("settings")))


@app.route("/settings/notifications", methods=["POST"])
@login_required
def update_notifications_route():
    user = get_current_user()
    is_premium = bool(user["premium"])
    notify_comments = is_premium and request.form.get("notify_comments") == "on"
    notify_replies = is_premium and request.form.get("notify_replies") == "on"
    notify_starred_posts = is_premium and request.form.get("notify_starred_posts") == "on"

    if not is_premium and (
        request.form.get("notify_comments") == "on"
        or request.form.get("notify_replies") == "on"
        or request.form.get("notify_starred_posts") == "on"
    ):
        flash("Notifications are a premium feature — enable Premium above first.", "error")

    silent_start_raw = request.form.get("silent_start", "").strip()
    silent_end_raw = request.form.get("silent_end", "").strip()
    silent_start = parse_hhmm(silent_start_raw)
    silent_end = parse_hhmm(silent_end_raw)

    errors = []
    if silent_start_raw and silent_start is None:
        errors.append("Silent hours start time isn't valid.")
    if silent_end_raw and silent_end is None:
        errors.append("Silent hours end time isn't valid.")
    if (silent_start_raw and not silent_end_raw) or (silent_end_raw and not silent_start_raw):
        errors.append("Set both a start and an end time for silent hours, or leave both blank.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("settings"))

    db.update_notification_prefs(
        user["id"],
        notify_comments,
        notify_replies,
        notify_starred_posts,
        silent_start_raw or None,
        silent_end_raw or None,
    )
    flash("Notification settings saved.", "success")
    return redirect(url_for("settings"))


@app.route("/push/subscribe", methods=["POST"])
@login_required
def push_subscribe():
    user = get_current_user()
    if not user["premium"]:
        return jsonify({"ok": False, "error": "Notifications are a premium feature."}), 403
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"ok": False, "error": "Malformed subscription."}), 400
    db.add_push_subscription(user["id"], endpoint, keys["p256dh"], keys["auth"], datetime.now(timezone.utc).isoformat())
    return jsonify({"ok": True})


@app.route("/push/unsubscribe", methods=["POST"])
@login_required
def push_unsubscribe():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if endpoint:
        db.remove_push_subscription(endpoint)
    return jsonify({"ok": True})


@app.route("/sw.js")
def service_worker():
    # Served from the root path (not /static/) so its default scope covers
    # the whole site, not just /static/.
    return app.response_class(
        (Path(__file__).parent / "static" / "sw.js").read_text(),
        mimetype="application/javascript",
    )


# ---------------------------------------------------------------------------
# Routes — Home / profile
# ---------------------------------------------------------------------------

def render_profile(profile_user, editable):
    rows = db.list_runs_for_user(profile_user["id"])
    weeks = build_weekly_log(rows)
    attach_comments(weeks)
    mileage_chart, time_chart, chart_legend = build_charts(weeks)

    active_shoes, retired_shoes = [], []
    if editable:
        all_shoes = db.list_shoes(profile_user["id"])
        active_shoes = [s for s in all_shoes if s["active"]]
        retired_shoes = [s for s in all_shoes if not s["active"]]

    return render_template(
        "log.html",
        active_tab="home" if editable else None,
        profile_user=profile_user,
        editable=editable,
        weeks=weeks,
        mileage_chart=mileage_chart,
        time_chart=time_chart,
        chart_legend_items=chart_legend,
        active_shoes=active_shoes,
        retired_shoes=retired_shoes,
        activity_types=db.ACTIVITY_TYPES,
        quality_choices=QUALITY_CHOICES,
        today=date.today().isoformat(),
        has_any_runs=len(rows) > 0,
        max_images=db.MAX_IMAGES_PER_RUN,
    )


@app.route("/")
@login_required
def home():
    return render_profile(get_current_user(), editable=True)


@app.route("/u/<username>")
@login_required
def user_profile(username):
    profile_user = db.get_user_by_username(username)
    if profile_user is None:
        abort(404)
    if profile_user["id"] == get_current_user()["id"]:
        return redirect(url_for("home"))
    return render_profile(profile_user, editable=False)


@app.route("/runs/save", methods=["POST"])
@login_required
def save_run():
    user = get_current_user()
    run_id = request.form.get("run_id", "").strip()
    is_premium = bool(user["premium"])

    existing = None
    if run_id:
        existing = db.get_run(int(run_id))
        if existing is None or existing["user_id"] != user["id"]:
            flash("You can only edit your own runs.", "error")
            return redirect(url_for("home"))

    data, errors = parse_run_form(request.form, request.files if is_premium else None)

    # Photos are a premium feature. Non-premium users never gain new
    # photos here — but editing a run that already has photos (e.g. from
    # before a downgrade) must not silently wipe them, since everyone
    # can still *view* existing photos regardless of premium status.
    if not is_premium:
        if existing is not None:
            data["image_url_1"] = existing["image_url_1"]
            data["image_url_2"] = existing["image_url_2"]
            data["image_url_3"] = existing["image_url_3"]
        else:
            data["image_url_1"] = data["image_url_2"] = data["image_url_3"] = None

    if data.get("shoe_id") is not None:
        shoe = db.get_shoe(data["shoe_id"])
        if shoe is None or shoe["user_id"] != user["id"]:
            errors.append("Choose one of your own shoes.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("home"))

    if run_id:
        db.update_run(int(run_id), data)
        flash("Run updated.", "success")
    else:
        data["user_id"] = user["id"]
        data["created_at"] = datetime.now(timezone.utc).isoformat()
        db.add_run(data)
        run_summary = f"{user['username']} logged {data['activity_type']}"
        if data.get("distance"):
            run_summary += f" ({data['distance']:g} mi)"
        for follower_id in db.list_star_followers(user["id"]):
            notify_user(follower_id, user["id"], "starred_post", run_summary, url_for("user_profile", username=user["username"]))
        flash("Run logged.", "success")

    return redirect(url_for("home"))


@app.route("/runs/<int:run_id>/delete", methods=["POST"])
@login_required
def delete_run(run_id):
    user = get_current_user()
    existing = db.get_run(run_id)
    if existing is None or existing["user_id"] != user["id"]:
        flash("You can only delete your own runs.", "error")
        return redirect(url_for("home"))
    db.delete_run(run_id)
    flash("Run deleted.", "success")
    return redirect(url_for("home"))


@app.route("/runs/<int:run_id>/comments", methods=["POST"])
@login_required
def add_run_comment(run_id):
    user = get_current_user()
    dest = safe_next(request.form.get("next"), url_for("home"))

    run = db.get_run(run_id)
    if run is None:
        abort(404)

    parent_id_raw = request.form.get("parent_id", "").strip()
    parent = None
    if parent_id_raw:
        parent = db.get_comment(int(parent_id_raw))
        if parent is None or parent["run_id"] != run_id:
            flash("That comment no longer exists.", "error")
            return redirect(dest)
        if parent["parent_id"]:
            # Replies are exactly one level deep everywhere in the UI —
            # replying to a reply attaches to its top-level parent instead.
            parent = db.get_comment(parent["parent_id"])

    body = request.form.get("body", "").strip()
    if not body:
        flash("Comment can't be empty.", "error")
        return redirect(dest)
    if len(body) > db.MAX_COMMENT_LENGTH:
        flash(f"Comments are limited to {db.MAX_COMMENT_LENGTH} characters.", "error")
        return redirect(dest)

    last = db.last_comment_at(user["id"])
    if last:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        if elapsed < db.COMMENT_COOLDOWN_SECONDS:
            flash(f"Please wait a few seconds before commenting again.", "error")
            return redirect(dest)

    db.add_comment(run_id, user["id"], body, datetime.now(timezone.utc).isoformat(), parent_id=parent["id"] if parent else None)
    flash("Reply added." if parent else "Comment added.", "success")

    run_url = url_for("user_profile", username=run["owner_username"])
    notified = set()
    if parent is not None and parent["user_id"] != user["id"]:
        notify_user(parent["user_id"], user["id"], "reply", f"{user['username']} replied to your comment", run_url)
        notified.add(parent["user_id"])
    if run["user_id"] not in notified:
        notify_user(run["user_id"], user["id"], "comment", f"{user['username']} commented on your log", run_url)

    return redirect(dest)


@app.route("/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    user = get_current_user()
    dest = safe_next(request.form.get("next"), url_for("home"))

    comment = db.get_comment(comment_id)
    if comment is None:
        abort(404)
    # Only the owner of the log the run belongs to can remove a comment —
    # not just anyone, and not even the comment's own author unless that's
    # also them.
    if comment["run_owner_id"] != user["id"]:
        flash("Only the log owner can delete a comment.", "error")
        return redirect(dest)

    db.delete_comment(comment_id)
    flash("Comment deleted.", "success")
    return redirect(dest)


# ---------------------------------------------------------------------------
# Routes — Shoes
# ---------------------------------------------------------------------------

@app.route("/shoes")
@login_required
def shoes():
    user = get_current_user()
    return render_template(
        "shoes.html", active_tab="shoes", shoes=db.list_shoes(user["id"]), today=date.today().isoformat()
    )


@app.route("/shoes/add", methods=["POST"])
@login_required
def add_shoe():
    user = get_current_user()
    brand = request.form.get("brand", "").strip()
    model = request.form.get("model", "").strip()
    nickname = request.form.get("nickname", "").strip()
    price_raw = request.form.get("price", "").strip()
    date_added = request.form.get("date_added", "").strip()

    errors = []
    if not brand:
        errors.append("Shoe brand is required.")
    if not model:
        errors.append("Shoe model is required.")
    price = None
    if price_raw:
        try:
            price = float(price_raw)
        except ValueError:
            errors.append("Price must be a number.")
    try:
        date.fromisoformat(date_added)
    except ValueError:
        errors.append("Enter a valid date added.")

    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("shoes"))

    db.add_shoe(user["id"], brand, model, nickname or None, price, date_added)
    flash(f"Added {brand} {model} to your shoe rack.", "success")
    return redirect(url_for("shoes"))


@app.route("/shoes/<int:shoe_id>/toggle", methods=["POST"])
@login_required
def toggle_shoe(shoe_id):
    user = get_current_user()
    shoe = db.get_shoe(shoe_id)
    if shoe is None or shoe["user_id"] != user["id"]:
        flash("You can only manage your own shoes.", "error")
        return redirect(url_for("shoes"))
    db.set_shoe_active(shoe_id, not shoe["active"])
    verb = "Reactivated" if not shoe["active"] else "Retired"
    flash(f'{verb} {shoe["brand"]} {shoe["model"]}.', "success")
    return redirect(url_for("shoes"))


# ---------------------------------------------------------------------------
# Routes — My Team
# ---------------------------------------------------------------------------

def build_team_grid(users, monday, runs):
    by_user_date = defaultdict(list)
    for r in runs:
        by_user_date[(r["user_id"], r["date"])].append(r)

    week_dates = [(monday + timedelta(days=i)).isoformat() for i in range(7)]

    grid = []
    popup_data = {}
    for u in users:
        day_cells = []
        week_distance = 0.0
        week_seconds = 0
        for d in week_dates:
            day_runs = by_user_date.get((u["id"], d), [])
            distance = sum(r["distance"] or 0 for r in day_runs)
            seconds = sum(r["time_seconds"] or 0 for r in day_runs)
            qualities = [r["quality"] for r in day_runs if r["quality"] is not None]
            avg_q = sum(qualities) / len(qualities) if qualities else None
            week_distance += distance
            week_seconds += seconds
            has_runs = len(day_runs) > 0
            day_cells.append(
                {
                    "date": d,
                    "distance": round(distance, 2) if has_runs else None,
                    "has_runs": has_runs,
                    "color": quality_color(avg_q),
                }
            )
            if has_runs:
                popup_data[f'{u["id"]}_{d}'] = [
                    {
                        "am_pm": r["am_pm"],
                        "activity_type": r["activity_type"],
                        "distance": r["distance"],
                        "time_display": format_seconds(r["time_seconds"]),
                        "shoe": r["shoe_nickname"] or (f'{r["shoe_brand"]} {r["shoe_model"]}' if r["shoe_brand"] else None),
                        "quality": r["quality"],
                        "description": r["description"] or "",
                    }
                    for r in day_runs
                ]
        grid.append(
            {
                "user": u,
                "days": day_cells,
                "week_distance": round(week_distance, 2),
                "week_time": format_seconds(week_seconds) if week_seconds else "0:00",
            }
        )
    return grid, popup_data


@app.route("/team")
@login_required
def team():
    viewer = get_current_user()
    starred_ids = db.get_starred_ids(viewer["id"])
    # db.list_users() is already alphabetical; a stable sort on "is this
    # one NOT starred" keeps each group (starred, then everyone else)
    # alphabetical internally while putting starred users first.
    users = sorted(db.list_users(), key=lambda u: (u["id"] not in starred_ids, u["username"].lower()))
    today = date.today()

    week_param = request.args.get("week")
    try:
        monday = week_start(date.fromisoformat(week_param)) if week_param else week_start(today)
    except ValueError:
        monday = week_start(today)

    runs = db.list_runs_in_range(monday.isoformat(), (monday + timedelta(days=6)).isoformat())
    grid, popup_data = build_team_grid(users, monday, runs)
    for row in grid:
        row["starred"] = row["user"]["id"] in starred_ids

    return render_template(
        "team.html",
        active_tab="team",
        grid=grid,
        week_days=[monday + timedelta(days=i) for i in range(7)],
        week_label=f"{fmt_month_day(monday)} – {fmt_month_day(monday + timedelta(days=6))}, {(monday + timedelta(days=6)).year}",
        prev_week=(monday - timedelta(days=7)).isoformat(),
        next_week=(monday + timedelta(days=7)).isoformat(),
        is_current_week=(monday == week_start(today)),
        popup_data=popup_data,
        star_count=db.count_stars(viewer["id"]),
        max_stars=db.MAX_STARS,
    )


@app.route("/team/star/<int:user_id>", methods=["POST"])
@login_required
def star_user_route(user_id):
    viewer = get_current_user()
    dest = safe_next(request.form.get("next"), url_for("team"))

    if user_id == viewer["id"]:
        flash("You can't star yourself.", "error")
        return redirect(dest)
    target = db.get_user(user_id)
    if target is None:
        abort(404)

    if db.count_stars(viewer["id"]) >= db.MAX_STARS:
        flash(f"You can star up to {db.MAX_STARS} runners. Unstar someone else first.", "error")
        return redirect(dest)

    db.star_user(viewer["id"], user_id, datetime.now(timezone.utc).isoformat())
    return redirect(dest)


@app.route("/team/unstar/<int:user_id>", methods=["POST"])
@login_required
def unstar_user_route(user_id):
    viewer = get_current_user()
    dest = safe_next(request.form.get("next"), url_for("team"))
    db.unstar_user(viewer["id"], user_id)
    return redirect(dest)


@app.route("/team/day/<date_str>")
@login_required
def team_day(date_str):
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        abort(404)

    users = db.list_users()
    raw_runs = db.list_runs_on_date(d.isoformat())

    by_user = defaultdict(list)
    for r in raw_runs:
        by_user[r["user_id"]].append(run_row_to_dict(r))

    run_ids = [r["id"] for lst in by_user.values() for r in lst]
    comments_by_run = db.list_comments_for_runs(run_ids)
    for lst in by_user.values():
        for r in lst:
            r["comments"] = comments_by_run.get(r["id"], [])

    rows = [{"user": u, "runs": by_user.get(u["id"], [])} for u in users]

    return render_template(
        "team_day.html",
        active_tab="team",
        day=d,
        day_label=f"{d.strftime('%A')}, {fmt_month_day(d)}, {d.year}",
        prev_day=(d - timedelta(days=1)).isoformat(),
        next_day=(d + timedelta(days=1)).isoformat(),
        is_today=(d == date.today()),
        rows=rows,
    )


if __name__ == "__main__":
    import os

    # Listens on every network interface by default, so other devices on
    # your network can reach it at http://<this-machine's-LAN-IP>:5000 —
    # not just http://127.0.0.1:5000 on this machine.
    #
    # Debug mode (the auto-reloader + interactive debugger) is OFF by
    # default on purpose: Werkzeug's debugger lets anyone who can reach a
    # crashed page run arbitrary code, which is fine on localhost-only but
    # not once the site is reachable from other machines. Opt in with
    # FLASK_DEBUG=1 only while developing solo on localhost.
    debug_mode = os.environ.get("FLASK_DEBUG") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug_mode, threaded=True)
