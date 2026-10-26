#!/usr/bin/env bash
set -euo pipefail

required_uv_version="0.10.4"
installed_uv_version="$(uv --version 2>/dev/null || true)"
if [[ "${installed_uv_version}" != "uv ${required_uv_version}"* ]]; then
    curl -LsSf "https://astral.sh/uv/${required_uv_version}/install.sh" | sh
    export PATH="${HOME}/.local/bin:${PATH}"
    hash -r
fi

uv python install 3.12
uv sync --python 3.12 --all-extras --dev --frozen
uv run --frozen pre-commit install-hooks

export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    source "${NVM_DIR}/nvm.sh"
fi

if ! command -v node >/dev/null 2>&1 ||
    [[ "$(node -p 'Number(process.versions.node.split(".")[0]) >= 22')" != "true" ]]; then
    if command -v nvm >/dev/null 2>&1; then
        nvm install 22
        nvm alias default 22
        nvm use 22
    elif command -v mise >/dev/null 2>&1; then
        mise use --global node@22
        eval "$(mise activate bash)"
    else
        echo "Node.js 22 or newer is required" >&2
        exit 1
    fi
fi

npm --prefix frontend ci
npm --prefix whatsapp-bridge ci
npm --prefix browser-agent ci
npm --prefix docker/codex-runner ci

if ! command -v terraform >/dev/null 2>&1; then
    if ! command -v mise >/dev/null 2>&1; then
        echo "mise is required to install Terraform" >&2
        exit 1
    fi
    mise use --global terraform@1.15.8
    eval "$(mise activate bash)"
fi
terraform version >/dev/null

flutter_version="3.44.8"
flutter_install_dir="${HOME}/.cache/flutter"
if ! command -v flutter >/dev/null 2>&1; then
    if [[ ! -d "${flutter_install_dir}/.git" ]]; then
        git clone --depth 1 --branch "${flutter_version}" \
            https://github.com/flutter/flutter.git "${flutter_install_dir}"
    fi
    export PATH="${flutter_install_dir}/bin:${PATH}"
    flutter_profile='export PATH="$HOME/.cache/flutter/bin:$PATH"'
    touch "${HOME}/.bashrc"
    if ! grep -Fqx "${flutter_profile}" "${HOME}/.bashrc"; then
        printf '\n%s\n' "${flutter_profile}" >>"${HOME}/.bashrc"
    fi
fi

flutter config --no-analytics
dart --disable-analytics
flutter precache --linux
(
    cd app
    flutter pub get
)
