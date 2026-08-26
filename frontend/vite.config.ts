import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // `netpulse serve` sobe a API em 127.0.0.1:8000 (ver README). O proxy
    // evita configurar VITE_API_URL só para desenvolver localmente — em
    // produção o build é servido pela própria API, mesma origem.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
