import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "reader",
  plugins: [react()],
  build: { outDir: "../reader-dist", emptyOutDir: true },
});
