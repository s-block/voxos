import { cpSync, existsSync, mkdirSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const testDirectories = new Set(["test", "tests", "__tests__"]);

/** Copy the compiled service into a new directory, retaining dependency notices. */
export function prepareRuntime(sourceDirectory, destinationDirectory) {
  mkdirSync(destinationDirectory);
  const required = [
    "package.json",
    "package-lock.json",
    "LICENSE",
    "node_modules",
    "api/package.json",
    "api/build",
    "api/extensions/recorder/dist",
    "api/extensions/recorder/manifest.json",
    "api/extensions/recorder/icon.png",
    "api/selenium",
  ];
  const optional = ["api/node_modules"];
  for (const path of [...required, ...optional.filter((path) => existsSync(join(sourceDirectory, path)))]) {
    const destination = join(destinationDirectory, path);
    mkdirSync(resolve(destination, ".."), { recursive: true });
    cpSync(join(sourceDirectory, path), destination, {
      recursive: true,
      verbatimSymlinks: true,
      filter: (source) => !testDirectories.has(basename(source)),
    });
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [sourceDirectory, destinationDirectory] = process.argv.slice(2);
  if (!sourceDirectory || !destinationDirectory) {
    throw new Error("Pass the build directory and a new runtime directory");
  }
  prepareRuntime(sourceDirectory, destinationDirectory);
}
