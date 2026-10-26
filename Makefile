.PHONY: help install install-dev pre-commit-install pre-commit secrets secrets-all secrets-baseline lint format type-check mypy frontend-lint whatsapp-bridge-install whatsapp-bridge-build whatsapp-bridge-check codex-runner-build docs-validate docs-manifest docs-index docs-refresh-check example-skills-list example-skills-load db-enable-vector up restart compose-up compose-restart mcp-stack-up mcp-stack-down mcp-stack-logs mcp-stack-config monzo-mcp-restart monzo-mcp-package-check test test-cov test-bills test-bills-inconclusive-artifacts clean build build-langgraph run ngrok worker worker-runs worker-codex worker-whatsapp scheduler frontend app-get app-devices app-format app-analyze app-test run-ios run-ios-device deploy-ios-device build-prod-ipa deploy-testflight app-run-ios app-run-android app-build-ios app-build-android app-open-ios
.DEFAULT_GOAL := help
.PHONY: helm-check publication-check

-include app/.env

# Optional installation-owned Sentry DSN, applied to mobile run/build targets.
APP_SENTRY_ARGS = $(if $(wildcard app/sentry.config.json),--dart-define-from-file=sentry.config.json,)


# Colors for output
BLUE := \033[36m
RESET := \033[0m

help: ## Show this help message
	@echo "$(BLUE)voxos Service - Available Commands:$(RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(RESET) %s\n", $$1, $$2}'

# Development Setup
install: ## Install production dependencies from lock file
	uv sync --all-extras --no-dev

install-dev: ## Install development dependencies from lock file and setup pre-commit
	uv sync --all-extras --dev
	$(MAKE) pre-commit-install

update-requirements: ## Update dependencies to latest versions and recreate lock file
	uv sync --upgrade
	@echo "$(BLUE)Dependencies updated and uv.lock regenerated$(RESET)"

check-outdated: ## Check for outdated dependencies
	uv pip list --outdated

# Git Hooks
pre-commit-install: ## Install local pre-commit hooks
	uv run pre-commit install

pre-commit: ## Run all pre-commit checks against the repository
	SKIP=detect-secrets uv run pre-commit run --all-files
	$(MAKE) secrets-all

secrets: ## Scan staged files for committed secrets
	scripts/check-staged-secrets.sh --staged

secrets-all: ## Scan tracked and untracked repository files for secrets
	scripts/check-staged-secrets.sh --all

publication-check: ## Reject private installation files and package registries
	uv run python -m voxos.release.source_policy

secrets-baseline: ## Regenerate stable committed secret baseline
	set -e; \
	tmp_baseline="$$(mktemp)"; \
	trap 'rm -f "$$tmp_baseline"' EXIT; \
	uv run detect-secrets scan --slim --exclude-files '(^|/)(\.secrets\.baseline|\.venv|screenlog\.0|uv\.lock|frontend/package-lock\.json)(/|$$)' . > "$$tmp_baseline"; \
	uv run python -m json.tool "$$tmp_baseline" >/dev/null; \
	mv "$$tmp_baseline" .secrets.baseline

# Code Quality
lint: ## Run linting with ruff
	uv run ruff check --no-cache .

format-check: ## Check formatting without using stale tool caches
	uv run ruff check --no-cache .
	uv run ruff format --check --no-cache .

format: ## Format code with ruff
	uv run ruff check --fix --no-cache .
	uv run ruff format --no-cache .

mypy: ## Run mypy type checking
	uv run mypy src/voxos/ examples/skills/scripts/

type-check: ## Run type checking with mypy
	uv run mypy src/voxos/ examples/skills/scripts/

validate: ## Run all validation with auto-fix (ruff and mypy)
	@echo "$(BLUE)Running validation with auto-fix...$(RESET)"
	@echo "$(BLUE)1. Running ruff check with auto-fix...$(RESET)"
	uv run ruff check --fix --no-cache .
	@echo "$(BLUE)2. Running ruff format...$(RESET)"
	uv run ruff format --no-cache .
	@echo "$(BLUE)3. Running mypy...$(RESET)"
	uv run mypy src/voxos/ examples/skills/scripts/
	@echo "$(BLUE)4. Running secret scan...$(RESET)"
	$(MAKE) secrets
	@echo "$(BLUE)✅ All validation checks completed!$(RESET)"

frontend-lint: ## Run frontend linting
	cd frontend && npm run lint

helm-check: ## Validate rendered Kubernetes manifests and deployment contracts
	@command -v helm >/dev/null || { echo "Install Helm before validating the chart"; exit 1; }
	@command -v kubeconform >/dev/null || { echo "Install kubeconform before validating the chart"; exit 1; }
	helm lint helm --strict --values helm/tests/database.yaml
	helm lint helm --strict --values helm/tests/application.yaml
	uv run pytest tests/deployment/test_helm_chart.py -q

whatsapp-bridge-install: ## Install the locked WhatsApp bridge dependencies
	cd whatsapp-bridge && npm ci

whatsapp-bridge-build: whatsapp-bridge-install ## Build the WhatsApp bridge
	cd whatsapp-bridge && npm run build

whatsapp-bridge-check: whatsapp-bridge-install ## Lint, typecheck, and test the WhatsApp bridge
	cd whatsapp-bridge && npm run lint
	cd whatsapp-bridge && npm run typecheck
	cd whatsapp-bridge && npm test

docs-validate: ## Validate wiki docs links, metadata, and manifest
	uv run python -m voxos.product_docs.validation --docs-dir docs/wiki --check-manifest

docs-manifest: ## Regenerate the wiki docs manifest
	uv run python -m voxos.product_docs.validation --docs-dir docs/wiki --write-manifest

docs-index: docs-validate ## Index wiki docs into the configured product-doc database
	uv run python -m voxos.product_docs.ingest --docs-dir docs/wiki --prune

docs-refresh-check: docs-validate ## Validate and dry-run wiki docs indexing
	uv run python -m voxos.product_docs.ingest --docs-dir docs/wiki --dry-run

example-skills-list: ## List curated MCP example-skill groups
	@uv run python -m examples.skills.scripts.load_examples --list

example-skills-load: ## Load one user's examples for MCP=<selector>
	@set -eu; \
	: "$${USER_ID:?USER_ID is required}"; \
	: "$${MCP:?MCP is required}"; \
	example_skills_user_id="$${USER_ID}"; \
	example_skills_mcp="$${MCP}"; \
	example_skills_dry_run="$${DRY_RUN:-0}"; \
	set -a; [ ! -f .env ] || . ./.env; set +a; \
	dry_run_arg=""; \
	case "$${example_skills_dry_run}" in \
		0) ;; \
		1) dry_run_arg="--dry-run" ;; \
		*) echo "DRY_RUN must be 0 or 1" >&2; exit 1 ;; \
	esac; \
	uv run python -m examples.skills.scripts.load_examples \
		--user-id "$${example_skills_user_id}" \
		--mcp "$${example_skills_mcp}" \
		$$dry_run_arg

db-enable-vector: ## Enable pgvector in the configured local Postgres database
	@set -eu; \
	set -a; [ ! -f .env ] || . ./.env; set +a; \
	: "$${DB_HOST:?DB_HOST is required}"; \
	: "$${DB_PORT:?DB_PORT is required}"; \
	: "$${DB_NAME:?DB_NAME is required}"; \
	: "$${POSTGRES_ADMIN_USER:?POSTGRES_ADMIN_USER is required}"; \
	: "$${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"; \
	PGPASSWORD="$${POSTGRES_ADMIN_PASSWORD}" psql \
		-v ON_ERROR_STOP=1 \
		--host "$${DB_HOST}" \
		--port "$${DB_PORT}" \
		--username "$${POSTGRES_ADMIN_USER}" \
		--dbname "$${DB_NAME}" \
		-c "CREATE EXTENSION IF NOT EXISTS vector;"

up: ## Build, reconcile, and verify the complete local Docker Compose stack
	docker compose --env-file ./.env --profile voice --profile whatsapp up \
		--build -d --remove-orphans --wait --wait-timeout 180

restart: up ## Rebuild and reconcile the complete local stack

compose-up: up ## Backward-compatible alias for make up

compose-restart: up ## Backward-compatible alias for make restart

mcp-stack-up: ## Build and start the local MCP services
	docker compose --env-file .env -f docker-compose.mcp.yml up --build -d

mcp-stack-down: ## Stop the local MCP services
	docker compose --env-file .env -f docker-compose.mcp.yml down

mcp-stack-logs: ## Follow logs for running local MCP services
	docker compose --env-file .env -f docker-compose.mcp.yml logs -f

mcp-stack-config: ## Validate the local MCP Compose configuration
	docker compose --env-file .env -f docker-compose.mcp.yml config --quiet

monzo-mcp-restart: ## Pull and restart the published Monzo MCP image
	docker compose --env-file .env -f docker-compose.mcp.yml up --pull always -d --no-deps --force-recreate --wait --wait-timeout 30 monzo-mcp
	$(MAKE) monzo-mcp-package-check

monzo-mcp-package-check: ## Verify Monzo MCP imports aiomonzo from its environment
	docker compose --env-file .env -f docker-compose.mcp.yml exec -T monzo-mcp \
		/app/.venv/bin/python -c "import aiomonzo; from importlib.metadata import version; from pathlib import Path; path = Path(aiomonzo.__file__).resolve(); expected = Path('/app/.venv'); assert path.is_relative_to(expected), path; print(f'aiomonzo {version(\"aiomonzo\")} from {path}')"

# Testing
test: ## Run tests
	uv run pytest -v

test-cov: ## Run tests with coverage
	uv run coverage run -m pytest -v
	uv run coverage html
	uv run coverage report

test-watch: ## Run tests in watch mode
	uv run pytest-watch

# Quality Check (all-in-one)
check: lint type-check test ## Run all quality checks

# FastAPI Server (no database/Redis required)
run: ## Run FastAPI server locally on PORT from .env, defaulting to 7100
	@set -a; [ ! -f .env ] || . ./.env; set +a; NEW_RELIC_ENABLED=false uv run uvicorn voxos.api.main:app --host 0.0.0.0 --port "$${PORT:-7100}" --reload --reload-include '*.jinja'

ngrok: ## Start an ngrok HTTPS/WSS tunnel to the local API
	@set -a; [ ! -f .env ] || . ./.env; set +a; \
	if ! command -v ngrok >/dev/null 2>&1; then \
		echo "ngrok CLI not found. Install ngrok and run 'ngrok config add-authtoken <token>' before using this target."; \
		exit 1; \
	fi; \
	api_port="$${PORT:-7100}"; \
	ngrok_url="$${NGROK_URL:-$${API_PUBLIC_BASE_URL:-}}"; \
	if [ -n "$$ngrok_url" ] && [ "$${ngrok_url#https://}" = "$$ngrok_url" ]; then \
		echo "NGROK_URL must start with https:// when set."; \
		exit 1; \
	fi; \
	echo "$(BLUE)Starting ngrok tunnel for Voxos API on http://localhost:$$api_port$(RESET)"; \
	if [ -n "$$ngrok_url" ]; then \
		echo "Using static ngrok URL: $$ngrok_url"; \
	else \
		echo "No NGROK_URL configured; ngrok will allocate a temporary forwarding URL."; \
	fi; \
	echo "Keep API_PUBLIC_BASE_URL set to the same https:// URL for webhook setup."; \
	echo "Websocket routes are available on the same public host with wss://."; \
	if [ -n "$$ngrok_url" ]; then \
		ngrok http --url="$$ngrok_url" $(NGROK_ARGS) "$$api_port"; \
	else \
		ngrok http $(NGROK_ARGS) "$$api_port"; \
	fi

worker: ## Run the demand-driven multi-tenant LiveKit voice supervisor
	@set -a; [ ! -f .env ] || . ./.env; set +a; uv run python -m voxos.voice.worker

worker-runs: ## Run the durable agent run worker locally
	@set -a; [ ! -f .env ] || . ./.env; set +a; uv run python -m voxos.agent_runtime.run_worker

codex-runner-build: ## Build the pinned disposable Codex CLI runner image
	docker build -t "$${CODEX_CLOUD_RUNNER_IMAGE:-voxos-codex-runner:0.148.0}" docker/codex-runner

worker-codex: ## Run the host-side Codex Cloud queue worker
	@set -a; [ ! -f .env ] || . ./.env; set +a; uv run python -m voxos.codex_cloud.worker

worker-whatsapp: whatsapp-bridge-build ## Run the multi-tenant WhatsApp worker locally
	@set -a; [ ! -f .env ] || . ./.env; set +a; \
		WHATSAPP_BRIDGE_EXECUTABLE=node \
		WHATSAPP_BRIDGE_SCRIPT="$(CURDIR)/whatsapp-bridge/dist/index.js" \
		WHATSAPP_HEALTH_PATH="$${WHATSAPP_HEALTH_PATH:-$${TMPDIR:-/tmp}/voxos-whatsapp-worker-$$(id -u).json}" \
		uv run python -m voxos.whatsapp.worker

scheduler: ## Run the durable agent schedule worker locally
	@set -a; [ ! -f .env ] || . ./.env; set +a; uv run python -m voxos.agent_runtime.schedule_worker

run-prod: ## Run FastAPI server in production mode on port 8000 (no reload)
	@set -a && source .env && set +a && uv run uvicorn voxos.api.main:app --host 0.0.0.0 --port 8000

frontend: ## Run the local LiveKit test frontend
	cd frontend && npm install && npm run dev -- --host 0.0.0.0

# Flutter Mobile App
app-get: ## Install Flutter mobile app dependencies
	cd app && flutter pub get

app-devices: ## List devices available to the Flutter mobile app
	cd app && flutter devices

app-format: ## Format the Flutter mobile app
	cd app && dart format lib test

app-analyze: app-get ## Analyze the Flutter mobile app
	cd app && flutter analyze

app-test: app-get ## Run Flutter mobile app tests
	cd app && flutter test

run-ios: app-get ## Run the Flutter app on an iOS simulator
	@echo "Opening iOS Simulator..."
	@open -a Simulator
	@set -eu; \
		simulator_id="$$(xcrun simctl list devices available | awk -F '[()]' '/iPhone.*Booted/{print $$2; exit}')"; \
		if [ -z "$$simulator_id" ]; then \
			simulator_id="$$(xcrun simctl list devices available | awk -F '[()]' '/iPhone/{print $$2; exit}')"; \
			if [ -z "$$simulator_id" ]; then \
				echo "No available iPhone simulator found. Install one in Xcode."; \
				exit 1; \
			fi; \
			xcrun simctl boot "$$simulator_id"; \
		fi; \
		xcrun simctl bootstatus "$$simulator_id" -b; \
		cd app && flutter run --no-pub $(APP_SENTRY_ARGS) -d "$$simulator_id"

run-ios-device: app-get ## Build, install, and run a debug build on APP_IOS_DEVICE_ID
	@if [ -z "$(APP_IOS_DEVICE_ID)" ]; then \
		echo "Set APP_IOS_DEVICE_ID in ignored app/.env (see app/.env.example)."; \
		exit 1; \
	fi
	cd app && flutter run --no-pub $(APP_SENTRY_ARGS) -d "$(APP_IOS_DEVICE_ID)"

deploy-ios-device: app-get ## Build, install, and run a release build on APP_IOS_DEVICE_ID
	@if [ -z "$(APP_IOS_DEVICE_ID)" ]; then \
		echo "Set APP_IOS_DEVICE_ID in ignored app/.env (see app/.env.example)."; \
		exit 1; \
	fi
	cd app && flutter run --no-pub $(APP_SENTRY_ARGS) --release -d "$(APP_IOS_DEVICE_ID)"

build-prod-ipa: app-get ## Build a release IPA with installation-owned signing
	@echo "Building a release IPA with the signing settings in Installation.xcconfig."
	cd app && flutter build ipa --no-pub --release $(APP_SENTRY_ARGS)

deploy-testflight: app-get ## Build and upload the iOS release to TestFlight
	@echo "Building and uploading with the installation's App Store Connect settings."
	scripts/deploy-testflight.sh

app-run-ios: run-ios-device ## Alias for run-ios-device

app-run-android: app-get ## Build, install, and run the Flutter app on APP_ANDROID_DEVICE_ID
	@if [ -z "$(APP_ANDROID_DEVICE_ID)" ]; then \
		echo "Set APP_ANDROID_DEVICE_ID in ignored app/.env (see app/.env.example)."; \
		exit 1; \
	fi
	cd app && flutter run $(APP_SENTRY_ARGS) -d "$(APP_ANDROID_DEVICE_ID)"

app-build-ios: app-get ## Build an unsigned debug iOS app
	cd app && flutter build ios --debug --no-codesign $(APP_SENTRY_ARGS)

app-build-android: app-get ## Build a debug Android APK
	cd app && flutter build apk --debug $(APP_SENTRY_ARGS)

app-open-ios: ## Open the iOS workspace to configure installation signing
	open app/ios/Runner.xcworkspace

# Docker Build Commands (for local testing)
build: ## Build the Docker image from public locked dependencies
	docker build -t voxos:latest .
