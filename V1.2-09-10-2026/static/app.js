// =========================================================================
// app.js — all the small bits of browser-side interactivity that make
// the page feel responsive without a full reload: opening/closing
// pop-ups, filling in the edit form, and (further down) subscribing to
// push notifications.
//
// NEW TO JAVASCRIPT? A QUICK ORIENTATION:
// This file runs INSIDE THE BROWSER (not on the server, unlike
// app.py/db.py) — it's loaded by every page via the <script> tag near
// the bottom of templates/base.html, and it runs after the page's HTML
// has loaded.
//   - document.getElementById("x") finds the one HTML element with
//     id="x" on the page, so we can read or change it.
//   - element.addEventListener("click", someFunction) says "run
//     someFunction whenever this element is clicked" (or "change" for
//     an <input>, "submit" for a <form>, etc.)
//   - element.classList.add("y") / .remove("y") turns a CSS class on
//     or off — that's how showing/hiding the modal pop-ups below works:
//     the CSS (static/style.css) says a ".modal-overlay" is hidden
//     UNLESS it also has the "is-open" class, so adding/removing just
//     that one class shows or hides the whole thing.
//   - (a, b) => { ... } is an "arrow function" — a compact way to write
//     a small function, equivalent to writing
//     function(a, b) { ... }
// =========================================================================

// ---------------------------------------------------------------------
// Generic modal open/close helper
// -------------------------------------------------------------------------
// A "modal" here just means a pop-up box that darkens/covers the rest
// of the page — used for the add/edit-run form, the add-shoe form, and
// the My Team day-cell pop-up. This one function handles opening and
// closing ANY of them, since they all behave the same way: show/hide on
// a button click, also close on pressing Escape or clicking the dark
// overlay outside the box.
// ---------------------------------------------------------------------
function wireModal(overlayId, openBtnId, closeIds) {
  const overlay = document.getElementById(overlayId);
  if (!overlay) return; // this page doesn't have this particular modal — nothing to do

  const open = () => overlay.classList.add("is-open");
  const close = () => overlay.classList.remove("is-open");

  const openBtn = document.getElementById(openBtnId);
  if (openBtn) openBtn.addEventListener("click", open);

  // closeIds is a list because some modals have more than one way to
  // close them (an X button AND a Cancel button, for instance).
  closeIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", close);
  });

  // Clicking the dark background OUTSIDE the modal box should also
  // close it — but only if the click was really on the overlay itself,
  // not on something inside the box that happens to be on top of it
  // (e.target is exactly what element was clicked).
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });

  // Hand back the open/close functions so other code (like the run-form
  // logic below) can trigger them directly, e.g. to reset the form and
  // THEN open it.
  return { open, close };
}

// ---------------------------------------------------------------------
// Run modal (home / profile page): add + edit share one form
// -------------------------------------------------------------------------
// This whole block is wrapped in (function setupRunModal() { ... })() —
// an "immediately-invoked function expression" (IIFE). That's just a
// function that's defined and called in one go, right here — its only
// purpose is to keep all the `const`/`let` variables inside it (like
// `fields` below) from leaking out and clashing with variables in the
// OTHER sections of this file, since they're never used outside this
// block anyway.
// ---------------------------------------------------------------------
(function setupRunModal() {
  const overlay = document.getElementById("runModalOverlay");
  if (!overlay) return; // no run modal on this page (e.g. someone else's read-only log) — nothing to wire up

  const modal = wireModal("runModalOverlay", "openAddRun", ["closeAddRun", "cancelAddRun"]);
  const title = document.getElementById("runModalTitle");
  const today = document.getElementById("field-date").value;

  // One place listing every field in the form, so the rest of this
  // function can say fields.distance instead of repeating
  // document.getElementById("field-distance") everywhere. Some of
  // these (image1/2/3, file1/2/3) may be `null` — they only exist in
  // the page's HTML at all for Premium users, see log.html — so code
  // below that touches them checks `if (fields.imageX)` first.
  const fields = {
    runId: document.getElementById("field-run-id"),
    date: document.getElementById("field-date"),
    amPm: document.getElementById("field-am-pm"),
    activityType: document.getElementById("field-activity-type"),
    shoeId: document.getElementById("field-shoe-id"),
    distance: document.getElementById("field-distance"),
    time: document.getElementById("field-time"),
    sleepHours: document.getElementById("field-sleep-hours"),
    restingHr: document.getElementById("field-resting-hr"),
    description: document.getElementById("field-description"),
    quality: document.getElementById("field-quality"),
    image1: document.getElementById("field-image-url-1"),
    image2: document.getElementById("field-image-url-2"),
    image3: document.getElementById("field-image-url-3"),
    file1: document.getElementById("field-image-file-1"),
    file2: document.getElementById("field-image-file-2"),
    file3: document.getElementById("field-image-file-3"),
  };

  function resetImageInputs() {
    [fields.image1, fields.image2, fields.image3].forEach((el) => {
      if (el) el.value = "";
    });
    [fields.file1, fields.file2, fields.file3].forEach((el) => {
      if (el) el.value = "";
    });
  }

  // AM/PM segmented control — two buttons that act like a single choice.
  // Clicking one sets the REAL hidden form field (fields.amPm) and
  // toggles which button LOOKS selected via the "is-selected" CSS class.
  const segBtns = document.querySelectorAll("#ampmSegmented .seg-btn");
  function setAmPm(value) {
    fields.amPm.value = value;
    segBtns.forEach((b) => b.classList.toggle("is-selected", b.dataset.value === value));
  }
  segBtns.forEach((b) => b.addEventListener("click", () => setAmPm(b.dataset.value)));

  // Off-day runs don't need shoes/distance/time — grey them out.
  const conditionalIds = ["field-shoe-id", "field-distance", "field-time"];
  function applyActivityState() {
    const isOffDay = fields.activityType.value === "off day";
    conditionalIds.forEach((id) => {
      const input = document.getElementById(id);
      input.disabled = isOffDay;
      input.closest(".field").classList.toggle("is-disabled", isOffDay);
    });
  }
  fields.activityType.addEventListener("change", applyActivityState);

  // Clear the form back to blank defaults — used when opening it fresh
  // to log a NEW run (as opposed to editing an existing one, below).
  function resetForEdit() {
    fields.runId.value = "";
    title.textContent = "Log a run";
    fields.date.value = today;
    setAmPm("AM");
    fields.activityType.selectedIndex = 0;
    fields.shoeId.value = "";
    fields.distance.value = "";
    fields.time.value = "";
    fields.sleepHours.value = "";
    fields.restingHr.value = "";
    fields.description.value = "";
    fields.quality.value = "";
    resetImageInputs();
    applyActivityState();
  }

  document.getElementById("openAddRun").addEventListener("click", resetForEdit);

  // Every row's "Edit" button opens the SAME modal/form as above, but
  // pre-filled with that row's existing data instead of blank. The data
  // itself comes from the data-* attributes on the <tr> (see
  // templates/_run_row.html) — `row.dataset` is how JavaScript reads
  // those: a data-run-id="5" attribute shows up as row.dataset.runId.
  document.querySelectorAll(".edit-run").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr"); // walk up to the row this button lives in
      const d = row.dataset;

      fields.runId.value = d.runId;
      title.textContent = "Edit run";
      fields.date.value = d.date;
      setAmPm(d.amPm);
      fields.activityType.value = d.activityType;
      fields.shoeId.value = d.shoeId || "";
      fields.distance.value = d.distance || "";
      fields.time.value = d.time || "";
      fields.sleepHours.value = d.sleepHours || "";
      fields.restingHr.value = d.restingHr || "";
      fields.description.value = d.description || "";
      fields.quality.value = d.quality || "";
      resetImageInputs();
      // File inputs can never be pre-filled by JavaScript for security
      // reasons (a website could otherwise silently "pick" a file from
      // your computer to upload) — only the URL text fields can be.
      if (fields.image1) fields.image1.value = d.imageUrl1 || "";
      if (fields.image2) fields.image2.value = d.imageUrl2 || "";
      if (fields.image3) fields.image3.value = d.imageUrl3 || "";
      applyActivityState();

      modal.open();
    });
  });
})();

// ---------------------------------------------------------------------
// Shoe modal (shoes page)
// ---------------------------------------------------------------------
wireModal("shoeModalOverlay", "openAddShoe", ["closeAddShoe", "cancelAddShoe"]);

// ---------------------------------------------------------------------
// Per-run "Details" toggle (photos + comments), used on the log table
// and the day-view table
// -------------------------------------------------------------------------
// The photos/comments for each run are already sitting in the page's
// HTML (rendered by the server, hidden via the `hidden` attribute) —
// clicking "Details" just reveals them, no extra network request
// needed. `.hidden = !target.hidden` is a compact way of saying "flip
// it": show it if it was hidden, hide it if it was showing.
// ---------------------------------------------------------------------
document.querySelectorAll(".details-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = document.getElementById(btn.dataset.target);
    if (!target) return;
    target.hidden = !target.hidden;
  });
});

// ---------------------------------------------------------------------
// My Team grid: click a day cell to see that runner's runs that day.
// -------------------------------------------------------------------------
// A SECURITY NOTE worth understanding: this pop-up's content comes from
// data other users typed (activity type, description, etc.), embedded
// on the page as JSON in a <script> tag (see the {{ popup_data | tojson
// }} line in templates/team.html). We build the pop-up's HTML using
// document.createElement(...) + node.textContent = someText (see the
// el() helper below) rather than something like
// element.innerHTML = "<div>" + someText + "</div>". That distinction
// matters a lot: textContent always inserts text as PLAIN TEXT, even if
// it happens to contain something that looks like an HTML tag, so
// there's no way for another user's run description to ever be treated
// as actual markup/code in your browser. innerHTML, by contrast, WOULD
// interpret that text as HTML — which is exactly the kind of hole a
// malicious "<script>...</script>" description could exploit if we'd
// used it here. (The one place below that still uses innerHTML just
// clears the box back to empty first, which is safe since there's no
// user data involved in that specific line.)
// ---------------------------------------------------------------------
(function setupTeamPopup() {
  if (typeof window.TEAM_POPUP_DATA === "undefined") return; // not on the My Team page

  const modal = wireModal("teamPopupOverlay", null, ["closeTeamPopup"]);
  const body = document.getElementById("teamPopupBody");
  const titleEl = document.getElementById("teamPopupTitle");

  // A tiny helper: make one HTML element, optionally with a CSS class
  // and some (always safely-inserted, see note above) text inside it.
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showPopup(key, runnerName, dateStr) {
    const runs = window.TEAM_POPUP_DATA[key] || [];
    titleEl.textContent = runnerName + " — " + dateStr;
    body.innerHTML = ""; // clear out whatever was shown last time (safe: no user data here, just emptying the box)

    if (runs.length === 0) {
      body.appendChild(el("p", "comment-empty", "No runs logged this day."));
    }

    runs.forEach((r) => {
      const card = el("div", "popup-run");
      const head = el("div", "popup-run-head");
      head.appendChild(el("span", "popup-run-activity", r.am_pm + " · " + r.activity_type));
      if (r.quality !== null && r.quality !== undefined) {
        head.appendChild(el("span", "popup-run-quality", "Quality " + r.quality));
      }
      card.appendChild(head);

      const stats = [];
      if (r.distance !== null && r.distance !== undefined) stats.push(r.distance.toFixed(2) + " mi");
      if (r.time_display) stats.push(r.time_display);
      if (r.shoe) stats.push(r.shoe);
      if (stats.length) card.appendChild(el("div", "popup-run-stats", stats.join(" · ")));

      if (r.description) card.appendChild(el("div", "popup-run-desc", r.description));

      body.appendChild(card);
    });

    modal.open();
  }

  document.querySelectorAll(".team-cell.has-data").forEach((cell) => {
    const activate = () => {
      const key = cell.dataset.cellKey;
      const runnerName = cell.closest("tr").querySelector(".runner-link").textContent;
      const dateStr = key.split("_").slice(1).join("_");
      showPopup(key, runnerName, dateStr);
    };
    cell.addEventListener("click", activate);
    // Cells are clickable, but should also work for someone navigating
    // by keyboard (tabbing to a cell and pressing Enter or Space) —
    // that's what this second listener is for, matching the
    // accessibility attributes (role="button" tabindex="0") the
    // template gives these cells.
    cell.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activate();
      }
    });
  });
})();


// ---------------------------------------------------------------------
// Push notifications (Settings page): request permission, subscribe via
// the browser's Push API, hand the subscription to the server.
// -------------------------------------------------------------------------
// This uses `async function` / `await` — a way of writing code that
// waits for something slow (like asking the user for permission, or a
// network request) without freezing the rest of the page. Reading it
// top to bottom like a normal step-by-step recipe is basically correct:
// each `await` just means "pause here until this finishes, then
// continue with the result".
// ---------------------------------------------------------------------
(function setupPush() {
  const enableBtn = document.getElementById("pushEnableBtn");
  const disableBtn = document.getElementById("pushDisableBtn");
  const statusEl = document.getElementById("pushStatus");
  if (!enableBtn) return; // not on the Settings page

  // Reuse whichever comment/edit form's hidden csrf_token field happens
  // to be on the page — they're all the same value (see get_csrf_token
  // in app.py), so it doesn't matter which one we grab.
  const csrfInput = document.querySelector('input[name="csrf_token"]');
  const csrfToken = csrfInput ? csrfInput.value : "";

  // The browser's Push API needs our VAPID public key as raw bytes, but
  // the server hands it to us as a base64url TEXT string (see the meta
  // tag in base.html) — this function does that text-to-bytes
  // conversion. You don't need to understand the byte-level details,
  // just that it's a standard, required conversion step for Web Push.
  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64); // decode base64 to a raw binary string
    const bytes = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i++) bytes[i] = rawData.charCodeAt(i);
    return bytes;
  }

  function setUI(subscribed) {
    enableBtn.hidden = subscribed;
    disableBtn.hidden = !subscribed;
  }

  // Not every browser supports push notifications (or the person might
  // have disabled the feature) — check before doing anything else.
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    statusEl.textContent = "This browser doesn't support push notifications.";
    enableBtn.disabled = true;
    return;
  }

  setUI(!!window.HAS_PUSH_SUBSCRIPTION); // !! turns any value into a plain true/false

  enableBtn.addEventListener("click", async () => {
    try {
      // Step 1: ask the browser to show the "Allow notifications?"
      // permission prompt. If they say no, there's nothing more we can do.
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        statusEl.textContent = "Notification permission wasn't granted.";
        return;
      }
      // Step 2: register our service worker (static/sw.js) — the
      // background script that will actually display notifications
      // later, even if this page/tab isn't open.
      await navigator.serviceWorker.register("/sw.js");
      const readyReg = await navigator.serviceWorker.ready;
      // Step 3: ask the browser to create a push subscription — this is
      // what actually talks to Apple/Google/Mozilla's push service and
      // gets us a unique "address" for this device.
      const vapidKey = document.querySelector('meta[name="vapid-public-key"]').content;
      const subscription = await readyReg.pushManager.subscribe({
        userVisibleOnly: true, // required by the spec: every push must result in a visible notification
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });
      // Step 4: send that subscription to OUR server to save (see
      // push_subscribe() in app.py) — fetch() is JavaScript's built-in
      // way of making an HTTP request without navigating to a new page.
      const resp = await fetch("/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify(subscription.toJSON()),
      });
      if (resp.ok) {
        setUI(true);
        statusEl.textContent = "Notifications enabled on this device.";
      } else {
        statusEl.textContent = "Couldn't save that subscription — try again.";
      }
    } catch (err) {
      statusEl.textContent = "Couldn't enable notifications: " + err.message;
    }
  });

  disableBtn.addEventListener("click", async () => {
    try {
      const reg = await navigator.serviceWorker.getRegistration("/sw.js");
      if (reg) {
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await fetch("/push/unsubscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
          await sub.unsubscribe();
        }
      }
      setUI(false);
      statusEl.textContent = "Notifications turned off on this device.";
    } catch (err) {
      statusEl.textContent = "Couldn't turn off notifications: " + err.message;
    }
  });
})();

// ---------------------------------------------------------------------
// Reply-to-comment: toggle a small inline form under a top-level comment
// ---------------------------------------------------------------------
document.querySelectorAll(".reply-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = document.getElementById(btn.dataset.target);
    if (!target) return;
    target.hidden = !target.hidden;
    if (!target.hidden) {
      const textarea = target.querySelector("textarea");
      if (textarea) textarea.focus();
    }
  });
});
