#!/usr/bin/env bash
set -u

baseline_file=".secrets.baseline"
mode="${1:---staged}"

if [[ "${mode}" != "--staged" && "${mode}" != "--all" ]]; then
  echo "Usage: $0 [--staged|--all]"
  exit 2
fi

if [[ ! -f "${baseline_file}" ]]; then
  echo "Missing ${baseline_file}. Regenerate it before running secret checks."
  exit 1
fi

secret_check_files=()
if [[ "${mode}" == "--all" ]]; then
  while IFS= read -r -d "" check_file; do
    secret_check_files+=("${check_file}")
  done < <(
    {
      git ls-files \
        -z \
        -- \
        . \
        ":(exclude).secrets.baseline" \
        ":(exclude)screenlog.0" \
        ":(exclude)uv.lock" \
        ":(exclude)frontend/package-lock.json"
      git ls-files \
        --others \
        --exclude-standard \
        -z \
        -- \
        . \
        ":(exclude).secrets.baseline" \
        ":(exclude)screenlog.0" \
        ":(exclude)uv.lock" \
        ":(exclude)frontend/package-lock.json"
    }
  )
  check_scope="tracked and untracked"
else
  while IFS= read -r -d "" check_file; do
    secret_check_files+=("${check_file}")
  done < <(
    git diff \
      --cached \
      --name-only \
      --diff-filter=ACMR \
      -z \
      -- \
      . \
      ":(exclude).secrets.baseline" \
      ":(exclude)screenlog.0" \
      ":(exclude)uv.lock" \
      ":(exclude)frontend/package-lock.json"
  )
  check_scope="staged"
fi

if [[ ${#secret_check_files[@]} -eq 0 ]]; then
  echo "No ${check_scope} files to scan for secrets."
  exit 0
fi

tmp_baseline="$(mktemp)"
cp "${baseline_file}" "${tmp_baseline}"
trap 'rm -f "${tmp_baseline}"' EXIT

secret_output="$(
  uv run detect-secrets-hook \
  --baseline "${tmp_baseline}" \
  "${secret_check_files[@]}" 2>&1
)"
status=$?

if [[ "${secret_output}" == *"ERROR: Potential secrets"* ]]; then
  echo "${secret_output}"
  exit 1
fi

if [[ ${status} -eq 3 ]]; then
  exit 0
fi

if [[ ${status} -ne 0 ]]; then
  echo "${secret_output}"
fi

exit "${status}"
