/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#1a1d23",
        card: "#23272f",
        cardHover: "#2c313a",
        muted: "#9aa3af",
        accent: "#f97316",
        ctl: "#3b82f6",
        atl: "#ef4444",
        tsb: "#22c55e",
        tsb_neg: "#f97316",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
