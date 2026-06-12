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
declare const _default: import("vite").UserConfigFnObject;
export default _default;
