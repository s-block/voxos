import eslint from "@eslint/js";
import { defineConfig } from "eslint/config";
import { URL, fileURLToPath } from "node:url";
import tseslint from "typescript-eslint";

const rootDirectory = fileURLToPath(new URL(".", import.meta.url));

const typescriptConfigs = [
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
].map((config) => ({
  ...config,
  files: ["src/**/*.ts"],
}));

export default defineConfig([
  {
    ignores: [".test-dist/**", "dist/**", "node_modules/**"],
  },
  eslint.configs.recommended,
  ...typescriptConfigs,
  {
    files: ["src/**/*.ts"],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: rootDirectory,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-confusing-void-expression": "off",
      "@typescript-eslint/no-magic-numbers": "off",
      "@typescript-eslint/no-unnecessary-type-parameters": "off"
    },
  },
  {
    files: ["src/**/*.test.ts"],
    rules: {
      "@typescript-eslint/no-floating-promises": "off",
      "@typescript-eslint/require-await": "off",
    },
  },
]);
