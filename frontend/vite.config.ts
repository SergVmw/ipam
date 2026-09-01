import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// OUT_DIR=../static — собирать сразу в каталог, который раздаёт backend
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: process.env.OUT_DIR || "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
