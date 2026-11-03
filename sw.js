const CACHE_NAME = 'golocal-shell-v3';
const APP_SHELL = [
  '/',
  '/index.html',
  '/styles.css',
  '/app-states.css',
  '/app.js',
  '/bus-app.js',
  '/manifest.webmanifest',
  '/stations.json',
  '/icons/golocal-mark.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;

  if (url.pathname === '/stations.json') {
    event.respondWith(caches.open(CACHE_NAME).then(async cache => {
      const cached = await cache.match(request);
      const update = fetch(request).then(response => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      });
      return cached || update;
    }));
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then(response => {
      if (response.ok) caches.open(CACHE_NAME).then(cache => cache.put('/index.html', response.clone()));
      return response;
    }).catch(async () => (await caches.match('/index.html')) || Response.error()));
    return;
  }

  event.respondWith(caches.match(request).then(cached => cached || fetch(request).then(response => {
    if (response.ok) caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone()));
    return response;
  })));
});
