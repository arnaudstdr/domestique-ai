import typography from "@tailwindcss/typography";
import defaultColors from "tailwindcss/colors";

// Nuances « statut » utilisées dans l'UI : en thème clair, les tons clairs
// (—300/—400) illisibles sur blanc sont assombris. Les valeurs sont pilotées
// par des variables CSS (voir index.css, `:root` = clair / `.dark` = sombre),
// les autres nuances de chaque palette restant les valeurs Tailwind par défaut.
const themed = (...tokens) =>
  Object.fromEntries(
    tokens.map((token) => [
      token.split("-").pop(),
      `rgb(var(--${token}) / <alpha-value>)`,
    ]),
  );

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Le thème est piloté par une classe `dark` posée sur <html> (light/dark/auto,
  // résolu en amont par le script anti-flash et `hooks/useTheme.tsx`).
  darkMode: "class",
  theme: {
    extend: {
      // Les couleurs pointent vers des variables CSS (canaux RGB) définies
      // dans src/index.css. Cela permet de garder les *noms* de tokens
      // historiques (bg-card, text-accent…) tout en pilotant la direction
      // artistique depuis un seul endroit — et autorise les modificateurs
      // d'alpha Tailwind (bg-card/95, text-accent/70…).
      colors: {
        surface: "rgb(var(--surface) / <alpha-value>)",
        card: "rgb(var(--card) / <alpha-value>)",
        cardHover: "rgb(var(--card-hover) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-ink": "rgb(var(--accent-ink) / <alpha-value>)",
        ctl: "rgb(var(--ctl) / <alpha-value>)",
        atl: "rgb(var(--atl) / <alpha-value>)",
        tsb: "rgb(var(--tsb) / <alpha-value>)",
        tsb_neg: "rgb(var(--tsb-neg) / <alpha-value>)",
        // Tokens sémantiques (bascule clair/sombre) :
        fg: "rgb(var(--fg) / <alpha-value>)", // texte principal
        "fg-soft": "rgb(var(--fg-soft) / <alpha-value>)", // texte secondaire
        border: "rgb(var(--border) / <alpha-value>)", // liserés translucides
        overlay: "rgb(var(--overlay) / <alpha-value>)", // surfaces surélevées
        sunken: "rgb(var(--sunken) / <alpha-value>)", // creux (inputs/selects)
        // Nuances « statut » retravaillées pour le thème clair (voir `themed`).
        red: { ...defaultColors.red, ...themed("red-300", "red-400", "red-500") },
        orange: { ...defaultColors.orange, ...themed("orange-300") },
        amber: {
          ...defaultColors.amber,
          ...themed("amber-300", "amber-400", "amber-500"),
        },
        yellow: { ...defaultColors.yellow, ...themed("yellow-200", "yellow-300") },
        emerald: {
          ...defaultColors.emerald,
          ...themed("emerald-300", "emerald-500"),
        },
        green: { ...defaultColors.green, ...themed("green-300", "green-400") },
        blue: { ...defaultColors.blue, ...themed("blue-300") },
        rose: { ...defaultColors.rose, ...themed("rose-400") },
        sky: { ...defaultColors.sky, ...themed("sky-400") },
        violet: { ...defaultColors.violet, ...themed("violet-400") },
      },
      fontFamily: {
        display: ['"Bricolage Grotesque"', "Hanken Grotesk", "sans-serif"],
        sans: [
          '"Hanken Grotesk"',
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ['"Geist Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        // Ombre douce et déposée des cartes (pas le shadow-sm plat par défaut),
        // avec un liseré clair en haut pour simuler la matière (verre/métal).
        card: "inset 0 1px 0 0 rgb(255 255 255 / 0.05), 0 8px 24px -12px rgb(0 0 0 / 0.7)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.35), 0 6px 26px -8px rgb(var(--accent) / 0.45)",
      },
      keyframes: {
        rise: {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "none" },
        },
        "coach-halo": {
          "0%, 100%": { opacity: "0.35", transform: "scale(1)" },
          "50%": { opacity: "0.6", transform: "scale(1.12)" },
        },
        "alert-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
      },
      animation: {
        rise: "rise 0.5s cubic-bezier(0.22, 1, 0.36, 1) both",
        "coach-halo": "coach-halo 3.2s ease-in-out infinite",
        "alert-pulse": "alert-pulse 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [typography],
};
