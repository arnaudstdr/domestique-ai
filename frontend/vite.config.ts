import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import pkg from "./package.json" with { type: "json" };

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [
    react(),
    VitePWA({
      // autoUpdate : le SW prend la main dès qu'une nouvelle version est
      // activée (skipWaiting + clientsClaim) et le client recharge l'onglet.
      // `onNeedReload` (voir src/main.tsx) diffère le reload si l'onglet est
      // visible, pour ne pas couper une saisie en cours.
      registerType: "autoUpdate",
      injectRegister: "auto",
      includeAssets: ["favicon.svg", "icon-24.png", "icon-48.png"],
      manifest: {
        name: "DomestiqueAI",
        short_name: "DomestiqueAI",
        description: "Assistant d'entraînement cycliste",
        start_url: "/",
        display: "standalone",
        theme_color: "#0c0d10",
        background_color: "#0c0d10",
        icons: [
          { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
        ],
      },
      workbox: {
        // App shell : toutes les navigations retombent sur index.html (SPA),
        // sauf l'API. Le precache est révisionné automatiquement par le build.
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
        globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
        // Whitelist stricte : seuls ces endpoints idempotents et non sensibles
        // sont mis en cache. Tout le reste — /api/coach/*, /api/morning,
        // /api/objective, /api/profile, /api/availability, auth — n'est
        // intercepté par aucune route et part directement sur le réseau.
        runtimeCaching: [
          {
            urlPattern: ({ url }) =>
              url.pathname === "/api/metrics" || url.pathname.startsWith("/api/metrics/"),
            handler: "NetworkFirst",
            options: {
              cacheName: "api-metrics",
              networkTimeoutSeconds: 3,
              cacheableResponse: { statuses: [200] },
              expiration: { maxEntries: 50, maxAgeSeconds: 60 * 60 * 24 },
            },
          },
          {
            urlPattern: ({ url }) =>
              url.pathname === "/api/activities" ||
              /^\/api\/activities\/\d+$/.test(url.pathname),
            handler: "NetworkFirst",
            options: {
              cacheName: "api-activities",
              networkTimeoutSeconds: 3,
              cacheableResponse: { statuses: [200] },
              expiration: { maxEntries: 60, maxAgeSeconds: 60 * 60 * 24 },
            },
          },
        ],
      },
      // Pas de service worker en dev : Vite sert les modules à la volée.
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8501",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
