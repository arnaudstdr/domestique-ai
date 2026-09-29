import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { registerSW } from "virtual:pwa-register";
import App from "./App";
import { ToastProvider } from "./hooks/useToast";
import { ThemeProvider } from "./hooks/useTheme";
import "./index.css";
import "leaflet/dist/leaflet.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>
);

// Mise à jour auto (registerType: "autoUpdate") : le service worker activé
// recharge la page. On diffère le reload tant que l'onglet est visible, pour
// ne pas interrompre une saisie en cours : il est appliqué au prochain passage
// en arrière-plan.
let pendingReload = false;

if ("serviceWorker" in navigator) {
  registerSW({
    immediate: true,
    onNeedReload() {
      if (document.visibilityState === "hidden") {
        window.location.reload();
      } else {
        pendingReload = true;
      }
    },
  });

  document.addEventListener("visibilitychange", () => {
    if (pendingReload && document.visibilityState === "hidden") {
      window.location.reload();
    }
  });
}
