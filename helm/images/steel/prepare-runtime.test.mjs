import assert from "node:assert/strict";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { prepareRuntime } from "./prepare-runtime.mjs";

test("runtime includes service assets and licenses without captured pages or test keys", () => {
  const root = mkdtempSync(join(tmpdir(), "steel-runtime-"));
  try {
    const build = join(root, "build");
    const runtime = join(root, "runtime");
    const files = [
      "package.json", "package-lock.json", "LICENSE", "api/package.json",
      "api/build/index.js", "api/build/templates/page.ejs",
      "api/extensions/recorder/dist/inject.js", "api/extensions/recorder/manifest.json",
      "api/extensions/recorder/icon.png", "api/selenium/driver/LICENSE.chromedriver",
      "node_modules/dependency/index.js", "node_modules/dependency/LICENSE",
      "node_modules/dependency/test/fixtures/server.key",
      "api/src/utils/scrape/__tests__/fixtures/article.html.gz",
      "api/build/utils/scrape/__tests__/fixture.js",
      "api/node_modules/nested/index.js", "api/node_modules/nested/tests/fixture.key",
    ];
    for (const file of files) {
      const path = join(build, file);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, file);
    }
    mkdirSync(join(build, "node_modules/@steel-browser"));
    symlinkSync("../../api", join(build, "node_modules/@steel-browser/api"));
    prepareRuntime(build, runtime);
    for (const file of ["api/build/index.js", "api/build/templates/page.ejs", "LICENSE",
      "node_modules/dependency/LICENSE", "api/extensions/recorder/dist/inject.js",
      "api/selenium/driver/LICENSE.chromedriver", "api/node_modules/nested/index.js"]) {
      assert.equal(readFileSync(join(runtime, file), "utf8"), file);
    }
    for (const file of ["api/src", "api/build/utils/scrape/__tests__",
      "node_modules/dependency/test", "api/node_modules/nested/tests"]) {
      assert.equal(existsSync(join(runtime, file)), false);
    }
    assert.equal(lstatSync(join(runtime, "node_modules/@steel-browser/api")).isSymbolicLink(), true);
    assert.throws(() => prepareRuntime(build, runtime), { code: "EEXIST" });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
