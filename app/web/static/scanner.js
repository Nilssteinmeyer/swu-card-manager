/* =========================================================================
   SWU Card Manager — Scanner JS v2
   Motion-detection triggered scanning with haptic feedback

   Ablauf:
   1) Kamera an → Suche nach Karte (motion detection)
   2) Karte erkannt + stabilisiert → Bild aufnehmen → Scan an Backend
   3) Ergebnis anzeigen als Popup → Vibration
   4) User bestätigt Ja/Nein → Vibration
   5) Popup schließt → Prozess beginnt von vorne
   ========================================================================= */

let videoStream = null;
let scanTimer = null;
let isScanning = false;
let lastScanResult = null;
let scanInProgress = false;
let prevFrame = null;
let motionBaseline = null;
let lastScanPhoto = null;

// User settings (loaded from /api/settings)
let USER_SETTINGS = {
  auto_scan: false,
  scan_interval_ms: 800,
  freeze_camera_during_scan: true,
  stabilization_delay_ms: 1000,
  capture_resolution: 1200,
  jpeg_quality: 90,
  camera_facing: "environment",
  haptic_on_scan: true,
  haptic_on_confirm: true,
  haptic_on_reject: true,
  haptic_pattern_confirm: "50,30,80",
  show_confidence_bar: true,
  show_processing_time: false,
  max_candidates: 5,
  bulk_scan_mode: false,
  bulk_min_confidence: 0.85,
  scan_language: "de",
};

// Language of the card currently shown in the scan overlay ("de" | "en")
let scanCardLanguage = "de";

function parseBool(v) { return v === "true" || v === true; }
function parseInt_(v, def) { const n = parseInt(v); return isNaN(n) ? def : n; }
function parsePattern(v, def) { try { const p = v.split(",").map(Number).filter(n => !isNaN(n) && n > 0); return p.length ? p : def; } catch { return def; } }

async function loadUserSettings() {
  try {
    const s = await api("/api/settings");
    USER_SETTINGS.auto_scan = parseBool(s.auto_scan);
    USER_SETTINGS.scan_interval_ms = parseInt_(s.scan_interval_ms, 800);
    USER_SETTINGS.freeze_camera_during_scan = parseBool(s.freeze_camera_during_scan);
    USER_SETTINGS.stabilization_delay_ms = parseInt_(s.stabilization_delay_ms, 1000);
    USER_SETTINGS.capture_resolution = parseInt_(s.capture_resolution, 1200);
    USER_SETTINGS.jpeg_quality = parseInt_(s.jpeg_quality, 90);
    USER_SETTINGS.camera_facing = s.camera_facing || "environment";
    USER_SETTINGS.haptic_on_scan = parseBool(s.haptic_on_scan);
    USER_SETTINGS.haptic_on_confirm = parseBool(s.haptic_on_confirm);
    USER_SETTINGS.haptic_on_reject = parseBool(s.haptic_on_reject);
    USER_SETTINGS.haptic_pattern_confirm = parsePattern(s.haptic_pattern_confirm, [50,30,80]);
    USER_SETTINGS.show_confidence_bar = parseBool(s.show_confidence_bar);
    USER_SETTINGS.show_processing_time = parseBool(s.show_processing_time);
    USER_SETTINGS.max_candidates = parseInt_(s.max_candidates, 5);
    USER_SETTINGS.bulk_scan_mode = parseBool(s.bulk_scan_mode);
    USER_SETTINGS.scan_language = s.scan_language || s.card_language || "de";
    USER_SETTINGS.bulk_min_confidence = (parseInt_(s.bulk_min_confidence, 85) || 85) / 100;
    // Bulk session counter
    if (typeof bulkSessionCount === "number") {
      document.getElementById("bulk-session-counter")?.classList.toggle("hidden", !USER_SETTINGS.bulk_scan_mode);
    }
  } catch (e) {
    console.warn("Could not load settings, using defaults:", e);
  }
}

function vibrate(pattern) {
  if (!navigator.vibrate) return;
  // Check the appropriate setting based on context is done by callers
  navigator.vibrate(pattern);
}

document.addEventListener("DOMContentLoaded", () => {
  initScanner();
});

async function initScanner() {
  const startBtn = document.getElementById("start-camera-btn");
  const stopBtn = document.getElementById("stop-camera-btn");
  const confirmBtn = document.getElementById("btn-confirm");
  const rejectBtn = document.getElementById("btn-reject");

  if (startBtn) startBtn.addEventListener("click", startCamera);
  if (stopBtn) stopBtn.addEventListener("click", stopCamera);
  const manualBtn = document.getElementById("manual-scan-btn");
  if (manualBtn) manualBtn.addEventListener("click", triggerManualScan);
  if (confirmBtn) confirmBtn.addEventListener("click", () => confirmScan(true));
  if (rejectBtn) rejectBtn.addEventListener("click", () => confirmScan(false));
  const cancelBtn = document.getElementById("btn-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => cancelScan());
  const qtyMinus = document.getElementById("btn-qty-minus");
  const qtyPlus = document.getElementById("btn-qty-plus");
  if (qtyMinus) qtyMinus.addEventListener("click", () => changeQty(-1));
  if (qtyPlus) qtyPlus.addEventListener("click", () => changeQty(1));

  // Load user settings before first scan
  await loadUserSettings();

  // Restore undo availability (survives page reloads)
  refreshUndoBar();
}

let scanQuantity = 1;
function changeQty(delta) {
  scanQuantity = Math.max(1, Math.min(99, scanQuantity + delta));
  const display = document.getElementById("qty-display");
  if (display) display.textContent = scanQuantity;
}

function cancelScan() {
  // Just close the popup, don't add or delete anything
  hideResultOverlay();
  lastScanResult = null;
  lastScanPhoto = null;
  scanInProgress = false;
  motionBaseline = null;
  prevFrame = null;
  const video = document.getElementById("camera-video");
  if (video && isScanning) {
    video.play();
    updateOverlay("searching", "Suche nach Karte...");
  }
  if (navigator.vibrate) navigator.vibrate(50);
}

async function startCamera() {
  const video = document.getElementById("camera-video");
  const startBtn = document.getElementById("start-camera-btn");
  const stopBtn = document.getElementById("stop-camera-btn");
  if (!video) return;

  try {
    videoStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: USER_SETTINGS.camera_facing },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    });
    video.srcObject = videoStream;
    await video.play();
    if (startBtn) startBtn.classList.add("hidden");
    if (stopBtn) stopBtn.classList.remove("hidden");
    isScanning = true;
    prevFrame = null;
    motionBaseline = null;
    // Mode-specific UI: manual shows the scan button, auto scans on its own
    const manualBtn = document.getElementById("manual-scan-btn");
    if (USER_SETTINGS.auto_scan) {
      if (manualBtn) manualBtn.classList.add("hidden");
      updateOverlay("searching", "Suche nach Karte...");
    } else {
      if (manualBtn) manualBtn.classList.remove("hidden");
      updateOverlay("searching", "Karte hinlegen und scannen");
    }
    // Timer only drives the scan in auto mode (manual triggers via button)
    scanTimer = setInterval(autoScan, Math.max(200, USER_SETTINGS.scan_interval_ms));
  } catch (err) {
    let msg = "Kamera nicht verfügbar";
    if (err.name === "NotAllowedError") msg = "Kamerazugriff verweigert. Bitte erlauben.";
    else if (err.name === "NotFoundError") msg = "Keine Kamera gefunden.";
    showToast(msg, "error", 5000);
    updateOverlay("error", msg);
  }
}

function stopCamera() {
  if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
  isScanning = false;
  if (videoStream) { videoStream.getTracks().forEach((t) => t.stop()); videoStream = null; }
  const video = document.getElementById("camera-video");
  if (video) video.srcObject = null;
  document.getElementById("start-camera-btn")?.classList.remove("hidden");
  document.getElementById("stop-camera-btn")?.classList.add("hidden");
  document.getElementById("manual-scan-btn")?.classList.add("hidden");
  updateOverlay("idle", "Kamera gestoppt");
  hideResultOverlay();
}

function updateOverlay(state, text) {
  const iconEl = document.getElementById("scan-overlay-icon");
  const textEl = document.getElementById("scan-overlay-text");
  const frame = document.getElementById("scan-frame");
  if (!iconEl || !textEl) return;
  textEl.textContent = text;
  const states = {
    idle:      { icon: "📷", color: "#666" },
    searching: { icon: "🔍", color: "#3b82f6" },
    scanning:  { icon: "⏳", color: "#f59e0b" },
    found:     { icon: "✅", color: "#22c55e" },
    error:     { icon: "❌", color: "#ef4444" },
  };
  const s = states[state] || states.idle;
  iconEl.textContent = s.icon;
  iconEl.style.color = s.color;
  textEl.style.color = s.color;
  if (frame) {
    frame.style.borderColor = s.color;
    if (state === "scanning") frame.style.animation = "pulse-border 0.8s infinite";
    else frame.style.animation = "";
  }
}

/* --- Motion detection: only scan when a card is placed and stabilizes --- */
function detectMotion(video) {
  const canvas = document.createElement("canvas");
  const w = 160, h = 120;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, w, h);
  const curr = ctx.getImageData(0, 0, w, h);
  if (!prevFrame) { prevFrame = curr; return null; }
  let diff = 0;
  const d1 = curr.data, d2 = prevFrame.data;
  for (let i = 0; i < d1.length; i += 4) {
    diff += (Math.abs(d1[i]-d2[i]) + Math.abs(d1[i+1]-d2[i+1]) + Math.abs(d1[i+2]-d2[i+2])) / 3;
  }
  diff = diff / (w * h);
  prevFrame = curr;
  return diff;
}

let bulkSessionCount = 0;

async function autoScan() {
  // Timer path: only active when auto_scan is enabled.
  if (!isScanning || scanInProgress) return;
  if (!USER_SETTINGS.auto_scan) return;
  const video = document.getElementById("camera-video");
  if (!video || !video.videoWidth) return;

  // If we already scanned and user hasn't confirmed yet, wait
  if (motionBaseline === "scanned") return;

  // Stabilization delay after start/confirm
  if (motionBaseline === null) {
    motionBaseline = "ready";
    await new Promise(r => setTimeout(r, USER_SETTINGS.stabilization_delay_ms));
    return;
  }

  await performScan();
}

// Manual trigger (button) — same pipeline, no auto-scan settings required.
async function triggerManualScan() {
  if (!isScanning || scanInProgress) return;
  const video = document.getElementById("camera-video");
  if (!video || !video.videoWidth) {
    showToast("Kamera ist noch nicht bereit", "error");
    return;
  }
  if (motionBaseline === null) motionBaseline = "ready";
  await performScan();
}

async function performScan() {
  const video = document.getElementById("camera-video");
  if (!video || !video.videoWidth) return;

  // Capture frame BEFORE scan starts
  scanInProgress = true;
  updateOverlay("scanning", "Scan läuft...");

  // Freeze camera preview if enabled in settings (not in bulk mode — camera keeps running)
  const freeze = USER_SETTINGS.freeze_camera_during_scan && !USER_SETTINGS.bulk_scan_mode;
  if (freeze && video) video.pause();

  const canvas = document.createElement("canvas");
  const scale = Math.min(1, USER_SETTINGS.capture_resolution / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  const dataUrl = canvas.toDataURL("image/jpeg", USER_SETTINGS.jpeg_quality / 100);

  // Store the captured photo for training on confirm
  lastScanPhoto = dataUrl;

  try {
    const result = await api("/api/scan", { method: "POST", body: { image: dataUrl } });
    lastScanResult = result;

    // ---- Bulk mode: auto-accept above threshold, keep scanning -------------
    if (USER_SETTINGS.bulk_scan_mode && result.confidence >= USER_SETTINGS.bulk_min_confidence && result.card_id) {
      bulkSessionCount += 1;
      const bulkEl = document.getElementById("bulk-session-counter");
      if (bulkEl) {
        bulkEl.classList.remove("hidden");
        bulkEl.textContent = `⚡ ${bulkSessionCount} Karten in dieser Session — ${result.card_name} (${(result.confidence * 100).toFixed(1)}%)`;
      }
      if (USER_SETTINGS.haptic_on_confirm && navigator.vibrate) navigator.vibrate(USER_SETTINGS.haptic_pattern_confirm);
      updateOverlay("found", "Karte erkannt!");
      // auto-confirm in the background (no popup)
      api("/api/scan/confirm", {
        method: "POST",
        body: {
          scan_id: result.scan_id,
          confirmed: true,
          card_id: result.card_id,
          original_card_id: result.card_id,
          quantity: 1,
          is_foil: false,
          language: USER_SETTINGS.scan_language || "de",
          photo: lastScanPhoto,
        },
      }).then(() => {
        bulkSessionCount = bulkSessionCount; // session continues
      }).catch(err => {
        showToast(`Auto-Bestätigung fehlgeschlagen: ${err.message}`, "error");
      });
      scanInProgress = false;
      // shorter cooldown in bulk mode, then continue scanning
      await new Promise(r => setTimeout(r, 1500));
      motionBaseline = "ready";
      return;
    }

    // ---- Normal mode: show popup --------------------------------------------
    if (USER_SETTINGS.haptic_on_scan && navigator.vibrate) navigator.vibrate(150);
    updateOverlay("found", "Karte erkannt!");
    showResultOverlay(result);
    motionBaseline = "scanned";
    // Camera stays paused while popup is shown
  } catch (err) {
    updateOverlay("error", `Scan-Fehler: ${err.message}`);
    scanInProgress = false;
    motionBaseline = "ready"; // Allow retry
    // Resume camera on error
    if (video) video.play();
  }
}

// Update the scan overlay for the currently selected card language.
function _applyScanLanguage() {
  const best = lastScanResult?.candidates?.[0] || {};
  const isDe = scanCardLanguage === "de";
  const name = isDe ? (best.name_de || best.name || "") : (best.name || "");
  const subtitle = isDe ? (best.subtitle_de || best.subtitle || "") : (best.subtitle || "");
  document.getElementById("result-name").textContent = name || "Unbekannt";
  document.getElementById("result-sub").textContent = subtitle;
  // Flag buttons: active state
  const btnDe = document.getElementById("lang-btn-de");
  const btnEn = document.getElementById("lang-btn-en");
  if (btnDe) btnDe.classList.toggle("lang-active", isDe);
  if (btnEn) btnEn.classList.toggle("lang-active", !isDe);
  // Card image in the selected language
  const imgArea = document.getElementById("result-image-area");
  if (best.card_id) {
    imgArea.innerHTML = `<img src="${cardImageUrl(best.card_id)}?lang=${scanCardLanguage}" alt="${escapeHtml(name)}" onerror="this.parentElement.innerHTML='🃏'">`;
  }
}

function setScanLanguage(lang) {
  scanCardLanguage = lang === "en" ? "en" : "de";
  _applyScanLanguage();
}

function showResultOverlay(result) {
  const overlay = document.getElementById("scan-result-overlay");
  if (!overlay) return;

  scanCardLanguage = USER_SETTINGS.scan_language || "de";
  const cardName = result.card_name || "Unbekannt";
  const best = result.candidates?.[0] || {};

  const imgArea = document.getElementById("result-image-area");
  if (best.card_id) {
    imgArea.innerHTML = `<img src="${cardImageUrl(best.card_id)}?lang=${scanCardLanguage}" alt="${escapeHtml(cardName)}" onerror="this.parentElement.innerHTML='🃏'">`;
  } else {
    imgArea.innerHTML = "🃏";
  }

  document.getElementById("result-name").textContent = cardName;
  document.getElementById("result-sub").textContent = best.subtitle || "";

  const metaText = [best.set_id, best.card_number ? `#${best.card_number}` : ""].filter(Boolean).join(" • ");
  document.getElementById("result-meta").textContent = metaText;

  const confEl = document.getElementById("result-confidence");
  const confPct = (result.confidence * 100).toFixed(1);
  const confClass = result.confidence >= 0.85 ? "conf-high" : result.confidence >= 0.5 ? "conf-medium" : "conf-low";
  if (USER_SETTINGS.show_confidence_bar) {
    const timeText = USER_SETTINGS.show_processing_time && result.processing_time_ms ? ` • ${result.processing_time_ms}ms` : "";
    confEl.innerHTML = `<div class="conf-bar ${confClass}"><div class="conf-fill" style="width:${confPct}%"></div></div><span>${confPct}% Konfidenz${timeText}</span>`;
  } else {
    confEl.innerHTML = "";
  }

  const foilCheck = document.getElementById("is-foil-checkbox");
  if (foilCheck) foilCheck.checked = false;
  scanQuantity = 1;
  const qtyDisplay = document.getElementById("qty-display");
  if (qtyDisplay) qtyDisplay.textContent = "1";

  // Reset quantity to 1
  scanQuantity = 1;
  const qtyDisp = document.getElementById("qty-display");
  if (qtyDisp) qtyDisp.textContent = "1";

  overlay.style.display = "flex";

  const candList = document.getElementById("candidate-list");
  if (result.candidates && result.candidates.length > 1) {
    let html = '<div class="cand-title">Alternative Kandidaten:</div>';
    result.candidates.slice(1, Math.max(2, USER_SETTINGS.max_candidates)).forEach((c) => {
      const candName = scanCardLanguage === "de" ? (c.name_de || c.name) : c.name;
      html += `<div class="cand-item" onclick="selectCandidate('${escapeHtml(c.card_id)}', '${escapeHtml(c.name)}', '${escapeHtml(c.subtitle || "")}', '${escapeHtml(c.set_id)}', '${escapeHtml(c.card_number)}')">
        <div><div class="cand-name">${escapeHtml(candName)}</div><div class="cand-meta">${escapeHtml(c.set_id)} • #${escapeHtml(c.card_number)}</div></div>
        <div class="cand-score">${formatConfidence(c.score)}</div>
      </div>`;
    });
    candList.innerHTML = html;
  } else {
    candList.innerHTML = "";
  }

  // Apply the selected card language to name/subtitle/flags/image
  _applyScanLanguage();
}

function selectCandidate(cardId, name, subtitle, setId, cardNumber) {
  lastScanResult.card_id = cardId;
  lastScanResult.card_name = name;
  if (!lastScanResult.candidates) lastScanResult.candidates = [];
  // keep the full candidate (with name_de/subtitle_de) if present in the list
  const existing = (lastScanResult.candidates || []).find(c => c.card_id === cardId) || {};
  lastScanResult.candidates[0] = { ...existing, card_id: cardId, name, subtitle, set_id: setId, card_number: cardNumber };

  const imgArea = document.getElementById("result-image-area");
  imgArea.innerHTML = `<img src="${cardImageUrl(cardId)}?lang=${scanCardLanguage}" alt="${escapeHtml(name)}" onerror="this.parentElement.innerHTML='🃏'">`;
  _applyScanLanguage();
  document.getElementById("result-name").textContent = name;
  document.getElementById("result-sub").textContent = subtitle || "";
  document.getElementById("result-meta").textContent = `${setId} • #${cardNumber}`;
  document.getElementById("candidate-list").innerHTML = "";
}

function hideResultOverlay() {
  const overlay = document.getElementById("scan-result-overlay");
  if (overlay) overlay.style.display = "none";
}

async function confirmScan(confirmed) {
  if (!lastScanResult) return;

  let cardId = lastScanResult.card_id;
  if (!cardId && lastScanResult.candidates?.[0]) {
    cardId = lastScanResult.candidates[0].card_id;
  }

  const foilCheckbox = document.getElementById("is-foil-checkbox");
  const isFoil = foilCheckbox ? foilCheckbox.checked : false;

  try {
    const result = await api("/api/scan/confirm", {
      method: "POST",
      body: {
        scan_id: lastScanResult.scan_id,
        confirmed: confirmed,
        card_id: cardId,
        add_to_collection: confirmed,
        is_foil: isFoil,
        quantity: confirmed ? scanQuantity : 1,
        language: scanCardLanguage,
        photo: confirmed ? lastScanPhoto : null,  // Only send photo on confirm
      },
    });

    if (confirmed) {
      const foilText = isFoil ? " (Foil)" : "";
      showToast(`"${lastScanResult.card_name}"${foilText} hinzugefügt ✓`, "success");
      if (USER_SETTINGS.haptic_on_confirm && navigator.vibrate) navigator.vibrate(USER_SETTINGS.haptic_pattern_confirm);
      // Show the undo bar with the confirmed card
      showUndoBar(lastScanResult.card_name || cardId, result.undo_id);
    } else {
      showToast("Verworfen — Foto gelöscht", "info");
      if (USER_SETTINGS.haptic_on_reject && navigator.vibrate) navigator.vibrate(200);
    }

    // Hide overlay and resume scanning
    hideResultOverlay();
    lastScanResult = null;
    lastScanPhoto = null;
    scanInProgress = false;
    motionBaseline = null;
    prevFrame = null;

    // Resume camera
    const video = document.getElementById("camera-video");
    if (video && isScanning) {
      video.play();
      updateOverlay("searching", "Suche nach Karte...");
    }
  } catch (err) {
    showToast(`Fehler: ${err.message}`, "error");
  }
}

// ---- Undo last scan action ---------------------------------------------------
function showUndoBar(cardName, undoId) {
  const bar = document.getElementById("undo-bar");
  const info = document.getElementById("undo-info");
  if (!bar) return;
  if (info) info.textContent = cardName ? `"${cardName}"` : "";
  bar.classList.remove("hidden");
  bar.dataset.undoId = undoId || "";
}

function hideUndoBar() {
  const bar = document.getElementById("undo-bar");
  if (bar) bar.classList.add("hidden");
}

async function undoLastScan() {
  const btn = document.getElementById("btn-undo-last");
  if (btn) btn.disabled = true;
  try {
    const result = await api("/api/scan/undo", { method: "POST", body: {} });
    if (result.undone) {
      const details = (result.details || []).join(", ");
      showToast(`Rückgängig gemacht ✓${details ? " (" + details + ")" : ""}`, "success");
      if (USER_SETTINGS.haptic_on_reject && navigator.vibrate) navigator.vibrate(200);
      hideUndoBar();
      // refresh bulk counter if visible
      const bulkEl = document.getElementById("bulk-session-counter");
      if (bulkEl && !bulkEl.classList.contains("hidden") && bulkSessionCount > 0) {
        bulkSessionCount -= 1;
        bulkEl.textContent = `⚡ ${bulkSessionCount} Karten in dieser Session`;
      }
    } else {
      showToast(result.error || "Nichts zum Rückgängigmachen", "info");
    }
  } catch (err) {
    showToast(`Undo fehlgeschlagen: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Restore undo bar state on page load (undo survives a page reload)
async function refreshUndoBar() {
  try {
    const p = await api("/api/scan/undo/preview");
    if (p.available) showUndoBar(p.card_id, null);
  } catch (e) { /* silent */ }
}

window.addEventListener("beforeunload", () => {
  if (videoStream) videoStream.getTracks().forEach((t) => t.stop());
});
