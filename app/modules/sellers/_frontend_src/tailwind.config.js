/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f5ff",
          100: "#dbe7ff",
          500: "#2f6feb",
          600: "#1e5acb",
          700: "#1a47a8",
        },
      },
      fontFamily: {
        sans: ["Inter", "Noto Sans Bengali", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
