// Service worker of "Am Tisch": the app opens without network, card images stay cached.
// The server fills in VERSION (a hash of the app's files) and FILES – a new version installs in the background
// and the app offers "Neu laden" (it never swaps itself under the user's fingers).
const VERSION = "__VERSION__";
const FILES = __FILES__;
const SHELL = `amtisch-shell-${VERSION}`;
const IMAGES = "amtisch-images";
const IMAGES_MAX = 900;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)));
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const key of await caches.keys()) if (key.startsWith("amtisch-shell-") && key !== SHELL) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => { if (e.data === "skip-waiting") self.skipWaiting(); });

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin) {
    if (!url.pathname.startsWith("/app/") || url.pathname === "/app/sw.js") return;  // the API is never cached here
    if (req.mode === "navigate") {
      e.respondWith(caches.open(SHELL).then(async (c) => (await c.match("/app/")) || fetch(req)));
      return;
    }
    e.respondWith(caches.open(SHELL).then(async (c) => {
      const hit = await c.match(req, { ignoreSearch: true });
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) c.put(req, res.clone());
      return res;
    }));
    return;
  }
  if (url.hostname.endsWith(".scryfall.io")) {  // card images: cache first, oldest out when full
    e.respondWith(caches.open(IMAGES).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) { await c.put(req, res.clone()); trim(c); }
      return res;
    }));
  }
});

async function trim(cache) {
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - IMAGES_MAX))) await cache.delete(k);
}
