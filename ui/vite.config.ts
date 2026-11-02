import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the UI on :5173 and forwards /api to the running API container.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8300" } },
});
