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
    // Erwartete 401s auf Auth-Seiten nicht als Fehler loggen (Konsolen-Hygiene)
    if (!(IS_AUTH_PAGE && err.message && err.message.includes("Nicht eingeloggt"))) {
      console.error(`API ${path} failed:`, err);
    }
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
  html += `<div class="ad-section">Aktionen</div>
    <button class="ad-item" onclick="showInviteDialog()"><span>👥 Sammlung teilen</span><span class="ad-value">${household && household.role === "owner" ? "Verwalten" : "Beitreten"}</span></button>`;
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

// ---- Sammlung teilen: Dialog -----------------------------------------------
async function showInviteDialog() { await openShareDialog(); }

async function openShareDialog() {
  const dlg = document.getElementById("share-dialog");
  const bd = document.getElementById("share-backdrop");
  if (!dlg) return;
  dlg.hidden = false; bd.hidden = false;
  requestAnimationFrame(() => { dlg.classList.add("visible"); bd.classList.add("visible"); });
  await refreshShareDialog();
}

function closeShareDialog() {
  const dlg = document.getElementById("share-dialog");
  const bd = document.getElementById("share-backdrop");
  if (!dlg) return;
  dlg.classList.remove("visible"); bd.classList.remove("visible");
  setTimeout(() => { dlg.hidden = true; bd.hidden = true; }, 250);
}

async function refreshShareDialog() {
  const setMsg = (id) => { const el = document.getElementById(id); if (el) { el.textContent = ""; } };
  setMsg("invite-msg"); setMsg("join-msg");

  if (!ME) {
    try {
      const resp = await fetch("/api/auth/me");
      const data = await resp.json();
      if (data.user) ME = data;
    } catch (e) { /* ignore */ }
  }
  const household = ME && (ME.households || []).find(h => h.household_id === ME.active_household_id);
  const isOwner = !!(household && household.role === "owner");

  const ownerArea = document.getElementById("share-owner-area");
  if (ownerArea) ownerArea.style.display = isOwner ? "" : "none";

  // Beitreten-Feld: nur wenn der Nutzer KEIN Member der aktiven Sammlung ist
  // (z.B. frischer Account) ODER explizit ueber den Beitritts-Link kam.
  const joinArea = document.getElementById("share-join-area");
  if (joinArea) joinArea.style.display = household ? "none" : "";

  // Code-Bereich: Owner sieht den Code
  const codeWrap = document.getElementById("share-code-area");
  const codeEl = document.getElementById("share-code-display");
  if (codeWrap) codeWrap.style.display = isOwner ? "" : "none";
  if (isOwner) {
    try {
      const resp = await fetch("/api/auth/household/invite-code");
      const data = await resp.json();
      if (resp.ok && data.code) {
        codeEl.innerHTML = `<span>${escapeHtml(data.code)}</span>
          <button class="btn btn-sm" style="margin-left:12px;" onclick="navigator.clipboard.writeText('${escapeHtml(data.code)}').then(()=>showToast('Code kopiert','success'))">Kopieren</button>`;
      } else {
        codeEl.innerHTML = `<span class="text-muted" style="font-size:0.9rem;">${escapeHtml(data.error || "Nicht verfügbar")}</span>`;
      }
    } catch (e) {
      codeEl.innerHTML = '<span class="text-muted">Fehler beim Laden</span>';
    }
  }

  if (isOwner) {
    const renameInput = document.getElementById("rename-input");
    if (renameInput && household && !renameInput.value) renameInput.value = household.name || "";
    await loadShareMembers();
  }
}

async function loadShareMembers() {
  const listEl = document.getElementById("share-members-list");
  if (!listEl) return;
  listEl.innerHTML = '<span class="text-muted">Lade Mitglieder…</span>';
  try {
    const resp = await fetch("/api/auth/household/members");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Fehler");
    const members = data.members || [];
    const me = ME ? ME.user.username : "";
    listEl.innerHTML = members.map(m => `
      <div class="share-member">
        <div class="sm-avatar">👤</div>
        <div>
          <div class="sm-name">${escapeHtml(m.username)}${m.username === me ? " (du)" : ""}</div>
        </div>
        <div class="sm-actions">
          <select onchange="changeMemberRole('${escapeHtml(m.username)}', this.value)" ${m.username === me ? "disabled" : ""}>
            <option value="owner" ${m.role === "owner" ? "selected" : ""}>Admin</option>
            <option value="editor" ${m.role === "editor" ? "selected" : ""}>Schreiben</option>
            <option value="viewer" ${m.role === "viewer" ? "selected" : ""}>Lesen</option>
          </select>
          <button class="btn btn-sm btn-danger" ${m.username === me ? "disabled" : ""} onclick="removeMember('${escapeHtml(m.username)}')" title="Entfernen">✕</button>
        </div>
      </div>`).join("");
  } catch (e) {
    listEl.innerHTML = `<span class="text-muted">${escapeHtml(e.message)}</span>`;
  }
}

async function renameHousehold() {
  const input = document.getElementById("rename-input");
  const msgEl = document.getElementById("rename-msg");
  const name = (input.value || "").trim();
  msgEl.textContent = ""; msgEl.className = "share-msg";
  if (!name) { msgEl.textContent = "Name eingeben."; msgEl.classList.add("err"); return; }
  try {
    const resp = await fetch("/api/auth/household/rename", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name}),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Fehler");
    msgEl.textContent = "✓ Umbenannt!";
    msgEl.classList.add("ok");
    showToast("Sammlung heißt jetzt: " + name, "success");
    ME = null; // Neu laden beim naechsten Dialog-Oeffnen
    setTimeout(() => window.location.reload(), 700);
  } catch (e) {
    msgEl.textContent = "✗ " + e.message; msgEl.classList.add("err");
  }
}

async function inviteByUsername() {
  const username = (document.getElementById("invite-username-input").value || "").trim();
  const role = document.getElementById("invite-role-select").value;
  const msgEl = document.getElementById("invite-msg");
  msgEl.textContent = ""; msgEl.className = "share-msg";
  if (!username) { msgEl.textContent = "Benutzername eingeben."; msgEl.classList.add("err"); return; }
  try {
    const resp = await fetch("/api/auth/household/invite", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username, role}),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Fehler");
    msgEl.textContent = "✓ " + username + " wurde hinzugefügt.";
    msgEl.classList.add("ok");
    document.getElementById("invite-username-input").value = "";
    await loadShareMembers();
    showToast(username + " eingeladen ✓", "success");
  } catch (e) {
    msgEl.textContent = "✗ " + e.message; msgEl.classList.add("err");
  }
}

async function changeMemberRole(username, role) {
  try {
    const resp = await fetch("/api/auth/household/members/role", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username, role}),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Fehler");
    showToast("Rolle von " + username + " geändert", "success");
    await loadShareMembers();
  } catch (e) {
    showToast(e.message, "error");
    await loadShareMembers();
  }
}

async function removeMember(username) {
  if (!confirm(username + " aus der Sammlung entfernen?")) return;
  try {
    const resp = await fetch("/api/auth/household/members/remove", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username}),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Fehler");
    showToast(username + " entfernt", "info");
    await loadShareMembers();
  } catch (e) { showToast(e.message, "error"); }
}

async function joinByCode() {
  const code = (document.getElementById("join-code-input").value || "").trim();
  const msgEl = document.getElementById("join-msg");
  msgEl.textContent = ""; msgEl.className = "share-msg";
  if (!code) { msgEl.textContent = "Code eingeben."; msgEl.classList.add("err"); return; }
  try {
    const resp = await fetch("/api/auth/household/join", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code}),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Ungültiger Code");
    msgEl.textContent = "✓ Du bist der Sammlung beigetreten!";
    msgEl.classList.add("ok");
    showToast("Willkommen in der Sammlung! 🎉", "success");
    setTimeout(() => window.location.reload(), 900);
  } catch (e) {
    msgEl.textContent = "✗ " + e.message; msgEl.classList.add("err");
  }
}

// ---- iOS-uebliches Drag-toDismiss an allen Bottom-Sheets -------------------
// Nutzer erwartet: am dickeren Balken (Handle) nach unten ziehen schliesst das
// Sheet - wie in jeder iOS-App. Implementiert: Pointer-Events (Touch+Maus),
// 1:1-Folge waehrend des Ziehens, Schliessen ab 80px oder schneller Bewegung.
function _installSheetDrag(sheetSel, closeFn) {
  const sheet = document.querySelector(sheetSel);
  if (!sheet || sheet.dataset.dragInstalled) return;
  sheet.dataset.dragInstalled = "1";

  let startY = 0, curY = 0, dragging = false, startT = 0;
  const THRESHOLD = 80;   // px: darunter schliesst es
  const VELOCITY = 0.5;   // px/ms: schnelles Runterziehen schliesst ebenfalls

  const handle = sheet.querySelector(".sheet-handle") || sheet;

  function onStart(e) {
    // Nur oben beginnen (Handle ODER obere 60px vom Sheet-Rand)
    const r = sheet.getBoundingClientRect();
    const y = e.touches ? e.touches[0].clientY : e.clientY;
    const onHandle = e.target.closest && e.target.closest(".sheet-handle");
    if (!onHandle && y - r.top > 60) return; // mitten im Inhalt: normales Scrollen
    dragging = true;
    startY = y; startT = Date.now();
    curY = 0;
    sheet.style.transition = "none";
    if (e.cancelable) e.preventDefault();
  }
  function onMove(e) {
    if (!dragging) return;
    const y = e.touches ? e.touches[0].clientY : e.clientY;
    curY = y - startY;
    if (curY > 0) {
      sheet.style.transform = "translateY(" + curY + "px)";
      sheet.style.opacity = String(Math.max(0.4, 1 - curY / 400));
      if (e.cancelable) e.preventDefault();
    }
  }
  function onEnd() {
    if (!dragging) return;
    dragging = false;
    sheet.style.transition = "";
    sheet.style.opacity = "";
    const dur = Date.now() - startT;
    const fast = dur > 0 && (curY / dur) > VELOCITY && curY > 20;
    if (curY > THRESHOLD || fast) {
      sheet.style.transform = "";
      closeFn();
    } else {
      sheet.style.transform = "";
    }
  }

  handle.addEventListener("touchstart", onStart, { passive: false });
  sheet.addEventListener("touchstart", onStart, { passive: false });
  sheet.addEventListener("touchmove", onMove, { passive: false });
  sheet.addEventListener("touchend", onEnd);
  sheet.addEventListener("touchcancel", onEnd);
  // Desktop-Unterstützung (Maus)
  sheet.addEventListener("mousedown", (e) => {
    const r = sheet.getBoundingClientRect();
    if (e.clientY - r.top > 60) return;
    const mv = (ev) => onMove(ev);
    const up = () => { onEnd(); document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); };
    onStart(e);
    document.addEventListener("mousemove", mv);
    document.addEventListener("mouseup", up);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  _installSheetDrag("#more-sheet", () => toggleMoreSheet(false));
  _installSheetDrag("#share-dialog", () => closeShareDialog());
});

// ---- Theme (Dark/Light/Auto) ----------------------------------------------
// Modus: 'dark' (Standard) | 'light' | 'auto' (folgt dem Geraet).
// Sofortiger Wechsel ohne Reload; Setting wird pro Nutzer persistiert.
function applyTheme(mode) {
  const mql = window.matchMedia("(prefers-color-scheme: light)");
  const light = mode === "light" || (mode === "auto" && mql.matches);
  document.documentElement.classList.toggle("theme-light", light);
  // Meta theme-color fuer Browser-UI anpassen
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", light ? "#f2f4f8" : "#0a0e14");
}

async function setTheme(mode) {
  if (!["dark", "light", "auto"].includes(mode)) return;
  applyTheme(mode);
  localStorage.setItem("swu-theme", mode);  // Auth-Seiten-Fallback (kein Settings-Call dort)
  try {
    await api("/api/settings", { method: "POST", body: { theme: mode } });
  } catch (e) { /* Setting-Store optional */ }
  showToast("Design: " + (mode === "auto" ? "Automatisch" : mode === "light" ? "Hell" : "Dunkel"), "info", 2500);
}

function _migrateLegacyTheme() {
  // Altes DB-Setting 'theme: dark' (veraltet) migriert Richtung auto
}

async function initTheme() {
  let mode = "dark";
  // Auth-Seiten: kein Settings-Call (401-Noise) - localStorage-Fallback
  if (IS_AUTH_PAGE) {
    mode = localStorage.getItem("swu-theme") || "dark";
    if (mode !== "light" && mode !== "dark" && mode !== "auto") mode = "dark";
  } else {
    try {
      const s = await api("/api/settings");
      if (s.theme === "light" || s.theme === "dark" || s.theme === "auto") mode = s.theme;
      localStorage.setItem("swu-theme", mode);
    } catch (e) { /* Default dark */ }
  }
  applyTheme(mode);
  // Auto-Modus: auf Systemwechsel reagieren (live)
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    api("/api/settings").then(s => { if (s.theme === "auto") applyTheme("auto"); }).catch(() => {});
  });
}

// ---- Init on page load ---------------------------------------------------
// Auth-Seiten (login/register/reset/no-household) haben keinen Nutzerkontext:
// /api/admin/status und /api/settings antworten dort immer 401 -> Konsolen-Noise.
const IS_AUTH_PAGE = document.body.classList.contains("auth-body");

document.addEventListener("DOMContentLoaded", () => {
  setActiveNav();
  if (!IS_AUTH_PAGE) {
    loadAdminState();
    initAccountMenu();
  }
  initTheme();
});
