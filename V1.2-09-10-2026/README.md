# TheLogSite.com — a running log

A Flask web app for logging runs, tracking shoe mileage, and following a
team's training.

## What's here

- **Accounts** — sign up with an email, username, password, and gender
  (Male/Female/Other — asked once at signup). One account per email.
  Usernames can include spaces, and everything about your account —
  username, theme, notifications — is changed from **Settings** in the
  top bar.
- **Home** — your own run log, grouped into Monday–Sunday weeks (most
  recent 10), with a divider between weeks showing that week's total
  mileage and time, plus weekly mileage/time charts broken down by
  activity type (server-rendered SVG — no JS charting library/CDN).
- **Shoes** — add shoes, see them listed by date added, mark them
  Active or Retired.
- **My Team** — a grid of every runner (alphabetical, starred runners
  pinned to the top) by day of the current week, shaded red-to-green by
  that day's average run quality. Click a cell for that day's runs in a
  pop-up; click a runner's name for their full log; click a day heading
  for a day view of everyone's entries on that date. Star up to 20
  runners (the leftmost column) to pin them to the top and get notified
  when they post.
- **Comments** — comment on any run, including your own, and reply to
  any comment (one level deep — replying to a reply attaches to the
  original top-level comment). One comment per person every 10 seconds.
  Only the log's owner can delete a comment on it.
- **Photos** — up to 3 per run, either pasted as a link or uploaded as
  a file (JPEG/PNG/GIF/WEBP, 6MB each), capped at 5in × 5in on display.
  **Premium only** to add — see below. Anyone can view photos that are
  already on a run regardless of their own premium status.
- **Notifications** — **premium only**. See below.
- **Dark mode** — toggle in Settings.
- Every table has light grey cell borders.

All data lives in a local SQLite file (`running_log.db`), created
automatically on first run.

## Premium

Settings → Premium is a plain on/off toggle — there's no payment flow
here, it's a placeholder switch anyone can flip on themselves. It gates
two things:
- **Notifications** (below) — the toggle, the checkboxes, and enabling
  push all require premium; turning premium off doesn't clear saved
  notification preferences, it just stops them from firing until
  premium is back on.
- **Adding photos to a run** — the photo fields only show up in the
  "Log a run" form when premium is on. Turning premium off never
  removes photos already on a run, and everyone (premium or not) can
  still see them — only *adding new ones* requires premium. Editing a
  run that already has photos while non-premium leaves those photos
  untouched.

Every one of these checks is enforced server-side, not just hidden in
the UI — so it holds even against a hand-crafted request, not only
someone using the page normally.

## Notifications

Requires Premium (see above). Settings → "Notifications on this
device" turns on real browser push notifications — the kind that show
up even when the site isn't open — for whichever of these you pick:
- Comments on your log
- Replies to your comments
- Starred users posting a run

You can also set **silent hours** (e.g. 22:00–06:00) during which
nothing gets sent. These are always in **Eastern Time** (EST or EDT,
whichever currently applies), no matter what timezone the server or
the person actually is in — so it means the same thing to everyone.

### Getting notifications on your phone, step by step

Push notifications only work over a secure (HTTPS) connection — that's
a rule browsers enforce, not something this app can skip. Your phone
also needs to be able to reach the app over the network, so
`127.0.0.1`/`localhost` (which only works on the same machine the
server is running on) won't do here. The easiest way to get both at
once is a free tunnel tool. Here's the full path using
[ngrok](https://ngrok.com), start to finish:

1. **Start the app** on your computer as usual:
   ```bash
   pip install -r requirements.txt
   python app.py
   ```
   Leave this running.
2. **Install ngrok** (one-time): go to
   [ngrok.com/download](https://ngrok.com/download), make a free
   account, and follow their install steps for your operating system.
3. **Start a tunnel** to your app, in a *second* terminal window (leave
   the app running in the first one):
   ```bash
   ngrok http 5000
   ```
4. Ngrok prints a line like `Forwarding  https://abcd-1234.ngrok-free.app -> http://localhost:5000`.
   That `https://...ngrok-free.app` address is a real, secure, public
   URL for your app — that's the one you'll use on your phone.
5. **On your phone**, open that `https://...ngrok-free.app` address in
   your browser (Safari on iPhone, Chrome on Android) and log in.
6. **iPhone only, one extra step:** tap the Share icon, then "Add to
   Home Screen", and open the app from that new home screen icon from
   now on. (Chrome on Android doesn't need this — a regular browser tab
   works.)
7. Go to **Settings** in the app and turn **Premium** on.
8. Still in Settings, tap **"Enable notifications on this device"**
   and allow the permission prompt your phone shows you.
9. Pick which of the three notification types you want under "What to
   be notified about", and optionally set silent hours.

That's it — comments, replies, or starred-user posts (whichever you
picked) will now show up as real notifications on your phone.

**A couple of things worth knowing:**
- The free ngrok URL changes every time you restart ngrok, so anyone
  who'd already enabled notifications will need to redo step 5 onward
  with the new address. [Tailscale](https://tailscale.com) is a good
  alternative if you want a URL that stays the same — it's a little
  more setup up front in exchange for that.
- Two files get created automatically the first time you run the app,
  next to `running_log.db`: `.secret_key` and `.vapid_private_key.pem`
  / `.vapid_public_key.txt`. These are what let the app prove to
  Apple/Google's push services that notifications are really coming
  from your install — don't share the private ones with anyone.
- If `pywebpush` isn't installed (it's in `requirements.txt`, so
  `pip install -r requirements.txt` should cover it), the rest of the
  site still works fine — notification preferences just won't actually
  reach a device.

## The charts

Weekly mileage and weekly time are stacked bars, one segment per
activity type, each with its own fill (solid color or stripe pattern,
not just a hue) so a week's training mix is visible at a glance — with
a legend underneath. Plain server-rendered SVG, no CDN dependency.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**, create an account, and go.

## Upgrading to a newer version without losing your data

Everyone's accounts, runs, shoes, comments — all of it — lives in one
file, `running_log.db`, sitting next to `app.py`. That file is never
part of the downloaded website code, which is what makes upgrading
safe:

1. Download the new version of the site.
2. Unzip it into the **same folder** your current `running_log.db` is
   already in, letting it overwrite `app.py`, `db.py`, `templates/`,
   `static/`, and `requirements.txt`.
3. **Don't delete or replace `running_log.db`, `.secret_key`, or the
   `.vapid_*` files** — those are your actual data and login setup, and
   none of them are in the downloaded zip to begin with, so a normal
   unzip won't touch them anyway.
4. `pip install -r requirements.txt` again (in case the new version
   added a dependency), then `python app.py` as usual.

If the new version added anything to the database (a new field on
accounts or runs, say), the app detects that automatically the moment
it starts, adds exactly what's missing, and leaves every existing row
untouched — nobody's data or login is affected. You'll also see a new
file appear alongside `running_log.db` right when that happens, named
something like `running_log.backup-20260115-093000.db` — a snapshot
taken right before the change, purely as a safety net. These are never
deleted automatically, so it's fine to remove old ones yourself once
you're confident everything looks right.

(If you're maintaining your own fork of this project: adding a new
column to an existing table needs two small, matching edits in `db.py`
— see the comment above `COLUMNS_ADDED_OVER_TIME` for exactly where.
Adding a whole new table needs no extra step at all.)

## Letting other devices reach it

By default the app listens on every network interface, not just
`127.0.0.1` — anyone on the **same Wi-Fi/network** can open it at
`http://<this-machine's-IP>:5000` as soon as it's running.

1. Find your machine's local IP: `ifconfig | grep "inet "` (macOS/Linux)
   or `ipconfig` (Windows, look for "IPv4 Address").
2. Your OS may prompt to allow incoming connections the first time —
   allow it.

For a **different network** (true internet access), your router
doesn't let outside traffic in by default — use a tunnel tool
(ngrok/Tailscale, also solves the HTTPS-for-push requirement above) or
port-forward port 5000, which many home ISPs complicate (CGNAT).

**Two safety notes if you go beyond your own machine:** debug mode is
off by default on purpose (Werkzeug's debugger allows code execution if
left on and reachable by others — only enable with `FLASK_DEBUG=1` for
solo localhost work), and this is still Flask's development server —
fine for a household/small team, but consider `waitress` instead of
`python app.py` for anything long-running at real scale.

## Customizing the site's appearance

Two places, both heavily commented:
- **`app.py`**, near the top — `SITE_NAME` controls the name shown in
  the header and every page title. (Currently "TheLogSite.com".)
- **`static/style.css`**, the block at the very top — every background,
  text, and accent color is a CSS custom property with a comment
  explaining what it controls, including a dedicated header color
  (kept separate from body text color on purpose, so the header can
  look different from the page and stay correct in both light and dark
  mode) and a full second copy of the same tokens for dark mode
  directly below it.

## Security notes

- **Passwords** hashed with Werkzeug's `generate_password_hash`.
- **XSS** — everything user-typed is HTML-escaped everywhere it's
  rendered, including inside the JSON the My Team pop-up reads (which
  is itself inserted into the page via `textContent`, never HTML).
  Image links are restricted to `http://`/`https://`.
- **Uploaded photos** are re-encoded with Pillow, which both confirms a
  file is a genuine, decodable image (not just something with a `.jpg`
  extension) and strips out anything else riding along in the file.
  Saved under a random generated filename, capped at 6MB (enforced both
  per-file and, via Flask's `MAX_CONTENT_LENGTH`, for the whole
  request).
- **SQL injection** — parameterized queries throughout.
- **CSRF** — a per-session token checked on every POST (via a hidden
  form field, or an `X-CSRFToken` header for the two JSON endpoints
  push subscribe/unsubscribe use).
- **Authorization** — editing/deleting a run, toggling a shoe, renaming
  your account, deleting a comment, and starring are all checked
  server-side against who you actually are, not just what buttons the
  UI shows you.

**Not implemented**: any actual payment/billing behind Premium (it's a
self-serve toggle for now, by design — see the Premium section above),
password reset, email verification, comment editing (only delete),
login-attempt rate limiting, and notification delivery retries (a push
that fails once — e.g. because a subscription went stale — isn't
retried, though stale subscriptions do get cleaned up automatically).

## Notes on a few fields

- **Time** — `mm:ss` or `h:mm:ss`.
- **Menstruating** — a checkbox that appears in the log-a-run form only
  for accounts with gender set to Female. It's private: only visible to
  you on your own log, never shown to anyone else viewing your log or
  the day-view table, and never included in the My Team pop-up. Gender
  itself is also never displayed anywhere — it's only ever used to
  decide whether to show this one checkbox.
- Distance, time, sleep, resting HR, shoes, quality, description, and
  photos are all optional; "off day" greys out shoes/distance/time as a
  hint, not a hard requirement.
- **Run quality** (1–10) drives the red-to-green shading on the My Team
  grid; distance and time (not quality) drive the two charts.
- **Silent hours** use the server's local clock — the simplest correct
  behavior for a team sharing one timezone.

## Reading the code

`app.py` and `db.py` are commented all the way through for someone new
to Flask/Python web development — including explanations of things
like decorators, sessions, and SQL along the way, not just what each
specific function does. `static/app.js` has the same treatment for the
JavaScript side (DOM basics, async/await, why user-typed content is
inserted with `textContent` rather than `innerHTML`). Worth starting at
the top of app.py — there's a longer orientation comment there before
the code itself begins.

## Project layout

```
app.py                  Flask routes, auth, CSRF, notifications, charts, VAPID keys
db.py                    SQLite schema and data-access functions
templates/
  base.html               layout, nav, fonts, theme attribute, SVG chart-fill patterns
  login.html / register.html / settings.html
  log.html                 home page AND another user's page (same template)
  _run_row.html             shared macro: a run's row + photos/threaded comments
  _chart.html               shared macro: one stacked SVG bar chart + legend
  shoes.html
  team.html                 My Team weekly grid with starring
  team_day.html              day view across all runners
static/
  style.css                operator-commented color tokens + dark mode + all component styles
  app.js                   modals, edit prefill, details/reply toggles, push subscribe, team pop-up
  sw.js                     service worker (shows push notifications)
  uploads/                  uploaded photo files (created automatically, gitignore-worthy)
requirements.txt          Flask, cryptography (VAPID keys), pywebpush, Pillow
```
