# Codex cloud environment

Create the Codex cloud environment for `s-block/voxos` with the default
`universal` image and these settings:

- Python: `3.12`
- uv: `0.10.4` (installed by the setup script)
- Node.js: `22`
- Setup script: `bash .codex/setup.sh`
- Maintenance script: `bash .codex/setup.sh`
- Environment variables and secrets: none required for the standard local
  validation suites

The script installs the locked Python environment, pre-commit hooks, every
Node.js workspace, Terraform 1.15.8, Flutter 3.44.8, and the locked Flutter
package graph while setup-phase internet access is available. Normal unit,
lint, type, docs, frontend, bridge, and Flutter checks can keep agent internet
access disabled. Provider, AWS, Firebase, live database, and deployment work
requires the corresponding task-specific network policy and credentials; never
add those values to the repository.

See the [Codex cloud environment documentation](https://developers.openai.com/codex/cloud/environments)
for the environment lifecycle and cache behavior.
