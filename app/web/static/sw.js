/* SWU Card Manager — Service Worker
   Caches the app shell (CSS/JS/icons) for offline loading and fast startup.
   Network-first for pages, cache-first for static assets. */
const CACHE_NAME = "swu-manager-v1";
const APP_SHELL = [
  "/static/style.css",
  "/static/common.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // only handle same-origin GET
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Cache-first for static assets
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

  // Network-first for pages and API (collection data must be fresh)
  // Do NOT cache /api/ responses.
  if (url.pathname.startsWith("/api/")) return;
});
