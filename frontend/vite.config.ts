import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  envPrefix: mode === "production" ? [] : "VITE_",
  // Portable release assets never incorporate workstation deployment settings.
  define: mode === "production"
    ? {
      "import.meta.env.VITE_API_BASE_URL": "undefined",
      "import.meta.env.VITE_LOGODEV_PUBLISHABLE_KEY": "undefined",
      "import.meta.env.VITE_SENTRY_DSN": "undefined",
    }
    : {},
}));
