const CACHE = 'rmifo-v42';
const STATIC = [
  '/assets/css/app.css?v=7',
  '/assets/js/app.js',
  '/assets/js/radar.js?v=7',
  '/assets/js/ui-enhancements.js',
  '/manifest.json',
  '/assets/icons/icon-192.png',
  '/assets/icons/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(STATIC))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
      .then(() => {
        // Force reload all open tabs after activation so fresh content loads immediately
        return self.clients.matchAll({ type: 'window' }).then(clients => {
          clients.forEach(client => client.navigate(client.url));
        });
      })
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API and HTML: always network-first (never serve stale data or HTML)
  if (url.pathname.startsWith('/api.php') ||
      url.pathname === '/' ||
      url.pathname.endsWith('.html') ||
      url.pathname.endsWith('.php')) {
    e.respondWith(
      fetch(e.request).catch(() => caches.match(e.request))
    );
    return;
  }

  // Static assets: cache-first with network fallback
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(resp => {
        if (resp && resp.status === 200 && e.request.method === 'GET') {
          const clone = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return resp;
      });
    })
  );
});
