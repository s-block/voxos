import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { advisories, checkAdvisories, trivyIgnore } from "./npm-audit.mjs";

const report = {
  vulnerabilities: {
    minimatch: {
      via: [
        {
          url: "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
          name: "minimatch",
          severity: "moderate",
          title: "Pattern denial of service",
        },
      ],
    },
    glob: {
      via: [
        "minimatch",
        {
          url: "https://github.com/advisories/GHSA-dddd-eeee-ffff",
          name: "glob",
          severity: "high",
          title: "Traversal after timeout",
        },
      ],
    },
    eslint: { via: ["minimatch"] },
  },
};

test("collects each advisory once and skips dependency names", () => {
  assert.deepEqual([...advisories(report).keys()].sort(), ["GHSA-aaaa-bbbb-cccc", "GHSA-dddd-eeee-ffff"]);
  assert.equal(advisories({ vulnerabilities: {} }).size, 0);
});

test("fails advisories without a reviewed exception", () => {
  const exceptions = { "GHSA-aaaa-bbbb-cccc": { expires: "2026-11-14" } };
  const { failures, excepted } = checkAdvisories(advisories(report), exceptions, "2026-11-01");
  assert.deepEqual(failures, [
    "GHSA-dddd-eeee-ffff (glob, high): Traversal after timeout has no reviewed exception",
  ]);
  assert.deepEqual(excepted, [
    "GHSA-aaaa-bbbb-cccc (minimatch, moderate): Pattern denial of service is excepted until 2026-11-14",
  ]);
});

test("accepts an exception through its expiry date and fails it afterwards", () => {
  const found = advisories(report);
  const exceptions = {
    "GHSA-aaaa-bbbb-cccc": { expires: "2026-11-14" },
    "GHSA-dddd-eeee-ffff": { expires: "2026-11-14" },
  };
  assert.deepEqual(checkAdvisories(found, exceptions, "2026-11-14").failures, []);
  assert.deepEqual(checkAdvisories(found, exceptions, "2026-11-15").failures, [
    "GHSA-aaaa-bbbb-cccc (minimatch, moderate): Pattern denial of service has an exception that expired on 2026-11-14",
    "GHSA-dddd-eeee-ffff (glob, high): Traversal after timeout has an exception that expired on 2026-11-14",
  ]);
});

test("each reviewed exception names its package, reason and expiry date", () => {
  const exceptions = JSON.parse(readFileSync(new URL("./npm-audit-exceptions.json", import.meta.url), "utf8"));
  for (const [id, exception] of Object.entries(exceptions)) {
    assert.match(id, /^GHSA(-[0-9a-z]{4}){3}$/);
    const { cve, ...required } = exception;
    assert.deepEqual(Object.keys(required).sort(), ["expires", "package", "reason"]);
    if (cve !== undefined) {
      assert.match(cve, /^CVE-\d{4}-\d{4,}$/);
    }
    assert.match(exception.expires, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(exception.package.length > 0 && exception.reason.length > 0);
  }
});

test("Trivy ignores each advisory by CVE, or by GHSA without one, through its expiry date", () => {
  const exceptions = {
    "GHSA-aaaa-bbbb-cccc": { package: "minimatch", cve: "CVE-2026-1234", expires: "2026-11-14", reason: "r" },
    "GHSA-dddd-eeee-ffff": { package: "glob", expires: "2026-12-31", reason: "r" },
  };
  assert.equal(
    trivyIgnore(exceptions),
    [
      "# Generated from scripts/npm-audit-exceptions.json by scripts/npm-audit.mjs.",
      "CVE-2026-1234 exp:2026-11-15",
      "GHSA-dddd-eeee-ffff exp:2027-01-01",
      "",
    ].join("\n"),
  );
});
