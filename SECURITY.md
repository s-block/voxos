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
