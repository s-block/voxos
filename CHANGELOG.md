# Changelog

All notable changes to this project will be documented in this file.

This changelog follows the Keep a Changelog format. Add new entries under
`Unreleased`, then move them into a dated version section when cutting a release.

## [Unreleased]

### Added

- Added the project changelog.
- Added weekly Dependabot updates for Python, frontend, GitHub Actions, Docker,
  Docker Compose, and pre-commit dependencies.

### Changed

- Reworked the mobile app into a single conversation screen: chat history moved
  into a sliding sidebar with search and date grouping, and a voice call is one
  tap from the composer instead of a separate tab.
- Updated the mobile voice call to check readiness, connect, and open the
  microphone in one step, and to end the call when the screen is dismissed.
- Updated CI to run format, lint, type checks, coverage, security scans, and
  container image scanning before publishing GHCR images.
- Updated ECS deployment workflows to deploy verified GHCR image tags for dev
  and production.
- Updated project agent guidance for the current Voxos API, authentication,
  credential storage, text chat, voice, MCP, and agent data boundaries.

### Fixed

- Fixed CI handling for optional Codecov and Safety credentials, Bandit scan
  thresholds, pytest import mode, and the patched `msgpack` dependency
  constraint.

### Removed

- Removed obsolete prompt planning documents from the repository.
