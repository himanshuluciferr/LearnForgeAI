import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The built app is served by FastAPI from backend/static, so there is one origin, one url
  // and no CORS.
  build: { outDir: "../backend/static", emptyOutDir: true },
  server: {
    // In development the app runs on 5173 and the API on 8000. Proxying keeps the paths
    // identical to production, so no code has to know which it is running under.
    proxy: {
      "/auth": "http://127.0.0.1:8000",
      "/courses": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
      "/quiz": "http://127.0.0.1:8000",
      "/progress": "http://127.0.0.1:8000",
      "/mentor": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // The default forks pool times out waiting for a worker on Windows.
    pool: "threads",
  },
});
