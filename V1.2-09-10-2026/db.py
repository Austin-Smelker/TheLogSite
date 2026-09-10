"""
db.py — SQLite data layer for the running log app.

===============================================================================
NEW TO DATABASES? READ THIS FIRST.
===============================================================================
This file is the ONLY place in the whole project that talks directly to
the database — app.py always goes through a function here (like
db.get_user(5)) instead of writing its own database queries. Keeping it
all in one file makes it much easier to see everything the app can
possibly read or write, and to make sure every query is written safely.

We're using SQLite, which stores the entire database as a single file
on disk (running_log.db, created automatically — see DB_PATH below) —
no separate database server to install or run, which is perfect for a
small app like this.

A relational database organizes data into TABLES (like spreadsheet
tabs) made of ROWS (individual records) and COLUMNS (fields every row
has, like "username" or "distance"). We talk to it using SQL
(Structured Query Language) — a specialized language for describing
"give me these rows" or "change this value" instead of writing a loop
in Python. You'll see four kinds of SQL statement in this file:
  SELECT  — read rows out                  ("get me all shoes for user 5")
  INSERT  — add a new row                   ("add this new run")
  UPDATE  — change existing row(s)          ("mark this shoe retired")
  DELETE  — remove row(s)                   ("delete this comment")

A VERY important security habit shows up in every single query below:
we NEVER build a query by gluing a variable into the SQL text (e.g.
f"SELECT * FROM users WHERE username = '{username}'"). If we did that,
someone could type something devious as a "username" that changes the
meaning of our SQL entirely — a classic attack called "SQL injection".
Instead, every value is passed in separately as a `?` placeholder, e.g.:

    conn.execute("SELECT * FROM users WHERE username = ?", (username,))

The database library fills in the `?` safely no matter what the value
contains, so it's always treated as plain data, never as part of the
SQL command itself.
===============================================================================
"""

import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

# Path(__file__).parent means "the same folder this file is in" — so the
# database file always lives right next to db.py/app.py.
DB_PATH = Path(__file__).parent / "running_log.db"

# The fixed list of activity types shown in the "log a run" dropdown.
# Kept here (not scattered through app.py) since it's really data about
# what the app tracks, similar in spirit to a database table.
ACTIVITY_TYPES = [
    "Aerobic Development Run",
    "recovery run",
    "Long run",
    "shakeout",
    "interval workout",
    "tempo workout",
    "fartlek",
    "off day",
    "cross training",
]

MAX_IMAGES_PER_RUN = 3
MAX_COMMENT_LENGTH = 2000
MAX_DESCRIPTION_LENGTH = 4000
COMMENT_COOLDOWN_SECONDS = 10

# ---------------------------------------------------------------------------
# SCHEMA — the "shape" of the database: every table and what columns
# each row in it has. This whole string is just SQL, run once at
# startup (see init_db below) to make sure every table exists.
#
# A few SQL words that show up a lot below, in plain terms:
#   PRIMARY KEY     — this column uniquely identifies the row (like a
#                     row number). AUTOINCREMENT means SQLite picks the
#                     next number automatically when a row is added.
#   NOT NULL        — this column can't be left empty.
#   UNIQUE          — no two rows can have the same value here (e.g. two
#                     accounts can't share an email).
#   COLLATE NOCASE  — comparisons/uniqueness on this column ignore
#                     upper/lower case, so "Alice" and "alice" count as
#                     the same username.
#   DEFAULT x       — if nothing is specified when adding a row, use x.
#   CHECK (...)     — SQLite itself will refuse to save a row that
#                     breaks this rule (e.g. gender must be one of
#                     exactly three values).
#   REFERENCES      — this column holds the id of a row in ANOTHER
#                     table (a "foreign key") — e.g. every run's
#                     user_id points at a row in the users table. This
#                     is how "which runs belong to this user" is
#                     represented: not by nesting data, but by each run
#                     separately pointing back at its owner.
#   ON DELETE CASCADE — if the row being pointed at is ever deleted,
#                     automatically delete this row too (e.g. deleting a
#                     run deletes its comments, rather than leaving
#                     orphaned comments pointing at nothing).
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 TEXT NOT NULL UNIQUE COLLATE NOCASE,
    username              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash         TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    theme                 TEXT NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark')),
    notify_comments       INTEGER NOT NULL DEFAULT 1,
    notify_replies        INTEGER NOT NULL DEFAULT 1,
    notify_starred_posts  INTEGER NOT NULL DEFAULT 1,
    silent_start          TEXT,
    silent_end            TEXT,
    premium               INTEGER NOT NULL DEFAULT 0,
    gender                TEXT NOT NULL CHECK (gender IN ('Male', 'Female', 'Other'))
);

-- SQLite has no true boolean type, so "yes/no" columns like active,
-- premium, notify_comments, and menstruating below are all stored as
-- an INTEGER that's really only ever 0 (false) or 1 (true).
CREATE TABLE IF NOT EXISTS shoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    brand       TEXT NOT NULL,
    model       TEXT NOT NULL,
    nickname    TEXT,
    price       REAL,
    date_added  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    am_pm           TEXT NOT NULL CHECK (am_pm IN ('AM', 'PM')),
    activity_type   TEXT NOT NULL,
    shoe_id         INTEGER REFERENCES shoes(id) ON DELETE SET NULL,
    distance        REAL,
    time_seconds    INTEGER,
    sleep_hours     REAL,
    resting_hr      INTEGER,
    description     TEXT,
    quality         INTEGER,
    image_url_1     TEXT,
    image_url_2     TEXT,
    image_url_3     TEXT,
    menstruating    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

-- parent_id is what makes a "reply" a reply: it's NULL for an ordinary
-- top-level comment, or another comment's id if this one is a reply to
-- it. See thread_comments() in app.py for how this turns into a nested
-- structure for display.
CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- One row per "user_id has starred starred_user_id". The UNIQUE
-- constraint on the pair stops the same star being added twice.
CREATE TABLE IF NOT EXISTS stars (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    starred_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    UNIQUE(user_id, starred_user_id)
);

-- One row per device that's subscribed to push notifications. endpoint,
-- p256dh, and auth together are exactly what the browser gave us when
-- the person tapped "Enable notifications" — see push_subscribe() in
-- app.py.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- A record of every notification we decided to raise, independent of
-- whether a push actually reached a device. Lets a user's notification
-- history be inspected/debugged, and is how "silent hours"/preferences
-- can be verified — a suppressed notification just never gets a row
-- with delivered=1.
CREATE TABLE IF NOT EXISTS notification_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('comment', 'reply', 'starred_post')),
    message      TEXT NOT NULL,
    url          TEXT,
    delivered    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

-- An INDEX doesn't change what data exists, only how fast SQLite can
-- find it — these tell it to keep an extra, pre-sorted lookup structure
-- for columns we frequently search/filter by, so those particular
-- queries don't have to scan the entire table every time.
CREATE INDEX IF NOT EXISTS idx_runs_user_date ON runs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_comments_run ON comments(run_id);
CREATE INDEX IF NOT EXISTS idx_comments_user_created ON comments(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stars_user ON stars(user_id);
CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subscriptions(user_id);
"""


def get_db():
    """Open a fresh connection to the database file. Every function below
    calls this at the start and conn.close() at the end — SQLite
    connections are cheap enough that opening one per function call
    (rather than trying to share one connection everywhere) keeps things
    simple and avoids a whole category of bugs around sharing a
    connection across different parts of the app."""
    conn = sqlite3.connect(DB_PATH)
    # Normally each row from a query comes back as a plain tuple, like
    # (5, "alice", "alice@example.com") — you'd have to remember that
    # column 0 is the id, column 1 is the username, and so on. Setting
    # row_factory to sqlite3.Row instead lets us access columns by name,
    # like row["username"], which is what you'll see used everywhere in
    # this file and in the Jinja templates.
    conn.row_factory = sqlite3.Row
    # SQLite has foreign keys (like runs.user_id pointing at users.id)
    # OFF by default for backward-compatibility reasons — this line
    # turns them on for this connection so the ON DELETE CASCADE rules
    # above actually take effect.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# =============================================================================
# UPGRADING AN EXISTING DATABASE WITHOUT LOSING DATA
# -----------------------------------------------------------------------------
# running_log.db is never part of the downloaded website files — it's
# created the first time the app runs, and it's the ONE file that holds
# everything a person has entered (accounts, runs, shoes, comments...).
# That separation is what makes it possible to drop a newer version of
# app.py/db.py/templates/static into the same folder and keep using the
# same data: as long as you don't delete or replace running_log.db
# itself, your data survives a code upgrade automatically.
#
# The one thing that needs extra care is when a NEWER version of the
# code expects a column that an OLDER database doesn't have yet (e.g.
# this app didn't always have a "premium" column on users). The
# functions below detect exactly that situation and fix it — by adding
# the missing column(s) with ALTER TABLE, which SQLite can do without
# touching any existing row — every time the app starts, before
# anything else runs. A brand new install (no database file yet) just
# gets every table created with every column already in place, the
# normal way.
#
# HOW TO ADD A NEW COLUMN TO AN EXISTING TABLE IN THE FUTURE:
#   1. Add it to the right table in SCHEMA above (so a BRAND NEW
#      database gets it immediately).
#   2. Add the exact same column name + definition to
#      COLUMNS_ADDED_OVER_TIME below, under the same table.
#   That's it — no other code needs to change. The next time the app
#   starts against an OLDER database that's missing that column, it
#   gets added automatically, existing rows keep every bit of their
#   data, and they get the new column's DEFAULT value for whatever's
#   new. (Adding a whole new TABLE needs no extra step at all — SCHEMA's
#   CREATE TABLE IF NOT EXISTS handles that by itself.)
# =============================================================================

COLUMNS_ADDED_OVER_TIME = {
    "users": {
        "theme": "TEXT NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark'))",
        "notify_comments": "INTEGER NOT NULL DEFAULT 1",
        "notify_replies": "INTEGER NOT NULL DEFAULT 1",
        "notify_starred_posts": "INTEGER NOT NULL DEFAULT 1",
        "silent_start": "TEXT",
        "silent_end": "TEXT",
        "premium": "INTEGER NOT NULL DEFAULT 0",
        # Accounts created before the gender field existed get 'Other'
        # so existing logins keep working — nobody's actual answer is
        # silently guessed or changed, this is purely a technical
        # placeholder so the column (which the rest of the app expects
        # to always have a value) isn't left blank.
        "gender": "TEXT NOT NULL DEFAULT 'Other' CHECK (gender IN ('Male', 'Female', 'Other'))",
    },
    "runs": {
        "image_url_1": "TEXT",
        "image_url_2": "TEXT",
        "image_url_3": "TEXT",
        "menstruating": "INTEGER NOT NULL DEFAULT 0",
    },
    "comments": {
        "parent_id": "INTEGER REFERENCES comments(id) ON DELETE CASCADE",
    },
}


def _table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _existing_columns(conn, table):
    """The set of column names `table` actually has right now. PRAGMA
    table_info returns one row per column; row[1] is that column's
    name."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _find_missing_columns(conn):
    """Compare COLUMNS_ADDED_OVER_TIME against the real, on-disk
    database and return exactly the (table, column, definition) triples
    that are genuinely missing — i.e. where an ALTER TABLE is actually
    needed. Empty list means the database is already fully up to date."""
    missing = []
    for table, columns in COLUMNS_ADDED_OVER_TIME.items():
        if not _table_exists(conn, table):
            continue  # a wholly new table — SCHEMA's CREATE TABLE already gave it every column
        existing = _existing_columns(conn, table)
        for col_name, col_def in columns.items():
            if col_name not in existing:
                missing.append((table, col_name, col_def))
    return missing


def _backup_before_migration():
    """Copy running_log.db to a timestamped backup file, kept forever
    (never auto-deleted), right before we're about to change an
    existing database's structure — a safety net in case anything about
    an upgrade ever goes wrong. Only ever called when a real schema
    change is about to happen, not on every ordinary startup."""
    if not DB_PATH.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = DB_PATH.parent / f"running_log.backup-{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def init_db():
    """Bring the database up to date with whatever this version of the
    app expects — creating it from scratch if it doesn't exist yet, or
    upgrading it in place (see the big comment above) if it does.
    Called unconditionally every time the app starts. On an
    already-up-to-date database this does nothing beyond a handful of
    cheap PRAGMA checks; it only backs up and alters anything when a
    genuinely older database needs it."""
    conn = get_db()
    conn.executescript(SCHEMA)  # creates any BRAND NEW tables; harmless no-op for ones that already exist
    conn.commit()

    missing = _find_missing_columns(conn)
    if missing:
        conn.close()
        _backup_before_migration()
        conn = get_db()
        for table, col_name, col_def in missing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
        conn.commit()

    conn.close()


# ---------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------
# Every function below follows the same small pattern: open a
# connection, run one query, close the connection, return whatever we
# got. .fetchone() gets a single row (or None if there wasn't one);
# .fetchall() gets every matching row as a list.
# ---------------------------------------------------------------------------

# A regular expression for "does this look like a valid email address" —
# not a perfect/complete check (real email validation is notoriously
# hard to get 100% right), just enough to catch obvious typos.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_taken(email):
    """Is there already an account with this email? Used at sign-up to
    give a clear "that email is already registered" message, on top of
    the database's own UNIQUE constraint as a backstop."""
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    conn.close()
    return row is not None


def username_taken(username):
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    conn.close()
    return row is not None


def create_user(email, username, password_hash, created_at, gender):
    """Add a brand new account. Returns the new user's id.
    cur.lastrowid gives us the id SQLite just auto-assigned to the row
    we inserted, without needing a separate SELECT to find it."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (email, username, password_hash, created_at, gender) VALUES (?, ?, ?, ?, ?)",
        (email, username, password_hash, created_at, gender),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_user(user_id):
    """Look up one user by their id. Returns None if no such user."""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def get_user_by_username(username):
    """Look up one user by username (case-insensitive, per COLLATE
    NOCASE — see the schema comments above)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    conn.close()
    return row


def list_users():
    """Every registered user, alphabetical by username — the base list
    the My Team page and day-view table both build on."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE ASC").fetchall()
    conn.close()
    return rows


def update_username(user_id, new_username):
    conn = get_db()
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_username, user_id))
    conn.commit()
    conn.close()


def update_theme(user_id, theme):
    conn = get_db()
    conn.execute("UPDATE users SET theme = ? WHERE id = ?", (theme, user_id))
    conn.commit()
    conn.close()


def set_premium(user_id, premium):
    conn = get_db()
    conn.execute("UPDATE users SET premium = ? WHERE id = ?", (int(premium), user_id))
    conn.commit()
    conn.close()


def update_notification_prefs(user_id, notify_comments, notify_replies, notify_starred_posts, silent_start, silent_end):
    """Save all of a user's notification settings in one query, rather
    than five separate ones — they're always changed together from the
    same Settings form."""
    conn = get_db()
    conn.execute(
        """UPDATE users SET notify_comments = ?, notify_replies = ?, notify_starred_posts = ?,
               silent_start = ?, silent_end = ? WHERE id = ?""",
        (int(notify_comments), int(notify_replies), int(notify_starred_posts), silent_start, silent_end, user_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Shoes (scoped to a user)
# ---------------------------------------------------------------------------

def list_shoes(user_id, active_only=False):
    """All of one user's shoes, newest-added first. Pass
    active_only=True to skip retired ones (used for the "log a run"
    dropdown, which shouldn't offer shoes you're not using anymore)."""
    conn = get_db()
    q = "SELECT * FROM shoes WHERE user_id = ?"
    params = [user_id]
    if active_only:
        q += " AND active = 1"
    q += " ORDER BY date_added DESC, id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def add_shoe(user_id, brand, model, nickname, price, date_added):
    """New shoes always start Active (see the `1` at the end of the
    VALUES list) — toggling that comes later, via set_shoe_active."""
    conn = get_db()
    conn.execute(
        """INSERT INTO shoes (user_id, brand, model, nickname, price, date_added, active)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (user_id, brand, model, nickname, price, date_added),
    )
    conn.commit()
    conn.close()


def set_shoe_active(shoe_id, active):
    conn = get_db()
    conn.execute("UPDATE shoes SET active = ? WHERE id = ?", (1 if active else 0, shoe_id))
    conn.commit()
    conn.close()


def get_shoe(shoe_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM shoes WHERE id = ?", (shoe_id,)).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Runs
# -----------------------------------------------------------------------------
# RUN_SELECT is shared by every "get some runs" function below, so they
# all return the same shape of row (including a couple of extra columns
# pulled in from the shoes and users tables via JOIN — see next comment).
# ---------------------------------------------------------------------------

# A JOIN combines rows from multiple tables that are related by a
# matching column, so we get shoe/owner details alongside each run in
# ONE query instead of a separate lookup per run. LEFT JOIN (for shoes)
# still includes the run even if it has no shoe picked (shoe_id is
# NULL); a plain JOIN (for users) is fine here since every run always
# has an owner.
RUN_SELECT = """
    SELECT runs.*, shoes.brand AS shoe_brand, shoes.model AS shoe_model,
           shoes.nickname AS shoe_nickname, users.username AS owner_username
    FROM runs
    LEFT JOIN shoes ON shoes.id = runs.shoe_id
    JOIN users ON users.id = runs.user_id
"""


def list_runs_for_user(user_id):
    """Every run belonging to one user, oldest-uploaded first (app.py's
    build_weekly_log then re-sorts these into the order the page
    actually displays them in)."""
    conn = get_db()
    rows = conn.execute(RUN_SELECT + " WHERE runs.user_id = ? ORDER BY runs.created_at ASC", (user_id,)).fetchall()
    conn.close()
    return rows


def list_runs_in_range(start_date, end_date):
    """All runs (all users) with date in [start_date, end_date], inclusive. Dates are ISO strings."""
    conn = get_db()
    rows = conn.execute(
        RUN_SELECT + " WHERE runs.date >= ? AND runs.date <= ? ORDER BY runs.created_at ASC",
        (start_date, end_date),
    ).fetchall()
    conn.close()
    return rows


def list_runs_on_date(date_str):
    """Every run (from every user) logged on one specific date — what
    the day-view table is built from."""
    conn = get_db()
    rows = conn.execute(
        RUN_SELECT + " WHERE runs.date = ? ORDER BY runs.created_at ASC", (date_str,)
    ).fetchall()
    conn.close()
    return rows


def get_run(run_id):
    conn = get_db()
    row = conn.execute(RUN_SELECT + " WHERE runs.id = ?", (run_id,)).fetchone()
    conn.close()
    return row


def add_run(data):
    """Insert a new run. `data` is a dict (built by parse_run_form in
    app.py) with a key for every column — using :name-style
    placeholders here (instead of ? placeholders) lets us pass that
    whole dict straight in, matched up by key name, rather than having
    to pull each value out in a specific order ourselves."""
    conn = get_db()
    conn.execute(
        """INSERT INTO runs
           (user_id, date, am_pm, activity_type, shoe_id, distance, time_seconds,
            sleep_hours, resting_hr, description, quality,
            image_url_1, image_url_2, image_url_3, menstruating, created_at)
           VALUES (:user_id, :date, :am_pm, :activity_type, :shoe_id, :distance, :time_seconds,
                   :sleep_hours, :resting_hr, :description, :quality,
                   :image_url_1, :image_url_2, :image_url_3, :menstruating, :created_at)""",
        data,
    )
    conn.commit()
    conn.close()


def update_run(run_id, data):
    """Save changes to an existing run. dict(data, id=run_id) makes a
    COPY of data with an extra "id" key added — so the :id placeholder
    below has something to match, without modifying the caller's
    original dict."""
    data = dict(data, id=run_id)
    conn = get_db()
    conn.execute(
        """UPDATE runs SET
               date = :date, am_pm = :am_pm, activity_type = :activity_type,
               shoe_id = :shoe_id, distance = :distance, time_seconds = :time_seconds,
               sleep_hours = :sleep_hours, resting_hr = :resting_hr,
               description = :description, quality = :quality,
               image_url_1 = :image_url_1, image_url_2 = :image_url_2, image_url_3 = :image_url_3,
               menstruating = :menstruating
           WHERE id = :id""",
        data,
    )
    conn.commit()
    conn.close()


def delete_run(run_id):
    """Delete a run — and, thanks to ON DELETE CASCADE in the schema,
    SQLite automatically deletes its comments too, so we never have to
    remember to clean those up ourselves here."""
    conn = get_db()
    conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def add_comment(run_id, user_id, body, created_at, parent_id=None):
    """Add a comment — or a reply, if parent_id is given (see the
    comments table's schema comment above for what that means)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO comments (run_id, user_id, parent_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, user_id, parent_id, body, created_at),
    )
    conn.commit()
    conn.close()


def list_comments_for_runs(run_ids):
    """Comments for a batch of run ids, joined with commenter username.
    Returns a dict of run_id -> list of comment rows, ordered oldest
    first. Fetching comments for MANY runs in one query (rather than
    one query per run in a loop) is why the home page stays fast even
    with 10 weeks of runs on it.
    The f-string with a variable number of `?` placeholders (one per
    run id) is safe here specifically because only the PLACEHOLDERS
    themselves go into the SQL text — the actual id values are still
    passed separately as parameters below, never glued into the string."""
    if not run_ids:
        return {}
    conn = get_db()
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"""SELECT comments.*, users.username AS author_username
            FROM comments JOIN users ON users.id = comments.user_id
            WHERE comments.run_id IN ({placeholders})
            ORDER BY comments.created_at ASC""",
        list(run_ids),
    ).fetchall()
    conn.close()
    # setdefault(key, []) means "if this key doesn't exist yet, start it
    # as an empty list" — a tidy way to build a dict-of-lists without
    # checking "have I seen this run_id before?" explicitly.
    out = {}
    for r in rows:
        out.setdefault(r["run_id"], []).append(r)
    return out


def last_comment_at(user_id):
    """When did this user last post a comment (on any run)? Used to
    enforce the 10-second-between-comments rate limit in app.py."""
    conn = get_db()
    row = conn.execute(
        "SELECT created_at FROM comments WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["created_at"] if row else None


def get_comment(comment_id):
    """Look up one comment, along with the id of whoever OWNS the run it
    was posted on (run_owner_id) — that's what lets app.py check "is
    this person allowed to delete this comment?" without a second query."""
    conn = get_db()
    row = conn.execute(
        """SELECT comments.*, runs.user_id AS run_owner_id
           FROM comments JOIN runs ON runs.id = comments.run_id
           WHERE comments.id = ?""",
        (comment_id,),
    ).fetchone()
    conn.close()
    return row


def delete_comment(comment_id):
    conn = get_db()
    conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Stars ("My Team" favorites)
# ---------------------------------------------------------------------------

MAX_STARS = 20


def get_starred_ids(user_id):
    """The set of user ids this person has starred. Returned as a Python
    set (rather than a list) because app.py only ever needs to ask "is
    this particular id in there?", which a set does faster than a list."""
    conn = get_db()
    rows = conn.execute("SELECT starred_user_id FROM stars WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return {r["starred_user_id"] for r in rows}


def count_stars(user_id):
    """How many people has this user starred? Used to enforce the
    MAX_STARS limit."""
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM stars WHERE user_id = ?", (user_id,)).fetchone()["n"]
    conn.close()
    return n


def star_user(user_id, starred_user_id, created_at):
    """INSERT OR IGNORE means "add this row, but if it would violate a
    UNIQUE constraint (i.e. this exact star already exists), just do
    nothing instead of raising an error" — handy here since re-starring
    someone you've already starred shouldn't be treated as a problem."""
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO stars (user_id, starred_user_id, created_at) VALUES (?, ?, ?)",
        (user_id, starred_user_id, created_at),
    )
    conn.commit()
    conn.close()


def unstar_user(user_id, starred_user_id):
    conn = get_db()
    conn.execute("DELETE FROM stars WHERE user_id = ? AND starred_user_id = ?", (user_id, starred_user_id))
    conn.commit()
    conn.close()


def list_star_followers(starred_user_id):
    """Everyone who has starred this user — used to fan out 'starred user
    posted' notifications."""
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM stars WHERE starred_user_id = ?", (starred_user_id,)).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


# ---------------------------------------------------------------------------
# Push subscriptions
# ---------------------------------------------------------------------------

def add_push_subscription(user_id, endpoint, p256dh, auth, created_at):
    """Save a device's push subscription. The ON CONFLICT(endpoint) DO
    UPDATE clause means: if this exact endpoint is already saved
    (e.g. the same device subscribing again), update it in place
    instead of failing on the UNIQUE constraint or creating a
    duplicate row."""
    conn = get_db()
    conn.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh,
               auth = excluded.auth""",
        (user_id, endpoint, p256dh, auth, created_at),
    )
    conn.commit()
    conn.close()


def remove_push_subscription(endpoint):
    """Forget a subscription — used both when a user actively turns
    notifications off, and when a push service tells us a subscription
    has gone stale (see send_push_to_user in app.py)."""
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
    conn.close()


def list_push_subscriptions(user_id):
    """Every device this user has enabled notifications on — someone
    might have more than one (phone AND laptop, say)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return rows


def has_push_subscription(user_id):
    """A quick yes/no version of list_push_subscriptions, for the
    Settings page to decide which button (Enable/Turn off) to show."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM push_subscriptions WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Notification log
# ---------------------------------------------------------------------------

def log_notification(user_id, kind, message, url, delivered, created_at):
    """Record that a notification was raised for someone, whether or not
    it actually reached a device (see `delivered`)."""
    conn = get_db()
    conn.execute(
        """INSERT INTO notification_log (user_id, kind, message, url, delivered, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, kind, message, url, int(delivered), created_at),
    )
    conn.commit()
    conn.close()


def list_notifications(user_id, limit=30):
    """A user's most recent notifications, newest first. (Not currently
    shown anywhere in the UI, but handy for debugging what would have
    been sent.)"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM notification_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows
