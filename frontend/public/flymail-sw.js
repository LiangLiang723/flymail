const STATIC_CACHE = 'flymail-v2-static-v1';
const STATIC_FALLBACK = '/v2.html';
const STATIC_ASSETS = [
  '/v2.html',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-512-maskable.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function mustBypass(request, url) {
  if (url.origin !== self.location.origin) return true;
  if (request.method !== 'GET') return true;
  if (url.pathname.startsWith('/api/')) return true;
  if (/\/body(?:\/|$)|\/attachments?(?:\/|$)|\/backups?(?:\/|$)|\/upload(?:\/|$)/i.test(url.pathname)) return true;
  return false;
}

async function networkFirstNavigation(request) {
  try {
    return await fetch(request);
  } catch {
    return (await caches.match(STATIC_FALLBACK)) || Response.error();
  }
}

async function cacheFirstStatic(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && ['script', 'style', 'image', 'font', 'manifest'].includes(request.destination)) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (mustBypass(request, url)) return;
  if (request.mode === 'navigate') {
    event.respondWith(networkFirstNavigation(request));
    return;
  }
  event.respondWith(cacheFirstStatic(request));
});
