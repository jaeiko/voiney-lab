/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The new screen lives under the server's static folder at /app/ (lane RX0,
// 2026-10-24): the build goes to src/voiney_lab/static/app/ (git-ignored) and
// server.py serves it; every asset URL starts with /app/ so the old screen's
// files beside it are untouched. Fonts (Pretendard, OFL) are bundled here and
// served by the same server -- no outside CDN.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: "../src/voiney_lab/static/app",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000", "/icons": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
