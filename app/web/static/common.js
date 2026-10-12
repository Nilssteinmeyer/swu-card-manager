/* =========================================================================
   SWU Card Manager — Common JS helpers
   ========================================================================= */

const API_BASE = "";

// ---- Fetch wrapper -------------------------------------------------------
async function api(path, options = {}) {
  const url = API_BASE + path;
  const opts = {
    headers: { "Content-Type": "application/json" },
    ...options,
  };
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof Blob)) {
    opts.body = JSON.stringify(opts.body);
  }
  try {
    const resp = await fetch(url, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    return data;
  } catch (err) {
    console.error(`API ${path} failed:`, err);
    throw err;
  }
}

// ---- Toast notifications -------------------------------------------------
function showToast(message, type = "info", duration = 3000) {
  let container = document.querySelector(".toast-container");
  if (!container) {
    container = document.createElement("div");
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const icons = { success: "✓", error: "✗", info: "ℹ" };
  toast.innerHTML = `<span>${icons[type] || ""}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(20px)";
    toast.style.transition = "all 0.3s";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ---- HTML escaping -------------------------------------------------------
function escapeHtml(text) {
  if (text === null || text === undefined) return "";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

// ---- Loading overlay -----------------------------------------------------
function showLoading(message = "Loading...") {
  hideLoading();
  const overlay = document.createElement("div");
  overlay.className = "loading-overlay";
  overlay.id = "loading-overlay";
  overlay.innerHTML = `
    <div class="spinner"></div>
    <div>${escapeHtml(message)}</div>
  `;
  document.body.appendChild(overlay);
}

function hideLoading() {
  const overlay = document.getElementById("loading-overlay");
  if (overlay) overlay.remove();
}

// ---- Active nav ----------------------------------------------------------
function setActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll(".navbar-nav a, .nav-desktop a, .tab-item[data-tab]").forEach((a) => {
    const href = a.getAttribute("href") || a.dataset.tab || "";
    if (href === path || (path === "/" && href === "/") || (href !== "/" && path.startsWith(href))) {
      a.classList.add("active");
    }
  });
}

// ---- Format helpers ------------------------------------------------------
function formatConfidence(c) {
  if (c === null || c === undefined) return "—";
  return `${(c * 100).toFixed(1)}%`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function rarityBadgeClass(rarity) {
  const r = (rarity || "").toLowerCase();
  const map = {
    common: "badge-common",
    uncommon: "badge-uncommon",
    rare: "badge-rare",
    legendary: "badge-legendary",
    special: "badge-special",
  };
  return map[r] || "badge-common";
}

// ---- Card image URL ------------------------------------------------------
function cardImageUrl(cardId) {
  return `${API_BASE}/api/card/image/${encodeURIComponent(cardId)}`;
}

// ---- Admin gate ----------------------------------------------------------
// Hides elements marked with data-admin-only when admin mode is off.
let IS_ADMIN = true;

async function loadAdminState() {
  try {
    const res = await fetch(`${API_BASE}/api/admin/status`);
    if (!res.ok) return;
    const data = await res.json();
    IS_ADMIN = !!data.admin;
    applyAdminVisibility();
  } catch (e) {
    console.warn("admin status unavailable, keeping admin elements visible:", e);
  }
}

function applyAdminVisibility() {
  const adminEls = document.querySelectorAll("[data-admin-only]");
  adminEls.forEach((el) => {
    if (IS_ADMIN) {
      el.classList.remove("admin-hidden");
    } else {
      el.classList.add("admin-hidden");
    }
  });
}

// ---- Account: Desktop-Dropdown + Mobile-Mehr-Sheet (Redesign 2026-10) ------
let ME = null; // aktueller User + Haushalte (fuer Sheet + Dropdown)

async function initAccountMenu() {
  let me;
  try {
    const resp = await fetch("/api/auth/me");
    if (resp.status === 401) return; // login page etc.
    me = await resp.json();
  } catch (e) { return; }
  if (!me.user) return;
  ME = me;

  const roleLabel = { owner: "Owner", editor: "Editor", viewer: "Zuschauer" };
  const household = (me.households || []).find(h => h.household_id === me.active_household_id) || me.households[0];

  // --- Desktop: Dropdown im account-area Container (FIX: nicht mehr geclippt) ---
  const desktopArea = document.getElementById("account-menu");
  if (desktopArea) {
    desktopArea.innerHTML = `
      <button class="account-dd-btn" id="account-dd-btn" type="button">
        <span class="avatar">👤</span>
        <span class="add-name">${escapeHtml(me.user.username)}</span>
        ${household ? `<span class="add-hh">· ${escapeHtml(household.name)}</span>` : ""}
        <span class="caret">▾</span>
      </button>`;
    const dd = document.createElement("div");
    dd.className = "account-dd hidden";
    dd.id = "account-dd";
    dd.innerHTML = _accountDropdownHTML(me, household, roleLabel);
    document.body.appendChild(dd); // fix am Body -> kein Clipping mehr
    const btn = document.getElementById("account-dd-btn");
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const r = btn.getBoundingClientRect();
      dd.style.top = (r.bottom + 6) + "px";
      dd.style.right = (window.innerWidth - r.right) + "px";
      dd.classList.toggle("hidden");
      btn.setAttribute("aria-expanded", !dd.classList.contains("hidden"));
    });
    document.addEventListener("click", () => dd.classList.add("hidden"));
  }

  // --- Mobile: Mehr-Sheet befuellen ---
  const sheetAcc = document.getElementById("sheet-account");
  if (sheetAcc) {
    sheetAcc.innerHTML = `
      <div class="sa-avatar">👤</div>
      <div>
        <div class="sa-name">${escapeHtml(me.user.display_name || me.user.username)}</div>
        <div class="sa-role">${household ? escapeHtml(household.name) + " · " + (roleLabel[household.role] || household.role) : "Keine Sammlung"}</div>
      </div>`;

    const actions = document.getElementById("sheet-actions");
    let html = "";
    (me.households || []).forEach((h) => {
      if (h.household_id !== me.active_household_id) {
        html += `<button class="sheet-action" onclick="switchHousehold(${h.household_id})">
          <span class="sl-icon">🔄</span><span>Zu „${escapeHtml(h.name)}" wechseln</span>
        </button>`;
      }
    });
    if (household && household.role === "owner") {
      html += `<button class="sheet-action" onclick="showInviteDialog()">
        <span class="sl-icon">👥</span><span>Sammlung teilen (Einladungscode)</span>
      </button>`;
    }
    html += `<button class="sheet-action danger" onclick="logout()">
      <span class="sl-icon">🚪</span><span>Abmelden</span>
    </button>`;
    actions.innerHTML = html;
  }

  // Account-Trigger (Mobile-Topbar) oeffnet ebenfalls das Sheet
  const trigger = document.getElementById("account-trigger");
  if (trigger) trigger.addEventListener("click", () => toggleMoreSheet(true));
}

function _accountDropdownHTML(me, household, roleLabel) {
  let html = `
    <div class="ad-header">
      <div class="ad-name">${escapeHtml(me.user.display_name || me.user.username)}</div>
      <div class="ad-role">${household ? escapeHtml(household.name) + " · " + (roleLabel[household.role] || household.role) : "Keine Sammlung"}</div>
    </div>`;
  if ((me.households || []).length > 1) {
    html += `<div class="ad-section">Sammlung wechseln</div>`;
    me.households.forEach((h) => {
      html += `<button class="ad-item ${h.household_id === me.active_household_id ? "is-active" : ""}" onclick="switchHousehold(${h.household_id})">
        <span>${h.household_id === me.active_household_id ? "● " : ""}${escapeHtml(h.name)}</span>
        <span class="ad-value">${roleLabel[h.role] || h.role}</span>
      </button>`;
    });
  }
  if (household && household.role === "owner") {
    html += `<div class="ad-section">Aktionen</div>
      <button class="ad-item" onclick="showInviteDialog()"><span>👥 Sammlung teilen</span><span class="ad-value">Einladungscode</span></button>`;
  }
  html += `<div class="ad-section">&nbsp;</div>
    <button class="ad-item danger" onclick="logout()"><span>🚪 Abmelden</span></button>`;
  return html;
}

// ---- Mehr-Sheet (Mobile) ---------------------------------------------------
function toggleMoreSheet(open) {
  const sheet = document.getElementById("more-sheet");
  const backdrop = document.getElementById("more-backdrop");
  if (!sheet || !backdrop) return;
  if (open) {
    sheet.hidden = false; backdrop.hidden = false;
    requestAnimationFrame(() => { sheet.classList.add("visible"); backdrop.classList.add("visible"); });
  } else {
    sheet.classList.remove("visible"); backdrop.classList.remove("visible");
    setTimeout(() => { sheet.hidden = true; backdrop.hidden = true; }, 250);
  }
}

async function switchHousehold(householdId) {
  try {
    const resp = await fetch("/api/auth/household/switch", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({household_id: householdId}),
    });
    if (resp.ok) window.location.reload();
  } catch (e) { showToast("Fehler beim Wechseln", "error"); }
}

async function logout() {
  await fetch("/api/auth/logout", {method: "POST"});
  window.location.href = "/login";
}

async function showInviteDialog() {
  try {
    const resp = await fetch("/api/auth/household/invite-code");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Fehler");
    showToast("Einladungscode: " + data.code, "info", 10000);
  } catch (e) { showToast(e.message, "error"); }
}

// ---- Init on page load ---------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  setActiveNav();
  loadAdminState();
  initAccountMenu();
});
