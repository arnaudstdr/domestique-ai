// Thème partagé pour les graphiques recharts.
//
// recharts ne lit pas les variables CSS (les SVG sont rendus hors flux Tailwind),
// on duplique donc ici les valeurs de la direction artistique définie dans
// src/index.css. Source de vérité unique côté charts : tout passe par ce module
// pour rester aligné sur les tokens (accent lime, CTL azur, ATL corail, TSB
// émeraude) et éviter les couleurs en dur dispersées.

export const CHART = {
  accent: "#c7f24a", // vert acide signal
  ctl: "#4aa8ff", // forme — azur
  atl: "#ff5d5d", // fatigue — corail
  tsb: "#34d399", // fraîcheur — émeraude
  tsbNeg: "#f5a524", // ambre
  muted: "#8e96a4", // axes / texte secondaire
  grid: "rgba(255,255,255,0.06)", // grille discrète
} as const;

// Zones HR : dégradé froid → chaud, distinct de l'accent lime.
export const ZONE_COLORS: Record<string, string> = {
  z1: "#4aa8ff", // récup — azur
  z2: "#34d399", // endurance — émeraude
  z3: "#eab308", // tempo — or
  z4: "#fb923c", // seuil — orange
  z5: "#ff5d5d", // VO2max — corail
};

// Styles réutilisables passés tels quels aux primitives recharts.
export const tooltipStyle = {
  backgroundColor: "#15171c",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 12,
  color: "#edeff3",
  boxShadow: "0 8px 24px -12px rgba(0,0,0,0.7)",
  fontSize: 12,
} as const;

export const legendStyle = { fontSize: 12, color: CHART.muted } as const;

// Props communs aux axes (police mono pour des graduations « instrument »).
export const axisProps = {
  stroke: CHART.muted,
  fontSize: 11,
  tick: { fontFamily: '"Geist Mono", ui-monospace, monospace' },
} as const;
