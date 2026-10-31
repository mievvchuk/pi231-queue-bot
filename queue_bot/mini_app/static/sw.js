"use strict";
const CACHE = 'pi231-shell-v1';
const SHELL = ['/app/', '/app/app.js', '/app/features.js', '/app/icons.js',
  '/app/pwa.js', '/app/app.css', '/app/theme.css', '/app/glass.css',
  '/app/manifest.webmanifest', '/app/icon-192.png', '/app/icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('pi231-shell-') && key !== CACHE).map(key => caches.delete(key)),
  )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Never intercept authentication, API, avatars, POSTs, or external resources.
  if (event.request.method !== 'GET' || url.origin !== self.location.origin ||
      !SHELL.includes(url.pathname)) return;
  event.respondWith((async () => {
    const key = url.pathname;
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(event.request);
      if (response.ok && response.type === 'basic')
        await cache.put(key, response.clone());
      return response;
    } catch (_) {
      const cached = await cache.match(key);
      if (cached) return cached;
      throw new Error('Offline');
    }
  })());
});
