# Security policy

Security fixes target the current `main` branch. Use current release images and
locked dependencies, and keep installation credentials in ignored local files
or deployment-managed secrets.

## Report a vulnerability

Report suspected vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/s-block/voxos/security/advisories/new).
Include the affected revision, configuration, impact, and minimal reproduction.
Use synthetic identities and data, and redact credentials and customer content.

For an exposed credential, revoke or rotate it with its provider and identify
the affected commits, logs, or package versions in the private report.

## Validate changes

Run `make publication-check` and `make pre-commit` before submitting source
changes. CI scans complete Git history with Gitleaks and audits the locked
Python and JavaScript dependencies. Scanner exemptions identify specific
synthetic fixtures or integrity hashes; review changes to them as security
changes.

The Bandit, pip-audit, Semgrep and uv CI toolchain is locked separately in
[`.github/security/requirements.txt`](.github/security/requirements.txt), including
transitive dependencies and distribution hashes. CI installs prebuilt wheels
with hash verification. Run `make security-lock` to refresh this Python 3.12,
Ubuntu 24.04 x86_64 environment with the seven-day package cooldown, then review the
lockfile diff and run the scanners. Vulnerability advisories and Semgrep's
`p/ci` rules are fetched when the scans run.

Semgrep 1.182.1 pins MCP 1.29 and OpenTelemetry 1.37. Their advisories
[CVE-2026-59951](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-84m7-p3x7-pcfv),
[CVE-2026-116684](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-w4fh-qvv9-3v23),
[CVE-2026-116685](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-5h93-6whr-6q8j),
[CVE-2026-116686](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-rwrf-2pqf-9j8j)
and [CVE-2026-114603](https://github.com/open-telemetry/opentelemetry-python/security/advisories/GHSA-xcq7-p3pf-p88h)
affect MCP client/server operations and inbound trace-header parsing. The CI
scanner runs `semgrep scan`, without an MCP client/server or inbound HTTP
listener. These dependencies belong only to the CI tool environment. Review
this assessment when changing the scanner command or updating its dependencies,
and remove it when upstream releases compatible fixes. Application dependency
audits retain their existing checks.
