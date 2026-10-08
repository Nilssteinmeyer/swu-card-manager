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
  document.querySelectorAll(".navbar-nav a").forEach((a) => {
    const href = a.getAttribute("href");
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

// ---- Account menu (user + household switcher + logout) --------------------
async function initAccountMenu() {
  const container = document.getElementById("account-menu");
  if (!container) return;
  let me;
  try {
    const resp = await fetch("/api/auth/me");
    if (resp.status === 401) return; // login page etc.
    me = await resp.json();
  } catch (e) { return; }
  if (!me.user) return;

  const roleLabel = { owner: "Owner", editor: "Editor", viewer: "Zuschauer" };
  const household = (me.households || []).find(h => h.household_id === me.active_household_id) || me.households[0];

  container.innerHTML = `
    <button class="account-btn" id="account-btn">
      <span>👤 ${escapeHtml(me.user.username)}</span>
      ${household ? `<span style="color:var(--muted);">· ${escapeHtml(household.name)}</span>` : ""}
      <span>▾</span>
    </button>
    <div class="account-dropdown hidden" id="account-dropdown">
      <div class="ad-header">
        <div class="ad-name">${escapeHtml(me.user.display_name || me.user.username)}</div>
        <div class="ad-role">${household ? roleLabel[household.role] || household.role : "Keine Sammlung"}</div>
      </div>
      ${(me.households || []).length > 1 ? `
        <div class="ad-section">Sammlung wechseln</div>
        ${me.households.map(h => `
          <div class="ad-item" onclick="switchHousehold(${h.household_id})">
            <span>${h.household_id === me.active_household_id ? "● " : ""}${escapeHtml(h.name)}</span>
            <span class="ad-value">${roleLabel[h.role] || h.role}</span>
          </div>`).join("")}
      ` : ""}
      <div class="ad-section">Aktionen</div>
      ${household && household.role === "owner" ? `
        <div class="ad-item" onclick="showInviteDialog()">
          <span>👥 Sammlung teilen</span><span class="ad-value">Einladungscode</span>
        </div>` : ""}
      <div class="ad-item" onclick="logout()">
        <span>🚪 Abmelden</span>
      </div>
    </div>`;

  document.getElementById("account-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    document.getElementById("account-dropdown").classList.toggle("hidden");
  });
  document.addEventListener("click", () => {
    document.getElementById("account-dropdown")?.classList.add("hidden");
  });
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
    showToast("Einladungscode: " + data.code + " (im Chat/Toast sichtbar)", "info", 10000);
  } catch (e) { showToast(e.message, "error"); }
}

// ---- Init on page load ---------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  setActiveNav();
  loadAdminState();
  initAccountMenu();
});
