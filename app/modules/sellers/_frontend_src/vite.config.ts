/// <reference types="node" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Hypershop seller frontend (Sprint 14).
 *
 * Served by the backend at `/seller/` once `pnpm build` emits `dist/`.
 * Vite `base` set to `/seller/` so asset URLs (JS/CSS) resolve when
 * the SPA is hosted behind the path prefix.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
  return {
    base: "/seller/",
    plugins: [react()],
    server: {
      port: 5174,
      proxy: {
        "/api": { target: backend, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            data: ["swr"],
          },
        },
      },
    },
  };
});
