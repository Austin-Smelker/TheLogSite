"""
db.py — SQLite data layer for the running log app.

Schema covers: users (accounts), shoes and runs (now per-user), and
comments (attached to a run, postable by any logged-in user).
"""

import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "running_log.db"

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
    premium               INTEGER NOT NULL DEFAULT 0
);

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
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stars (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    starred_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    UNIQUE(user_id, starred_user_id)
);

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

CREATE INDEX IF NOT EXISTS idx_runs_user_date ON runs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_comments_run ON comments(run_id);
CREATE INDEX IF NOT EXISTS idx_comments_user_created ON comments(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_stars_user ON stars(user_id);
CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subscriptions(user_id);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_taken(email):
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()
    conn.close()
    return row is not None


def username_taken(username):
    conn = get_db()
    row = conn.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    conn.close()
    return row is not None


def create_user(email, username, password_hash, created_at):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (email, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (email, username, password_hash, created_at),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    conn.close()
    return row


def list_users():
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
# ---------------------------------------------------------------------------

RUN_SELECT = """
    SELECT runs.*, shoes.brand AS shoe_brand, shoes.model AS shoe_model,
           shoes.nickname AS shoe_nickname, users.username AS owner_username
    FROM runs
    LEFT JOIN shoes ON shoes.id = runs.shoe_id
    JOIN users ON users.id = runs.user_id
"""


def list_runs_for_user(user_id):
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
    conn = get_db()
    conn.execute(
        """INSERT INTO runs
           (user_id, date, am_pm, activity_type, shoe_id, distance, time_seconds,
            sleep_hours, resting_hr, description, quality,
            image_url_1, image_url_2, image_url_3, created_at)
           VALUES (:user_id, :date, :am_pm, :activity_type, :shoe_id, :distance, :time_seconds,
                   :sleep_hours, :resting_hr, :description, :quality,
                   :image_url_1, :image_url_2, :image_url_3, :created_at)""",
        data,
    )
    conn.commit()
    conn.close()


def update_run(run_id, data):
    data = dict(data, id=run_id)
    conn = get_db()
    conn.execute(
        """UPDATE runs SET
               date = :date, am_pm = :am_pm, activity_type = :activity_type,
               shoe_id = :shoe_id, distance = :distance, time_seconds = :time_seconds,
               sleep_hours = :sleep_hours, resting_hr = :resting_hr,
               description = :description, quality = :quality,
               image_url_1 = :image_url_1, image_url_2 = :image_url_2, image_url_3 = :image_url_3
           WHERE id = :id""",
        data,
    )
    conn.commit()
    conn.close()


def delete_run(run_id):
    conn = get_db()
    conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def add_comment(run_id, user_id, body, created_at, parent_id=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO comments (run_id, user_id, parent_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, user_id, parent_id, body, created_at),
    )
    conn.commit()
    conn.close()


def list_comments_for_runs(run_ids):
    """Comments for a batch of run ids, joined with commenter username. Returns
    a dict of run_id -> list of comment rows, ordered oldest first."""
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
    out = {}
    for r in rows:
        out.setdefault(r["run_id"], []).append(r)
    return out


def last_comment_at(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT created_at FROM comments WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["created_at"] if row else None


def get_comment(comment_id):
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
    conn = get_db()
    rows = conn.execute("SELECT starred_user_id FROM stars WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return {r["starred_user_id"] for r in rows}


def count_stars(user_id):
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS n FROM stars WHERE user_id = ?", (user_id,)).fetchone()["n"]
    conn.close()
    return n


def star_user(user_id, starred_user_id, created_at):
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
    conn = get_db()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()
    conn.close()


def list_push_subscriptions(user_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return rows


def has_push_subscription(user_id):
    conn = get_db()
    row = conn.execute("SELECT 1 FROM push_subscriptions WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
    conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Notification log
# ---------------------------------------------------------------------------

def log_notification(user_id, kind, message, url, delivered, created_at):
    conn = get_db()
    conn.execute(
        """INSERT INTO notification_log (user_id, kind, message, url, delivered, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, kind, message, url, int(delivered), created_at),
    )
    conn.commit()
    conn.close()


def list_notifications(user_id, limit=30):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM notification_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows
