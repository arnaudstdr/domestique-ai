/*
 * Service worker — domestique-ai.
 *
 * Stratégie /api :
 *  - Whitelist explicite des endpoints idempotents et non-sensibles
 *    (CR-018). Tout le reste — y compris /api/coach/*, /api/morning,
 *    /api/objective, /api/profile, /api/availability — passe en
 *    network-only sans mise en cache.
 *  - NetworkFirst sur la whitelist avec timeout 3 s ; si le réseau est
 *    lent ou indisponible, on retombe sur la version en cache.
 *  - Bump des noms de cache à chaque release pour invalider les anciens
 *    contenus de l'API.
 *
 * Stratégie statique : CacheFirst sur les assets de la PWA (/, /assets/*,
 * /manifest.json, /favicon.svg).
 */

const STATIC_CACHE = "domestique-static-v6";
const API_CACHE = "domestique-api-v6";

// Endpoints `/api/*` autorisés à être mis en cache (lecture historique sans
// donnée personnelle sensible). Tout endpoint absent de cette liste passe
// en network-only.
const CACHEABLE_API_PATTERNS = [
  /^\/api\/metrics(\/|$)/,
  /^\/api\/activities(\/\d+)?$/,
];

const NETWORK_TIMEOUT_MS = 3000;

function networkWithTimeout(request) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("network timeout")),
      NETWORK_TIMEOUT_MS,
    );
    fetch(request).then(
      (res) => {
        clearTimeout(timer);
        resolve(res);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

function isCacheableApi(pathname) {
  return CACHEABLE_API_PATTERNS.some((re) => re.test(pathname));
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(["/", "/manifest.json", "/favicon.svg"]))
      .catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== API_CACHE)
          .map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.pathname.startsWith("/api/")) {
    // Endpoints sensibles ou non idempotents : network-only, jamais
    // de cache (évite de servir des données personnelles obsolètes
    // ou de cacher un coach/today calculé via LLM).
    if (!isCacheableApi(url.pathname)) {
      event.respondWith(fetch(request));
      return;
    }

    // NetworkFirst avec timeout pour les endpoints idempotents
    // listés dans la whitelist.
    event.respondWith(
      networkWithTimeout(request)
        .then((res) => {
          // Ne cacher que les réponses OK pour éviter de figer une 5xx.
          if (res && res.ok) {
            const clone = res.clone();
            caches
              .open(API_CACHE)
              .then((cache) => cache.put(request, clone))
              .catch(() => undefined);
          }
          return res;
        })
        .catch(() =>
          caches.match(request).then((cached) =>
            cached ||
            new Response(
              JSON.stringify({ error: "offline" }),
              {
                status: 503,
                headers: { "Content-Type": "application/json" },
              },
            ),
          ),
        ),
    );
    return;
  }

  // CacheFirst pour les assets statiques.
  event.respondWith(
    caches.match(request).then(
      (cached) =>
        cached ||
        fetch(request).then((res) => {
          const clone = res.clone();
          caches
            .open(STATIC_CACHE)
            .then((cache) => cache.put(request, clone))
            .catch(() => undefined);
          return res;
        }),
    ),
  );
});
