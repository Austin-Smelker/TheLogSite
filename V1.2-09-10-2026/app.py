"""
app.py — Flask application for the running log.

===============================================================================
NEW TO FLASK? READ THIS FIRST.
===============================================================================
Flask is a Python "web framework" — a toolkit for writing programs that
respond to web browsers. Here's the core idea, in plain terms:

1. A browser (or your phone) sends a "request" to a URL, like
   "GET /shoes" (GET = just show me the page) or
   "POST /runs/save" (POST = here's some data, please save it).
2. Flask looks at the URL and the method (GET/POST/etc.), finds the
   Python function you've connected to it, and runs that function.
3. Whatever that function returns (usually rendered HTML, sometimes a
   redirect or some JSON) gets sent back to the browser as the
   "response".

You connect a URL to a function with a "decorator" — that's the line
starting with @ right above a function, like:

    @app.route("/shoes")
    def shoes():
        ...

The @app.route("/shoes") line means "when someone visits /shoes, call
the shoes() function below me". You'll see this pattern (decorator,
then a function definition) everywhere in this file — each one is a
separate page or action the site can respond to.

A few other Python things used a lot in this file, in case they're
new to you:
  - A "decorator" in general (not just @app.route) is a function that
    wraps another function to add behavior without changing its code.
    @login_required (defined below) is one we wrote ourselves — it
    checks the person is logged in *before* letting the real route
    function run.
  - "with open(...) as f" / context managers aren't used much here
    since the database library (sqlite3) handles opening/closing
    connections inside the helper functions in db.py instead.
  - f-strings like f"Hello {name}" are just a way to build a string
    with a variable's value inserted into it.

This file (app.py) is the "backend" — it decides what data to show and
handles saving new data. The actual HTML layout lives in the
templates/ folder (using a templating language called Jinja2, which
lets HTML files include {{ variables }} and {% if/for %} logic). The
look of the site (colors, spacing, fonts) lives in static/style.css,
and small bits of browser-side interactivity (like opening a popup
without reloading the page) live in static/app.js.

===============================================================================
WHAT THIS SITE DOES
===============================================================================
Pages:
  /                 My home — my own run log, editable
  /u/<username>     Someone else's run log — read-only, commentable
  /shoes            My shoe rack
  /team             My Team — weekly grid across everyone
  /team/day/<date>  Day view — everyone's runs on one date
  /settings         Account settings (username, theme, notifications, premium)
  /login /register  Auth

Run with:  python app.py
"""

# ------------------------------------------------------------------------
# IMPORTS
# "import x" pulls in code someone else already wrote so we can use it.
# The first block below is all from Python's own standard library (comes
# with Python, no installation needed). The second block is from
# third-party packages listed in requirements.txt — those DO need
# `pip install -r requirements.txt` before this file will run.
# ------------------------------------------------------------------------
import base64  # encodes binary data (like cryptographic keys) as plain text
import io  # lets us treat a chunk of bytes in memory like a file, for image uploads
import json  # converts Python data <-> the JSON text format (used by push notifications)
import math  # just used for one calculation: rounding chart axis values to "nice" numbers
import re  # "regular expressions" — pattern matching for text, e.g. validating a username's shape
import secrets  # generates random, hard-to-guess strings/tokens (for security)
import sqlite3  # lets Python catch the specific error SQLite raises on duplicate data
from collections import defaultdict  # a dict that auto-creates a default value for new keys
from datetime import date, datetime, time as dtime, timedelta, timezone  # working with dates/times
from zoneinfo import ZoneInfo  # looks up real-world timezones (e.g. "America/New_York")
from functools import wraps  # helper for writing our own decorators (see login_required below)
from pathlib import Path  # a friendlier way to work with file paths than plain strings

# Third-party packages (installed via requirements.txt):
from cryptography.hazmat.primitives import serialization  # saving/loading cryptographic keys
from cryptography.hazmat.primitives.asymmetric import ec  # the specific key type push needs
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash  # password hashing

import db  # our own file, db.py — everything that touches the database lives there

# pywebpush is an optional dependency: the rest of the site works fine
# without it, but push notifications silently no-op (each send just logs
# and returns False) until it's installed — see requirements.txt.
# This "try/except ImportError" pattern means: try to import it, and if
# it's not installed, don't crash the whole program — just remember that
# it's unavailable (via the True/False flag) so other code can check.
try:
    from pywebpush import WebPushException, webpush

    PYWEBPUSH_AVAILABLE = True
except ImportError:
    PYWEBPUSH_AVAILABLE = False

# Pillow (the "PIL" package) validates that an uploaded file is actually a
# real, decodable image (not just something with a .jpg extension) and
# re-encodes it, which also strips out any non-image data riding along in
# the file. Same optional-dependency pattern as above.
try:
    from PIL import Image, UnidentifiedImageError

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# This line creates "the app" — the central Flask object that everything
# else (routes, config, etc.) attaches to. __name__ just tells Flask
# which file it's running from, so it can find the templates/ and
# static/ folders correctly.
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

# Silent hours are always interpreted in US Eastern Time (America/New_York),
# regardless of where this server actually runs or what timezone a user is
# physically in — this correctly switches between EST (winter) and EDT
# (summer daylight saving) automatically, which is what people mean by
# "Eastern time" day-to-day. ZoneInfo looks up the real rules for that
# named timezone (including exactly when DST starts/ends each year).
EASTERN_TIME = ZoneInfo("America/New_York")

# Path(__file__).parent means "the folder this app.py file is sitting in" —
# so these files always live right next to app.py no matter where you run
# the command from.
SECRET_KEY_PATH = Path(__file__).parent / ".secret_key"
VAPID_PRIVATE_KEY_PATH = Path(__file__).parent / ".vapid_private_key.pem"
VAPID_PUBLIC_KEY_PATH = Path(__file__).parent / ".vapid_public_key.txt"
VAPID_CLAIM_EMAIL = "admin@example.com"  # only seen by push services, not shown to users

UPLOAD_DIR = Path(__file__).parent / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)  # create the folder now if it doesn't exist yet
MAX_UPLOAD_BYTES = 6 * 1024 * 1024  # 6MB per file, enforced both client-side (hint) and here
ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


def get_or_create_secret_key():
    """Flask uses a "secret key" to cryptographically sign the login-session
    cookie, so a user can't forge or tamper with their own session data
    (like pretending to be logged in as someone else). This function reads
    that key from a file if we've already made one, or creates a new
    random one the very first time the app runs. Generated once and
    reused across restarts so people don't get logged out every time you
    restart the server. Never checked into the delivered zip — it's
    created fresh on the machine that actually runs the app."""
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text().strip()
    key = secrets.token_hex(32)  # a random 64-character string
    SECRET_KEY_PATH.write_text(key)
    return key


def get_or_create_vapid_keys():
    """VAPID keys are a public/private key pair (like a lock and its key)
    that let push notification services (run by Apple, Google, etc.)
    verify that a push really is coming from this specific app install,
    not an impostor. We generate this pair once per install and reuse it,
    exactly like the session secret above. Returns a tuple:
    (path to the private key file, the public key as a text string)."""
    if VAPID_PRIVATE_KEY_PATH.exists() and VAPID_PUBLIC_KEY_PATH.exists():
        return str(VAPID_PRIVATE_KEY_PATH), VAPID_PUBLIC_KEY_PATH.read_text().strip()

    # ec.SECP256R1() is the specific "elliptic curve" the Web Push standard
    # requires for these keys — this isn't something you need to
    # understand deeply, just know it's the shape of key the browsers expect.
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
    # The browser's JavaScript needs the public key as a URL-safe base64
    # text string (not raw bytes), so we convert it here.
    public_b64 = base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("utf-8")

    VAPID_PRIVATE_KEY_PATH.write_text(private_pem)
    VAPID_PUBLIC_KEY_PATH.write_text(public_b64)
    return str(VAPID_PRIVATE_KEY_PATH), public_b64


# Run these two functions now, at startup, so the keys exist before any
# page is ever requested.
app.secret_key = get_or_create_secret_key()
VAPID_PRIVATE_KEY_PATH_STR, VAPID_PUBLIC_KEY = get_or_create_vapid_keys()

# app.config holds Flask's own settings. HTTPONLY and SAMESITE are both
# small security hardening steps for the login cookie: HTTPONLY means
# JavaScript running on the page can't read the cookie (only the browser
# and server can), and SAMESITE="Lax" stops the cookie being sent along
# with requests that originate from a *different* website (a common trick
# used in "cross-site request forgery" attacks).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # A little headroom over MAX_UPLOAD_BYTES*3 (three photo slots) for
    # the rest of the multipart form fields. Flask will automatically
    # reject any request bigger than this before our own code even runs.
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES * 3 + 200_000,
)


# Make sure all our database tables exist before the app starts handling
# any requests. Safe to call every time — see db.py for what it does.
db.init_db()

MAX_WEEKS = 10  # how many weeks of history the home page shows at once
QUALITY_CHOICES = list(range(1, 11))  # [1, 2, 3, ... 10] — the run-quality dropdown options
# A "regular expression" (regex) describing what a valid username looks
# like: 3 to 30 characters, letters/numbers/underscore/period/hyphen/space.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\- ]{3,30}$")

GENDER_OPTIONS = ["Male", "Female", "Other"]


def normalize_username(raw):
    """Trim and collapse repeated whitespace so 'john   doe' and leading/
    trailing spaces don't create confusing near-duplicate usernames.
    re.sub(pattern, replacement, text) finds every match of `pattern`
    in `text` and swaps it for `replacement` — here, "one or more
    whitespace characters" (\\s+) each become a single space."""
    return re.sub(r"\s+", " ", (raw or "").strip())


def validate_username(raw, current_user_id=None):
    """Check a proposed username is a valid shape AND not already taken
    by someone else, used both at signup and when renaming later.
    Returns a tuple: (clean_username, error) — if error is not None,
    clean_username will be None, and vice versa. `current_user_id` lets
    someone "rename" to the username they already have without it
    complaining that it's taken."""
    username = normalize_username(raw)
    if not USERNAME_RE.match(username):
        return None, "Username must be 3–30 characters: letters, numbers, spaces, underscore, period, or hyphen."
    existing = db.get_user_by_username(username)
    if existing is not None and existing["id"] != current_user_id:
        return None, "That username is taken."
    return username, None


# ---------------------------------------------------------------------------
# Auth / session helpers
# -----------------------------------------------------------------------------
# "Session" is Flask's built-in way of remembering things about a visitor
# between one page request and the next — under the hood, it stores a
# small signed cookie in the visitor's browser. We put exactly one thing
# in it once someone logs in: their user_id.
# ---------------------------------------------------------------------------

def get_current_user():
    """Look up which user (if any) is logged in on this request, by
    reading the user_id we stashed in their session cookie when they
    logged in. Returns None if nobody's logged in. This gets called a lot
    — practically every page needs to know who's asking for it."""
    uid = session.get("user_id")
    if not uid:
        return None
    return db.get_user(uid)


def login_required(view):
    """This is a decorator FACTORY — a function that returns a wrapped
    version of whatever route function you put it on. Stick
    @login_required above any route, and it'll automatically bounce
    anyone who isn't logged in to the login page, before your route's
    own code ever runs. Example:

        @app.route("/settings")
        @login_required          # <- this line does the checking
        def settings():
            ...                   # <- by the time we get here, we know
                                   #    someone is definitely logged in

    `@wraps(view)` just makes the wrapped function keep the original
    function's name/docs, which Flask needs internally — you can ignore
    it and simply trust it belongs there."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_user() is None:
            # Send them to the login page, remembering (via ?next=...)
            # which page they were actually trying to reach, so we can
            # send them straight there after they log in.
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def safe_next(raw, fallback):
    """Several places let the browser say "redirect me back here after
    this action" via a hidden `next` field. If we trusted that blindly, a
    malicious link could set next=https://evil-site.com and trick someone
    into being redirected off our site right after they log in or comment
    (an "open redirect"). So: only ever redirect to a path that starts
    with a single "/" (a page on OUR site), never a full external URL."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return fallback


def get_csrf_token():
    """CSRF stands for Cross-Site Request Forgery — a trick where another
    website tries to make a visitor's browser submit a request to OUR
    site without them meaning to (e.g. a hidden auto-submitting form on
    an attacker's page that posts to /runs/1/delete). The fix: every one
    of our own forms includes a random secret token, and we check it
    matches what we previously stored in that visitor's session before
    accepting the POST. An attacker's page has no way to know this
    token, so their forged request gets rejected.
    This function creates that token the first time it's needed for a
    given visitor, and just returns the same one on every later call."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


@app.context_processor
def inject_globals():
    """A "context processor" runs before EVERY template is rendered, and
    whatever dict it returns becomes available inside that template
    automatically — that's how every .html file in templates/ can use
    {{ current_user }}, {{ csrf_token }}, {{ site_name }}, etc. without
    each individual route having to pass them in by hand every time."""
    return {
        "current_user": get_current_user(),
        "csrf_token": get_csrf_token(),
        "site_name": SITE_NAME,
        "vapid_public_key": VAPID_PUBLIC_KEY,
    }


@app.before_request
def csrf_protect():
    """A "before_request" function runs before every single request,
    for every route, automatically — we don't have to remember to call
    it. Here, we use that to enforce the CSRF check described above on
    every POST request site-wide, in one place, rather than repeating
    the same check inside every single route function.
    secrets.compare_digest (instead of a plain ==) compares the two
    tokens in a way that always takes the same amount of time regardless
    of where they first differ — an ordinary == can theoretically leak
    tiny timing differences an attacker could exploit to guess the
    correct token one character at a time."""
    if request.method == "POST":
        token = session.get("csrf_token")
        # Normal <form> submissions send the token as a hidden field;
        # the two JSON endpoints (push subscribe/unsubscribe) can't do
        # that, so they send it as a header instead. Accept either.
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not token or not supplied or not secrets.compare_digest(token, supplied):
            abort(400, description="Your session expired or the form was tampered with. Go back and try again.")


# ---------------------------------------------------------------------------
# Small formatting / parsing helpers
# -----------------------------------------------------------------------------
# These are little utility functions used all over the rest of the file —
# converting between how a human types a value (like "1:15:00" for a
# time) and how we store/calculate with it (like 4500 seconds).
# ---------------------------------------------------------------------------

def parse_time_to_seconds(value):
    """Turn a run time the user typed — "48:30" (mm:ss) or "1:15:00"
    (h:mm:ss) — into a plain number of seconds, which is much easier to
    add up and compare than a string. Raises a ValueError (which the
    calling code turns into a friendly on-screen message) if the text
    doesn't look like a time at all."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None  # an empty box just means "no time entered" — not an error
    parts = value.split(":")  # "1:15:00" -> ["1", "15", "00"]
    try:
        parts = [int(p) for p in parts]  # turn each piece into a whole number
    except ValueError:
        raise ValueError("Time must look like mm:ss or h:mm:ss (e.g. 48:30 or 1:15:00).")
    if any(p < 0 for p in parts):
        raise ValueError("Time can't be negative.")
    if len(parts) == 1:
        return parts[0] * 60  # just one number typed = treat it as minutes
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise ValueError("Time must look like mm:ss or h:mm:ss (e.g. 48:30 or 1:15:00).")


def format_seconds(total_seconds):
    """The reverse of the function above: turn a number of seconds back
    into a "48:30" or "1:15:00" style string for displaying on screen."""
    if total_seconds is None:
        return ""
    total_seconds = int(total_seconds)
    # divmod(a, b) returns (a // b, a % b) in one step — here, splitting
    # total seconds into whole hours plus the seconds left over, then
    # doing the same again to split those leftover seconds into minutes
    # plus seconds left over.
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"  # :02d pads with a leading zero, e.g. "05" not "5"
    return f"{m}:{s:02d}"


def fmt_month_day(d):
    """Format a date object as "Jul 20" — used for week labels and chart
    axis labels. d.strftime('%b') gives the abbreviated month name."""
    return f"{d.strftime('%b')} {d.day}"


def week_start(d):
    """Given any date, return the Monday of that same week.
    d.weekday() gives 0 for Monday, 1 for Tuesday, ... 6 for Sunday, so
    subtracting that many days from d always lands on the Monday."""
    return d - timedelta(days=d.weekday())


# A regular expression matching exactly the shape of path our own upload
# code generates (see save_uploaded_photo below): /static/uploads/ followed
# by a 32-character random hex filename and a known image extension.
UPLOAD_PATH_RE = re.compile(r"^/static/uploads/[a-f0-9]{32}\.(?:jpg|jpeg|png|gif|webp)$")


def clean_image_url(raw):
    """Check that a pasted image link is safe to store and display.
    Accepts an http(s) link, or one of our own /static/uploads/ paths
    (so editing a run that already has an uploaded photo round-trips
    correctly instead of failing validation on save). Blocks
    javascript:/data: URI tricks via an <img src> — without this check,
    someone could paste "javascript:alert(1)" as an "image link" and
    have it run as code in another visitor's browser when the page
    tries to display it as an image.
    Returns a tuple: (clean_url_or_None, error_message_or_None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, None  # blank is fine — it just means no photo in this slot
    if len(raw) > 2000:
        return None, "Image links must be under 2000 characters."
    if UPLOAD_PATH_RE.match(raw):
        return raw, None
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        return None, "Image links must start with http:// or https://"
    return raw, None


def save_uploaded_photo(file_storage):
    """Handle one uploaded photo file (from a <input type="file"> field).
    `file_storage` is Flask's wrapper object representing the uploaded
    file. Validate + re-encode it with Pillow, save the result under
    static/uploads/ with a random generated filename, and return
    (url_path, error) — exactly one of the two will be None.

    Re-encoding via Pillow (rather than just saving whatever bytes were
    uploaded) does two important things: it PROVES the file is a
    genuine, decodable image — not just something renamed to end in
    ".jpg" — and it strips out anything else that might be hiding
    inside the file alongside the actual image data."""
    if not PILLOW_AVAILABLE:
        return None, "Photo uploads aren't available on this install (missing Pillow)."

    raw = file_storage.read()  # the raw bytes of the uploaded file
    if not raw:
        return None, None  # an empty/unused file input — not an error, just nothing to do
    if len(raw) > MAX_UPLOAD_BYTES:
        return None, f"Photos must be under {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."

    try:
        # io.BytesIO(raw) lets Pillow read the bytes as if they were a
        # file on disk, without us actually writing anything yet.
        img = Image.open(io.BytesIO(raw))
        img.verify()  # raises an error here if this isn't really an image
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
        img = img.convert("RGB")  # JPEG can't store some color modes (like transparency)

    # secrets.token_hex(16) makes a random 32-character filename — this
    # avoids ever trusting the filename the uploader's browser sent us
    # (which could contain tricky characters or clash with another file).
    filename = f"{secrets.token_hex(16)}.{ext}"
    out_path = UPLOAD_DIR / filename
    save_kwargs = {"quality": 88, "optimize": True} if ext == "jpg" else {"optimize": True}
    try:
        img.save(out_path, **save_kwargs)
    except (OSError, ValueError):
        img.save(out_path)  # fall back to plain defaults if the fancy save options ever fail

    # This is the value that ends up stored in the run's image_url_N
    # column — a normal-looking path our own <img src="..."> can use.
    return f"/static/uploads/{filename}", None


def quality_color(avg_quality):
    """Turn a 1-10 quality number into a background color, sliding from
    red (low) to green (high), for the My Team grid cells. HSL color is
    (Hue, Saturation, Lightness) — Hue is a position on a color wheel
    where 0=red and 120=green, so we just scale quality 1-10 onto that
    0-120 range. Saturation/lightness are fixed at soft pastel values so
    the numbers printed on top stay easy to read."""
    if avg_quality is None:
        return None
    q = max(1.0, min(10.0, avg_quality))  # clamp to the 1-10 range just in case
    hue = (q - 1) / 9 * 120
    return f"hsl({hue:.0f}, 55%, 83%)"


# ---------------------------------------------------------------------------
# Notifications
# -----------------------------------------------------------------------------
# How push notifications work, in a nutshell (this matters for reading
# the functions below): when someone taps "Enable notifications" in
# Settings, their BROWSER creates a "subscription" — basically an address
# where messages can be delivered to that specific device — and sends it
# to us to save (see the /push/subscribe route further down). Later, when
# something notification-worthy happens (a comment, a reply, a starred
# user's new run), OUR server sends the actual message to that saved
# address using the pywebpush library, and the browser's operating system
# shows it as a normal notification, even if the site isn't open.
# ---------------------------------------------------------------------------

def parse_hhmm(value):
    """Turn a "22:00"-style string (from a <input type="time"> field)
    into a Python `time` object we can compare against another time.
    Returns None for anything blank or not shaped like that."""
    if not value:
        return None
    try:
        h, m = value.split(":")
        return dtime(int(h), int(m))
    except (ValueError, TypeError):
        return None


def in_silent_hours(user_row, now=None):
    """Is it currently within this user's "do not notify me" window?
    Silent hours are always compared in US Eastern Time (see
    EASTERN_TIME above), no matter what timezone this server's clock is
    set to, or what timezone the user is actually in — so a silent-hours
    setting means the same thing for everyone using the site.
    A range that crosses midnight (e.g. 22:00 to 06:00) wraps correctly
    — see the two comparison branches at the bottom."""
    start = parse_hhmm(user_row["silent_start"])
    end = parse_hhmm(user_row["silent_end"])
    if start is None or end is None:
        return False  # no silent hours configured at all
    now_et = now or datetime.now(EASTERN_TIME)
    if now_et.tzinfo is None:
        # A naive datetime (no timezone attached) is treated as already
        # being Eastern Time, rather than guessing — this matters for
        # tests that construct a plain datetime() to mean "pretend it's
        # this Eastern time".
        now_et = now_et.replace(tzinfo=EASTERN_TIME)
    else:
        now_et = now_et.astimezone(EASTERN_TIME)
    now_t = now_et.time()
    if start <= end:
        # A normal same-day range, e.g. 13:00 to 15:00.
        return start <= now_t <= end
    # A range that wraps past midnight, e.g. 22:00 to 06:00 — "silent"
    # means "at or after 22:00" OR "at or before 06:00" (it can't be both
    # at once, so this OR correctly covers the whole wrapped window).
    return now_t >= start or now_t <= end


def send_push_to_user(user_id, title, body, url):
    """Actually deliver a push notification to every device this user has
    subscribed (someone might have notifications on for both their phone
    and their laptop, say). Returns True if at least one device actually
    received it."""
    if not PYWEBPUSH_AVAILABLE:
        return False  # the optional pywebpush package isn't installed — nothing we can do
    delivered = False
    for sub in db.list_push_subscriptions(user_id):
        try:
            # webpush() is the pywebpush library function that does the
            # actual network call to Apple/Google/Mozilla's push servers,
            # encrypted and signed using our VAPID keys so they can trust
            # it's really coming from us.
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


# Maps each kind of notification to the column in the users table that
# says whether that user wants it. Using a dict like this means
# notify_user (below) can look up the right preference with one line
# instead of a chain of if/elif checks for each kind.
NOTIFICATION_PREF_COLUMN = {
    "comment": "notify_comments",
    "reply": "notify_replies",
    "starred_post": "notify_starred_posts",
}


def notify_user(recipient_id, actor_id, kind, message, url):
    """The single function every part of the app calls when something
    happens that MIGHT deserve a notification — it's responsible for
    deciding whether to actually send one, then doing it. Called with:
      recipient_id — who might get notified
      actor_id     — who did the thing that might trigger it
      kind         — "comment", "reply", or "starred_post"
      message/url  — what the notification should say, and where
                     tapping it should go

    Checked in order, any one of these stops the notification:
      - it's about your own action (no one needs to be told about
        something they just did themselves)
      - the recipient isn't a Premium user (notifications are a premium
        feature — see Settings)
      - the recipient turned this particular kind off
      - it's currently inside the recipient's silent hours
    Every notification that gets this far is both logged (so it shows up
    in their history even if the actual push fails) and pushed to their
    device(s)."""
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
# -----------------------------------------------------------------------------
# The home page doesn't just dump every run in a flat list — it groups
# them into calendar weeks (Monday-Sunday), most recent first, with a
# little "week total" summary row between each group. These two
# functions do that grouping; the actual HTML is built afterwards by
# the template, from the data structure build_weekly_log returns.
# ---------------------------------------------------------------------------

def run_row_to_dict(row):
    """Convert one database row (from db.list_runs_for_user, etc.) into a
    plain Python dict that's easier for the rest of this file and the
    templates to work with — e.g. computing a shoe's display name once
    here, instead of repeating that logic everywhere the shoe is shown."""
    shoe_label = None
    if row["shoe_id"]:
        # Prefer a nickname if the person gave the shoe one, otherwise
        # fall back to "Brand Model".
        shoe_label = row["shoe_nickname"] or f'{row["shoe_brand"]} {row["shoe_model"]}'
    # Build a short list of just the photo URLs that are actually filled
    # in — a run might have 0, 1, 2, or 3 of the three possible slots.
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
        # The raw URLs are kept too (not just the `images` list above) so
        # the edit-run form's JavaScript can pre-fill each individual
        # photo box correctly when someone reopens a run to edit it.
        "image_url_1": row["image_url_1"] or "",
        "image_url_2": row["image_url_2"] or "",
        "image_url_3": row["image_url_3"] or "",
        "menstruating": bool(row["menstruating"]),
        "comments": [],  # filled in later by attach_comments(), once we know which runs we're showing
    }


def build_weekly_log(rows, max_weeks=MAX_WEEKS):
    """Take a flat list of run rows (in any order) and turn them into a
    list of week groups, ready for the home page template to loop over.
    Ordering rules (matching what the page is supposed to show):
      - within a week: earliest date first (so Monday is on top), and
        AM before PM on the same date
      - weeks themselves: MOST RECENT week first, and only the most
        recent `max_weeks` of them are kept at all
    """
    ampm_rank = {"AM": 0, "PM": 1}  # used below so "AM" sorts before "PM"

    # Turn each row's date string into a real `date` object (so it sorts
    # correctly) and pair it with the row itself, then sort by
    # (date, AM-or-PM). Python's sort() is "stable" — items that compare
    # equal keep their original relative order — which is what makes
    # same-date-and-time-of-day runs land in the order they were logged.
    parsed = [(date.fromisoformat(r["date"]), r) for r in rows]
    parsed.sort(key=lambda t: (t[0], ampm_rank.get(t[1]["am_pm"], 2)))

    # Bucket every run into the Monday of its week.
    weeks = {}
    order = []
    for d, r in parsed:
        ws = week_start(d)
        if ws not in weeks:
            weeks[ws] = []
            order.append(ws)
        weeks[ws].append((d, r))

    # sorted(..., reverse=True) puts the latest Monday first; [:max_weeks]
    # then keeps only that many of them.
    week_keys = sorted(weeks.keys(), reverse=True)[:max_weeks]

    result = []
    for ws in week_keys:
        we = ws + timedelta(days=6)  # the Sunday that ends this week
        day_entries = []
        total_distance = 0.0
        total_seconds = 0
        for d, r in weeks[ws]:
            entry = run_row_to_dict(r)
            entry["weekday"] = d.strftime("%a")  # "Mon", "Tue", etc., for display
            day_entries.append(entry)
            # Add up the week's totals as we go, for the little summary
            # banner between weeks (and the totals column on My Team).
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


# ---------------------------------------------------------------------------
# Charts — rendered as plain server-side SVG (no charting library / CDN),
# stacked by activity type so each bar shows its mix of workouts. Each
# activity type gets a distinct fill (a flat color or a repeating pattern)
# rather than relying on hue alone.
# -----------------------------------------------------------------------------
# SVG is an image format made of simple shape instructions (a rectangle
# here, a line there) written as text/XML, rather than pixels — which
# means we can build the whole chart ourselves with plain math and hand
# it to the browser, no JavaScript charting library needed. The general
# idea below: we have a canvas CHART_W x CHART_H pixels, we work out
# where every bar/gridline/label should sit in THAT coordinate space,
# and the actual drawing (turning those numbers into <rect> and <text>
# tags) happens in templates/_chart.html.
# -----------------------------------------------------------------------------

def _slug(text):
    """Turn text into a lowercase-with-hyphens id, e.g. for HTML element
    ids. (Currently unused directly, but kept as a small utility.)"""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# Every activity type gets its own fill so a stacked bar's segments are
# distinguishable even in black and white — some are a flat "solid"
# color, others a repeating "stripes"/"dots" SVG pattern (those patterns
# themselves are defined once in templates/base.html and referenced here
# by the url(#pat-...) id). swatch/swatch_color are used to draw the
# matching little legend squares in CSS.
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

CHART_W, CHART_H = 640, 260  # the SVG "canvas" size, in pixels
# How much empty space to leave around the actual plotted area, for the
# axis numbers/labels to sit in.
CHART_MARGIN = {"left": 42, "right": 10, "top": 14, "bottom": 30}


def nice_axis_max(value):
    """Pick a "nice" round number to use as the chart's top axis value,
    rather than the exact maximum (which might be an odd number like 27
    and make for ugly gridlines like 6.75, 13.5, 20.25...). E.g. for a
    max value of 26, this returns 50 (with gridlines at 0/12.5/25/37.5/50).
    math.log10 + floor finds the "order of magnitude" (10, 100, 1000...)
    just below the value, then we test increasingly large round
    multiples of that (1x, 2x, 2.5x, 5x, 10x) until one is big enough."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * magnitude
        if candidate >= value - 1e-9:  # the tiny 1e-9 avoids float rounding surprises
            return candidate
    return magnitude * 10


def format_axis_value(v):
    """Show whole numbers plainly (10, not 10.0) but keep one decimal
    place for anything that isn't whole (2.5)."""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"


def build_activity_breakdown(weeks):
    """For each week (oldest to newest), add up how many miles and how
    many hours went to each activity type. Returns a list of dicts like
    {label, short_label, distance_by_type: {...}, hours_by_type: {...}}
    — the shared starting point both charts (mileage and time) are built
    from below."""
    ordered = sorted(weeks, key=lambda w: w["start"])
    breakdown = []
    for week in ordered:
        # defaultdict(float) means "a dict that starts any new key at
        # 0.0" — so `dist_by_type[some_type] += x` works even the very
        # first time that activity type is seen, no need to check first.
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
    """Turn the weekly breakdown into exact pixel coordinates for one
    chart (either the mileage chart or the time chart, depending on
    whether value_key is "distance_by_type" or "hours_by_type").
    Returns everything templates/_chart.html needs to draw it: each
    bar's position/width and its stack of colored segments, the
    horizontal gridlines and their labels, and which activity types
    actually appeared (for the legend)."""
    # The usable drawing area, after subtracting the margins.
    plot_w = CHART_W - CHART_MARGIN["left"] - CHART_MARGIN["right"]
    plot_h = CHART_H - CHART_MARGIN["top"] - CHART_MARGIN["bottom"]
    plot_top = CHART_MARGIN["top"]
    plot_bottom = CHART_MARGIN["top"] + plot_h
    plot_left = CHART_MARGIN["left"]
    plot_right = CHART_W - CHART_MARGIN["right"]

    n = len(breakdown)
    totals = [sum(week[value_key].values()) for week in breakdown]
    y_max = nice_axis_max(max(totals) if totals else 0)

    # Divide the width evenly between however many weeks we're showing,
    # then make each bar a bit narrower than its slot so there's a gap
    # between bars.
    slot_w = plot_w / n if n else plot_w
    bar_w = max(slot_w * 0.58, 4)

    bars = []
    types_used = []
    for i, week in enumerate(breakdown):
        # Center this bar within its slot.
        x = plot_left + i * slot_w + (slot_w - bar_w) / 2
        y_cursor = plot_bottom  # we stack segments upward, starting from the bottom
        segments = []
        # Looping over db.ACTIVITY_TYPES (rather than just this week's
        # data) means segments always stack in the SAME order across
        # every bar, so e.g. "Long run" is always in the same visual
        # position whenever it appears — much easier to compare bars.
        for activity_type in db.ACTIVITY_TYPES:
            val = week[value_key].get(activity_type, 0)
            if val <= 0:
                continue
            if activity_type not in types_used:
                types_used.append(activity_type)
            h = (val / y_max) * plot_h if y_max else 0  # scale this value onto our pixel height
            y_cursor -= h  # move the "current top" up by this segment's height
            segments.append(
                {
                    "y": round(y_cursor, 1),
                    "height": round(max(h, 0.5), 1),  # never fully zero-height, or it'd be invisible
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

    # Work out 5 evenly-spaced horizontal gridlines (0%, 25%, 50%, 75%,
    # 100% of the way up to y_max) and where each one sits in pixels.
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
    """The one function the home-page route actually calls: build BOTH
    charts (mileage and time) from the same underlying weekly data, plus
    a combined legend listing every activity type that appears in
    either chart (so a type used only in the time chart still gets a
    legend entry, and vice versa, but each type only appears once)."""
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
    parent_id column pointing to whichever comment they're replying to,
    or nothing for a top-level comment) into a nested structure: a list
    of top-level comments, each carrying a `replies` list underneath it.
    Only one level deep — replying to a reply still attaches to the
    ORIGINAL top-level comment (enforced back where replies are saved,
    in add_run_comment below), which keeps this — and the template that
    displays it — simple, with no need to handle infinitely deep
    nesting."""
    by_id = {}
    top_level = []
    # First pass: create every comment's dict entry (with an empty
    # replies list to fill in next) so we can look any of them up by id.
    for c in flat_comments:
        entry = dict(c)
        entry["replies"] = []
        by_id[c["id"]] = entry
    # Second pass: now that every entry exists, slot each comment into
    # either its parent's replies list, or the top-level list if it has
    # no parent.
    for c in flat_comments:
        entry = by_id[c["id"]]
        parent_id = c["parent_id"]
        if parent_id and parent_id in by_id:
            by_id[parent_id]["replies"].append(entry)
        else:
            top_level.append(entry)
    return top_level


def attach_comments(weeks):
    """Given the week groups build_weekly_log produced, fetch every
    comment on every one of those runs in a single database query
    (rather than one query per run, which would be much slower), thread
    them, and attach the result onto each run dict."""
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
    """Read every field out of the "log a run" form, check each one makes
    sense, and build a clean dict ready to hand to db.add_run/update_run.
    Used for BOTH adding a new run and editing an existing one — they
    share the exact same form fields.

    `form` is Flask's request.form (the text fields), and `files` is
    request.files (the uploaded-photo fields) — kept as separate
    parameters, rather than reading request.form/request.files directly
    in here, so this function can be tested on its own without needing a
    real web request.

    Most fields below follow the same little pattern: read the raw text
    the user typed, and if it's not blank, try to convert it to the
    right type (a number, a date, etc.) inside a try/except — if that
    conversion fails, add a human-readable message to `errors` instead
    of crashing. The route that calls this (save_run, further down)
    shows those messages back to the user and refuses to save anything
    if the list isn't empty — see that this function never talks to the
    database itself, it just parses and validates.

    Returns a tuple: (data_dict, list_of_error_strings)."""
    errors = []
    data = {}
    files = files or {}  # so callers that don't have any files can just leave this out

    run_date = form.get("date", "").strip()
    try:
        date.fromisoformat(run_date)  # this just checks the format is valid; we still store the original string
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
    data["shoe_id"] = int(shoe_id) if shoe_id else None  # "— none —" submits as an empty string

    distance = form.get("distance", "").strip()
    if distance:
        try:
            data["distance"] = float(distance)
        except ValueError:
            errors.append("Distance must be a number.")
            data["distance"] = None
    else:
        data["distance"] = None  # blank is allowed — distance is optional

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
                raise ValueError  # deliberately re-raising to fall into the except below with one message
            data["quality"] = q
        except ValueError:
            errors.append("Run quality must be between 1 and 10.")
            data["quality"] = None
    else:
        data["quality"] = None

    # Each of the 3 photo slots can be filled either by uploading a file
    # or by pasting a link — if BOTH are present for the same slot, the
    # uploaded file wins (see the `if uploaded ... else` below).
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

    # A checkbox that ISN'T checked doesn't send anything at all in the
    # form data (not even "off") — so "was this box checked?" is really
    # "is this field present, and equal to 'on'?".
    # Whether this field actually gets saved is decided in save_run
    # (only meaningful — and only ever shown in the form — for users
    # whose gender is Female), same pattern as the premium-gated photo
    # fields above.
    data["menstruating"] = form.get("menstruating") == "on"

    return data, errors


# ---------------------------------------------------------------------------
# Routes — Auth
# -----------------------------------------------------------------------------
# Everything below this point is organized as one function per URL the
# site responds to, grouped into sections by what they're about (Auth,
# Home/profile, Shoes, My Team, ...). Skim the @app.route(...) line above
# each function to see which URL and HTTP method(s) it handles.
# ---------------------------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    """The sign-up page. GET just shows the empty form; POST is the
    browser submitting it. Flask routes commonly handle both like this
    — one function, branching on request.method — since showing a form
    and processing its submission are so closely related."""
    if get_current_user():
        return redirect(url_for("home"))  # already logged in — nothing to do here

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        gender = request.form.get("gender", "").strip()

        # Collect every validation problem before showing any of them,
        # so the person sees everything wrong with their submission in
        # one go instead of fixing one mistake at a time.
        errors = []
        if not db.EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        username, username_error = validate_username(request.form.get("username", ""))
        if username_error:
            errors.append(username_error)
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if gender not in GENDER_OPTIONS:
            errors.append("Choose a gender.")
        if not errors and db.email_taken(email):
            errors.append("An account with that email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")  # flash() queues a message the NEXT rendered page will show
            return render_template(
                "register.html",
                email=email,
                username=request.form.get("username", ""),
                gender=gender,
                gender_options=GENDER_OPTIONS,
            )

        # generate_password_hash scrambles the password so thoroughly
        # that it can't practically be reversed — we deliberately never
        # store the actual password anywhere, only this hash. Logging in
        # later just re-hashes what they type and checks it matches.
        pw_hash = generate_password_hash(password)
        try:
            user_id = db.create_user(email, username, pw_hash, datetime.now(timezone.utc).isoformat(), gender)
        except sqlite3.IntegrityError:
            # A rare race condition: two people somehow submit the exact
            # same email/username in the same instant. Our earlier check
            # above should already have caught this, but the database's
            # own UNIQUE constraint is the last line of defense.
            flash("That email or username is already in use.", "error")
            return render_template(
                "register.html", email=email, username=username, gender=gender, gender_options=GENDER_OPTIONS
            )

        # session.clear() wipes any old session data before logging them
        # in fresh, then we store their new user_id — from this point on,
        # get_current_user() will recognize them on every future request.
        session.clear()
        session["user_id"] = user_id
        flash(f"Welcome, {username}!", "success")
        return redirect(url_for("home"))

    return render_template("register.html", email="", username="", gender="", gender_options=GENDER_OPTIONS)


@app.route("/login", methods=["GET", "POST"])
def login():
    """The log-in page. Same GET-shows-form / POST-processes-it pattern
    as register() above."""
    if get_current_user():
        return redirect(url_for("home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_username(username)
        # check_password_hash re-hashes the typed password the same way
        # and compares it to what's stored — note we give the exact same
        # "Incorrect username or password" message whether the username
        # doesn't exist at all or the password was just wrong, so an
        # attacker can't use error messages to discover which usernames
        # are registered.
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.", "error")
            return render_template("login.html", username=username)

        session.clear()
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['username']}!", "success")
        # If they got sent here from login_required (e.g. they tried to
        # visit /settings while logged out), send them straight back to
        # wherever they were headed instead of just the home page.
        return redirect(safe_next(request.args.get("next"), url_for("home")))

    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
def logout():
    """Clear the session (forgetting who's logged in) and bounce back to
    the login page. This is a POST rather than a plain link/GET
    specifically so it goes through our CSRF check too — otherwise
    another website could force-log-out a visitor just by getting their
    browser to load an image tag pointing at this URL."""
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.route("/settings")
@login_required
def settings():
    """The Settings page — just gathers everything the template needs
    to display (current username, star count, whether push notifications
    are even possible on this install) and hands off to Jinja."""
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
    """Turn Premium on or off for the logged-in user. This is currently a
    simple self-serve toggle with no actual payment involved — see the
    README for what it's meant to represent long-term."""
    user = get_current_user()
    enable = request.form.get("premium") == "on"
    db.set_premium(user["id"], enable)
    flash("Premium enabled." if enable else "Premium turned off.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/username", methods=["POST"])
@login_required
def update_username_route():
    """Rename the logged-in user. Reuses the same validate_username()
    helper as the sign-up form, so the rules (length, characters,
    uniqueness) stay identical in both places."""
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
    """Switch the logged-in user between light and dark mode. Just saves
    a preference — templates/base.html reads it back and puts it on the
    <html data-theme="..."> attribute, which is what static/style.css's
    dark-mode color overrides key off of."""
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
    """Save which kinds of notifications this user wants, plus their
    silent hours. Notifications are a Premium feature — notice every
    `notify_x` value below is "is_premium AND the checkbox was on", so a
    non-premium user's checkboxes always save as off no matter what was
    actually submitted (defends against someone bypassing the Settings
    page's UI and POSTing here directly)."""
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
    """Called by JavaScript (static/app.js) right after a browser
    creates a push subscription, to hand it to us for safekeeping.
    Note this reads request.get_json() instead of request.form — that's
    because the browser sends this one as JSON, not a normal HTML form
    submission (there's no actual <form> involved, just fetch())."""
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
    """The other half of push_subscribe — called when someone taps "Turn
    off notifications on this device" in Settings."""
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if endpoint:
        db.remove_push_subscription(endpoint)
    return jsonify({"ok": True})


@app.route("/sw.js")
def service_worker():
    """Serves the "service worker" JavaScript file — a special script a
    browser keeps running in the background (separately from any actual
    page/tab) specifically so it can receive and display push
    notifications even when the site isn't open. It has to be served
    from the site's root (/sw.js), not from /static/sw.js, because a
    service worker can only control pages at or below the URL path it
    was loaded from — serving it from /static/ would limit it to only
    ever working on /static/ pages, which don't exist here."""
    return app.response_class(
        (Path(__file__).parent / "static" / "sw.js").read_text(),
        mimetype="application/javascript",
    )


# ---------------------------------------------------------------------------
# Routes — Home / profile
# ---------------------------------------------------------------------------

def render_profile(profile_user, editable):
    """Build and render someone's run-log page — this ONE function
    handles both "my own home page" (editable=True) and "someone else's
    page" (editable=False), since the two are almost identical, just
    with editing controls hidden/blocked in the read-only case. See the
    two tiny route functions right below that each call this with a
    different `editable` value."""
    rows = db.list_runs_for_user(profile_user["id"])
    weeks = build_weekly_log(rows)
    attach_comments(weeks)
    mileage_chart, time_chart, chart_legend = build_charts(weeks)

    # Only bother loading shoe data at all when we're going to show the
    # "log a run" form (which needs a shoe dropdown) — no point doing
    # the extra database work on someone else's read-only page.
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
    """The home page: your own log, fully editable."""
    return render_profile(get_current_user(), editable=True)


@app.route("/u/<username>")
@login_required
def user_profile(username):
    """Someone else's log, read-only. The <username> in the route means
    Flask captures whatever's in that part of the URL and passes it to
    this function as the `username` argument — so /u/jane calls
    user_profile(username="jane")."""
    profile_user = db.get_user_by_username(username)
    if profile_user is None:
        abort(404)  # no such user — show a standard "not found" page
    if profile_user["id"] == get_current_user()["id"]:
        # Visiting your own /u/<you> link — just send them to the real
        # (editable) home page instead of a redundant read-only copy.
        return redirect(url_for("home"))
    return render_profile(profile_user, editable=False)


@app.route("/runs/save", methods=["POST"])
@login_required
def save_run():
    """Handles BOTH adding a new run and saving edits to an existing one
    — whether request.form contains a run_id decides which. This is the
    single most heavily-guarded route in the app: it's where photo
    access (premium-only), the menstruating flag (Female-gender-only),
    and "you can only edit your own stuff" all get enforced, all in one
    place, no matter what the page's UI actually showed the user."""
    user = get_current_user()
    run_id = request.form.get("run_id", "").strip()
    is_premium = bool(user["premium"])

    existing = None
    if run_id:
        # Editing: load the run first so we can both (a) check they
        # actually own it, and (b) fall back to its current photos below
        # if they're not premium.
        existing = db.get_run(int(run_id))
        if existing is None or existing["user_id"] != user["id"]:
            flash("You can only edit your own runs.", "error")
            return redirect(url_for("home"))

    # Only hand the uploaded FILES to parse_run_form if they're premium —
    # for a non-premium user we don't even want to write an uploaded
    # file to disk, since it'll just get thrown away below anyway.
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

    # The menstruating field only ever means anything for a user whose
    # gender is Female — enforced here regardless of what the submitted
    # form contained.
    if user["gender"] != "Female":
        data["menstruating"] = False

    # Make sure a shoe they picked is actually one of THEIR shoes, not
    # something guessed/tampered from another user's shoe rack.
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
        # Tell everyone who has starred this person that they just
        # logged something (subject to each follower's own notification
        # preferences and silent hours — see notify_user).
        run_summary = f"{user['username']} logged {data['activity_type']}"
        if data.get("distance"):
            run_summary += f" ({data['distance']:g} mi)"  # :g trims trailing zeros, e.g. "6" not "6.00"
        for follower_id in db.list_star_followers(user["id"]):
            notify_user(follower_id, user["id"], "starred_post", run_summary, url_for("user_profile", username=user["username"]))
        flash("Run logged.", "success")

    return redirect(url_for("home"))


@app.route("/runs/<int:run_id>/delete", methods=["POST"])
@login_required
def delete_run(run_id):
    """Permanently remove one of YOUR OWN runs. The ownership check
    (existing["user_id"] != user["id"]) is what stops someone deleting
    another person's run just by guessing/crafting a run_id in the URL —
    the "Delete" button is only ever shown for your own runs in the UI,
    but we never rely on the UI alone to enforce that."""
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
    """Post a new comment OR a reply on a run — which one depends on
    whether a parent_id was submitted. Anyone logged in can comment on
    anyone's run (including their own)."""
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

    # Rate-limit: look up when this person last commented (on ANY run)
    # and refuse if it's been under db.COMMENT_COOLDOWN_SECONDS.
    last = db.last_comment_at(user["id"])
    if last:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        if elapsed < db.COMMENT_COOLDOWN_SECONDS:
            flash(f"Please wait a few seconds before commenting again.", "error")
            return redirect(dest)

    db.add_comment(run_id, user["id"], body, datetime.now(timezone.utc).isoformat(), parent_id=parent["id"] if parent else None)
    flash("Reply added." if parent else "Comment added.", "success")

    # Notify whoever should hear about this — but never notify the SAME
    # person twice for one comment. If this is a reply, the parent
    # comment's author gets a "reply" notification; then, only if the
    # run's owner is a DIFFERENT person than that, they separately get a
    # "someone commented on your log" notification (both notify_user
    # calls internally skip notifying someone about their own action too).
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
    """Delete a comment — but ONLY if you're the owner of the log it was
    posted on, not just anyone, and not even the comment's own author
    unless that happens to be the same person."""
    user = get_current_user()
    dest = safe_next(request.form.get("next"), url_for("home"))

    comment = db.get_comment(comment_id)
    if comment is None:
        abort(404)
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
    """Your shoe rack — every shoe you've ever added, active or retired."""
    user = get_current_user()
    return render_template(
        "shoes.html", active_tab="shoes", shoes=db.list_shoes(user["id"]), today=date.today().isoformat()
    )


@app.route("/shoes/add", methods=["POST"])
@login_required
def add_shoe():
    """Add a new shoe to your rack. New shoes start out Active by
    default (see db.add_shoe)."""
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
    """Flip a shoe between Active and Retired (same button does both,
    depending on its current state) — with the same "only your own
    stuff" ownership check used throughout the app."""
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
    """Build the data the My Team page's big grid is drawn from: one row
    per user, one column per day of the given week, each cell showing
    that day's total distance and a red-to-green color based on average
    quality. Also builds `popup_data` — full details for each
    user+day combination that has runs, which gets embedded on the page
    as JSON for the "click a cell to see that day's runs" pop-up (see
    static/app.js) rather than needing a separate page load per click."""
    # Group the flat list of runs by (whose it is, what date) so we can
    # look up "did this person run on this day, and what did they do" in
    # one dictionary lookup instead of scanning the whole list repeatedly.
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
                # The key here (e.g. "3_2026-08-01") is how the
                # JavaScript on the page looks up which pop-up content
                # belongs to which cell when it's clicked.
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
    """The My Team weekly grid. Accepts an optional ?week=YYYY-MM-DD
    query-string parameter (that's how the Prev/Next week links work —
    each just links to the same page with a different date), defaulting
    to the current week if it's missing or invalid."""
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
    """Star another runner — pins them to the top of My Team and turns on
    "notify me when they post" (if you also have that notification type
    and Premium turned on)."""
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
    """The opposite of star_user_route above."""
    viewer = get_current_user()
    dest = safe_next(request.form.get("next"), url_for("team"))
    db.unstar_user(viewer["id"], user_id)
    return redirect(dest)


@app.route("/team/day/<date_str>")
@login_required
def team_day(date_str):
    """The day-view table: everyone's entries for one specific date,
    reached by clicking a day-of-week heading on the My Team grid."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        abort(404)

    users = db.list_users()
    raw_runs = db.list_runs_on_date(d.isoformat())

    # Group this date's runs by whose they are, so the template can loop
    # "for each user, show their runs (if any) for this day".
    by_user = defaultdict(list)
    for r in raw_runs:
        by_user[r["user_id"]].append(run_row_to_dict(r))

    run_ids = [r["id"] for lst in by_user.values() for r in lst]
    comments_by_run = db.list_comments_for_runs(run_ids)
    for lst in by_user.values():
        for r in lst:
            r["comments"] = comments_by_run.get(r["id"], [])

    # Every user gets a row even if they didn't run that day (an empty
    # `runs` list) — that's what makes "everyone shows up, even with
    # nothing logged" work on this page.
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
    # This funny-looking check is a standard Python idiom meaning
    # "only run the code below when this file is executed directly
    # (like `python app.py`), not when some other file imports it".
    # It doesn't matter much here since nothing else imports app.py, but
    # it's a pattern you'll see in almost every Python program's main
    # file, so it's worth recognizing.
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
