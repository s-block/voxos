import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const exceptionsFile = new URL("./npm-audit-exceptions.json", import.meta.url);

/** Map each advisory ID in an `npm audit --json` report to its package, severity and title. */
export function advisories(report) {
  const found = new Map();
  for (const vulnerability of Object.values(report.vulnerabilities ?? {})) {
    for (const via of vulnerability.via) {
      // String entries name an affected dependency; objects are the advisories.
      if (typeof via === "object") {
        const id = via.url.split("/").pop();
        found.set(id, { name: via.name, severity: via.severity, title: via.title });
      }
    }
  }
  return found;
}

/** Split advisories into failures and reviewed exceptions that have not expired. */
export function checkAdvisories(found, exceptions, today) {
  const failures = [];
  const excepted = [];
  for (const [id, { name, severity, title }] of [...found].sort()) {
    const description = `${id} (${name}, ${severity}): ${title}`;
    const exception = exceptions[id];
    if (!exception) {
      failures.push(`${description} has no reviewed exception`);
    } else if (exception.expires < today) {
      failures.push(`${description} has an exception that expired on ${exception.expires}`);
    } else {
      excepted.push(`${description} is excepted until ${exception.expires}`);
    }
  }
  return { failures, excepted };
}

function auditProject(project) {
  const result = spawnSync("npm", ["audit", "--package-lock-only", "--json", "--prefix", project], {
    encoding: "utf8",
  });
  if (result.error) {
    throw result.error;
  }
  const report = JSON.parse(result.stdout);
  if (report.error) {
    throw new Error(`npm audit could not check ${project}: ${report.error.summary}`);
  }
  return report;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const projects = process.argv.slice(2);
  if (projects.length === 0) {
    throw new Error("Pass the directories of the npm projects to audit");
  }
  const exceptions = JSON.parse(readFileSync(exceptionsFile, "utf8"));
  const today = new Date().toISOString().slice(0, 10);
  for (const project of projects) {
    const { failures, excepted } = checkAdvisories(advisories(auditProject(project)), exceptions, today);
    for (const line of excepted) {
      console.log(`${project}: ${line}`);
    }
    for (const line of failures) {
      console.error(`${project}: ${line}`);
    }
    if (failures.length > 0) {
      process.exitCode = 1;
    } else {
      console.log(`${project}: no unexcepted advisories`);
    }
  }
}
