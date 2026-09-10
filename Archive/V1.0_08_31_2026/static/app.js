// ---------------------------------------------------------------------
// Generic modal open/close helper
// ---------------------------------------------------------------------
function wireModal(overlayId, openBtnId, closeIds) {
  const overlay = document.getElementById(overlayId);
  if (!overlay) return;

  const open = () => overlay.classList.add("is-open");
  const close = () => overlay.classList.remove("is-open");

  const openBtn = document.getElementById(openBtnId);
  if (openBtn) openBtn.addEventListener("click", open);

  closeIds.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", close);
  });

  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });

  return { open, close };
}

// ---------------------------------------------------------------------
// Run modal (home / profile page): add + edit share one form
// ---------------------------------------------------------------------
(function setupRunModal() {
  const overlay = document.getElementById("runModalOverlay");
  if (!overlay) return;

  const modal = wireModal("runModalOverlay", "openAddRun", ["closeAddRun", "cancelAddRun"]);
  const title = document.getElementById("runModalTitle");
  const today = document.getElementById("field-date").value;

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

  // AM/PM segmented control
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

  document.querySelectorAll(".edit-run").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
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
// Content is built with textContent (never innerHTML) so nothing a user
// pasted into a run — description, activity type, etc. — can ever be
// interpreted as markup.
// ---------------------------------------------------------------------
(function setupTeamPopup() {
  if (typeof window.TEAM_POPUP_DATA === "undefined") return;

  const modal = wireModal("teamPopupOverlay", null, ["closeTeamPopup"]);
  const body = document.getElementById("teamPopupBody");
  const titleEl = document.getElementById("teamPopupTitle");

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showPopup(key, runnerName, dateStr) {
    const runs = window.TEAM_POPUP_DATA[key] || [];
    titleEl.textContent = runnerName + " — " + dateStr;
    body.innerHTML = "";

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
// ---------------------------------------------------------------------
(function setupPush() {
  const enableBtn = document.getElementById("pushEnableBtn");
  const disableBtn = document.getElementById("pushDisableBtn");
  const statusEl = document.getElementById("pushStatus");
  if (!enableBtn) return; // not on the Settings page

  const csrfInput = document.querySelector('input[name="csrf_token"]');
  const csrfToken = csrfInput ? csrfInput.value : "";

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    const bytes = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i++) bytes[i] = rawData.charCodeAt(i);
    return bytes;
  }

  function setUI(subscribed) {
    enableBtn.hidden = subscribed;
    disableBtn.hidden = !subscribed;
  }

  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    statusEl.textContent = "This browser doesn't support push notifications.";
    enableBtn.disabled = true;
    return;
  }

  setUI(!!window.HAS_PUSH_SUBSCRIPTION);

  enableBtn.addEventListener("click", async () => {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        statusEl.textContent = "Notification permission wasn't granted.";
        return;
      }
      await navigator.serviceWorker.register("/sw.js");
      const readyReg = await navigator.serviceWorker.ready;
      const vapidKey = document.querySelector('meta[name="vapid-public-key"]').content;
      const subscription = await readyReg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });
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
