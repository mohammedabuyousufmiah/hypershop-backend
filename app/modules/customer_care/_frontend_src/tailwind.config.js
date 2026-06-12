/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  "#f0f7ff",
          100: "#dceaff",
          500: "#2f6feb",
          600: "#2456c7",
          700: "#1c449a",
        },
        sla: {
          ok:    "#10b981",
          warn:  "#f59e0b",
          breach:"#ef4444",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        bangla: ["'Noto Sans Bengali'", "Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
