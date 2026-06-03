import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Les couleurs pointent vers des variables CSS (canaux RGB) définies
      // dans src/index.css. Cela permet de garder les *noms* de tokens
      // historiques (bg-card, text-accent, border-white/5…) tout en pilotant
      // la direction artistique depuis un seul endroit — et autorise les
      // modificateurs d'alpha Tailwind (bg-card/95, text-accent/70…).
      colors: {
        surface: "rgb(var(--surface) / <alpha-value>)",
        card: "rgb(var(--card) / <alpha-value>)",
        cardHover: "rgb(var(--card-hover) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        ctl: "rgb(var(--ctl) / <alpha-value>)",
        atl: "rgb(var(--atl) / <alpha-value>)",
        tsb: "rgb(var(--tsb) / <alpha-value>)",
        tsb_neg: "rgb(var(--tsb-neg) / <alpha-value>)",
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
      },
      animation: {
        rise: "rise 0.5s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [typography],
};
