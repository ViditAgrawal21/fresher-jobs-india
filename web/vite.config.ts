import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // FastAPI runs on :8000 in dev (uvicorn api.main:app --reload)
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
