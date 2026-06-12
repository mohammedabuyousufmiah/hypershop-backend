/// <reference types="node" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
/**
 * Hypershop-integrated Vite config (2026-05-13).
 *
 * Differences from the standalone CC build:
 *  - `base: "/customercare/"` so all asset URLs (JS/CSS/images) emit
 *    with the correct prefix when the PWA is served at the Hypershop
 *    /customercare path.
 *  - Dev proxy rewrites `/api/customer-care/*` and `/api/v1/customer-care/*`
 *    to the Hypershop backend. The CC-original `/api/*` paths still
 *    proxy too so legacy fetches don't 404 during the migration.
 */
export default defineConfig(function (_a) {
    var mode = _a.mode;
    var env = loadEnv(mode, process.cwd(), "");
    var backend = env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
    return {
        base: "/customercare/",
        plugins: [react()],
        server: {
            port: 5173,
            proxy: {
                "/api": {
                    target: backend,
                    changeOrigin: true,
                },
            },
        },
        build: {
            outDir: "dist",
            sourcemap: true,
            rollupOptions: {
                output: {
                    manualChunks: {
                        react: ["react", "react-dom", "react-router-dom"],
                        data: ["swr", "zustand"],
                    },
                },
            },
        },
    };
});
