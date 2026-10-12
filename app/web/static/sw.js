/* SWU Card Manager — Service Worker v21
   Cache-first fuer versionierte Assets, network-first fuer Seiten.
   Update-Strategie: bei neuem SW sofort aktivieren + alte Caches killen. */
const CACHE_NAME = "swu-manager-v26";
const APP_SHELL = [
  "/static/style.css?v=22",
  "/static/platform.css?v=6",
  "/static/common.js?v=16",
  "/static/scanner.js?v=16",
  "/static/manifest.json?v=1",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting(); // neuer SW uebernimmt SOFORT
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim(); // offene Tabs sofort uebernehmen
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Versionierte Static-Assets: cache-first (vom Server mit ?v= eindeutig)
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return resp;
        });
      })
    );
    return;
  }

  // API nie cachen
  if (url.pathname.startsWith("/api/")) return;
  // Seiten (HTML): network-first mit Cache-Fallback (offline)
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() => caches.match(event.request))
    );
  }
});
