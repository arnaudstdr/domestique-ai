// Thème partagé pour les graphiques recharts.
//
// recharts ne lit pas les variables CSS dans les attributs SVG (les SVG sont
// rendus hors flux Tailwind) et les couleurs doivent suivre le thème
// clair/sombre. On lit donc les tokens de la direction artistique (src/index.css)
// à la volée via `getComputedStyle` : les valeurs exportées ici sont des
// accesseurs, pas des constantes figées. Toute couleur reste centralisée dans ce
// module (accent lime, CTL azur, ATL corail, TSB émeraude).
//
// NB : tooltip et legend sont rendus en HTML — leurs styles inline sont relus à
// chaque affichage, donc ils suivent le thème sans re-render du graphique.

import type { CSSProperties } from "react";

type Theme = "light" | "dark";

export function isDarkTheme(): boolean {
  if (typeof document === "undefined") return true;
  return document.documentElement.classList.contains("dark");
}

// Lit un token (stocké en canaux RGB dans index.css) et renvoie une couleur CSS.
export function themeColor(varName: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return fallback;
  }
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(varName)
    .trim();
  return raw ? `rgb(${raw})` : fallback;
}

// Liseré / remplissage neutre qui reste visible dans les deux thèmes
// (blanc translucide en sombre, encre translucide en clair).
export function hairline(alpha: number): string {
  return isDarkTheme()
    ? `rgba(255,255,255,${alpha})`
    : `rgba(15,18,24,${alpha})`;
}

const GRID: Record<Theme, string> = {
  dark: "rgba(255,255,255,0.06)", // grille discrète sur fond sombre
  light: "rgba(15,18,24,0.08)", // grille discrète sur fond clair
};

const TOOLTIP_BORDER: Record<Theme, string> = {
  dark: "rgba(255,255,255,0.08)",
  light: "rgba(15,18,24,0.10)",
};

// Zones HR : dégradé froid → chaud, distinct de l'accent. Variantes plus
// soutenues en thème clair pour rester lisibles sur fond blanc.
const ZONE_COLORS_DARK: Record<string, string> = {
  z1: "#4aa8ff", // récup — azur
  z2: "#34d399", // endurance — émeraude
  z3: "#eab308", // tempo — or
  z4: "#fb923c", // seuil — orange
  z5: "#ff5d5d", // VO2max — corail
};

const ZONE_COLORS_LIGHT: Record<string, string> = {
  z1: "#2563eb",
  z2: "#059669",
  z3: "#ca8a04",
  z4: "#ea580c",
  z5: "#dc2626",
};

interface Palette {
  accent: string;
  ctl: string;
  atl: string;
  tsb: string;
  tsbNeg: string;
  muted: string;
  grid: string;
}

// Un getter par propriété → les tokens sont relus à chaque accès (rendu).
export const CHART = {} as Palette;
Object.defineProperties(CHART, {
  accent: { enumerable: true, get: () => themeColor("--accent", "#c7f24a") },
  ctl: { enumerable: true, get: () => themeColor("--ctl", "#4aa8ff") },
  atl: { enumerable: true, get: () => themeColor("--atl", "#ff5d5d") },
  tsb: { enumerable: true, get: () => themeColor("--tsb", "#34d399") },
  tsbNeg: { enumerable: true, get: () => themeColor("--tsb-neg", "#f5a524") },
  muted: { enumerable: true, get: () => themeColor("--muted", "#8e96a4") },
  grid: { enumerable: true, get: () => GRID[isDarkTheme() ? "dark" : "light"] },
});

export const ZONE_COLORS: Record<string, string> = new Proxy(
  {} as Record<string, string>,
  {
    get: (_target, key) =>
      (isDarkTheme() ? ZONE_COLORS_DARK : ZONE_COLORS_LIGHT)[String(key)],
  },
);

// Styles réutilisables passés tels quels aux primitives recharts.
export const tooltipStyle = {} as CSSProperties;
Object.defineProperties(tooltipStyle, {
  backgroundColor: {
    enumerable: true,
    get: () => themeColor("--card", "#15171c"),
  },
  border: {
    enumerable: true,
    get: () => `1px solid ${TOOLTIP_BORDER[isDarkTheme() ? "dark" : "light"]}`,
  },
  borderRadius: { enumerable: true, value: 12 },
  color: { enumerable: true, get: () => themeColor("--fg", "#edeff3") },
  boxShadow: { enumerable: true, value: "0 8px 24px -12px rgba(0,0,0,0.7)" },
  fontSize: { enumerable: true, value: 12 },
});

// Couleur du texte des items du tooltip. Indispensable pour les <Bar> dont la
// couleur est définie par cellule (fill dans les données) et non via la prop
// `fill` : recharts retombe sinon sur un item noir, illisible sur fond sombre.
export const tooltipItemStyle = {} as CSSProperties;
Object.defineProperties(tooltipItemStyle, {
  color: { enumerable: true, get: () => themeColor("--fg", "#edeff3") },
});

export const legendStyle = {} as CSSProperties;
Object.defineProperties(legendStyle, {
  fontSize: { enumerable: true, value: 12 },
  color: { enumerable: true, get: () => CHART.muted },
});

// Props communs aux axes (police mono pour des graduations « instrument »).
export const axisProps = {} as {
  stroke: string;
  fontSize: number;
  tick: { fontFamily: string };
};
Object.defineProperties(axisProps, {
  stroke: { enumerable: true, get: () => CHART.muted },
  fontSize: { enumerable: true, value: 11 },
  tick: {
    enumerable: true,
    value: { fontFamily: '"Geist Mono", ui-monospace, monospace' },
  },
});
