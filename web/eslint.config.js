import js from "@eslint/js";
import astro from "eslint-plugin-astro";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

export default defineConfig([
  { ignores: ["dist", ".astro"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  astro.configs.recommended,
  {
    // CloudFront Functions are classic scripts whose runtime calls `handler`.
    files: ["infra/**/*.js"],
    languageOptions: { sourceType: "script" },
  },
]);
