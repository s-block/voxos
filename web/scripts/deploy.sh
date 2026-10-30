#!/usr/bin/env bash
# Uploads the built website in web/dist to its S3 bucket, then purges the site
# from the Cloudflare cache.
#
#   WEB_SITE_URL               required: the site's public URL, such as https://example.com
#   WEB_S3_BUCKET              required: bucket that serves the site
#   WEB_CLOUDFLARE_ZONE_ID     required: Cloudflare zone that holds the site's domain
#   WEB_CLOUDFLARE_API_TOKEN   required: Cloudflare API token with Cache Purge permission
#   WEB_DEPLOY_DRY_RUN=1       optional: list the changes without uploading or purging
#
# make web-deploy reads these from web/.env. AWS credentials come from the usual
# AWS CLI sources, such as AWS_PROFILE.
set -euo pipefail

web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dist_dir="${web_dir}/dist"
site_url="${WEB_SITE_URL:?Set WEB_SITE_URL to the public URL of the site, such as https://example.com}"
bucket="${WEB_S3_BUCKET:?Set WEB_S3_BUCKET to the website bucket name}"
dry_run=""
if [[ "${WEB_DEPLOY_DRY_RUN:-0}" == "1" ]]; then
  dry_run="--dryrun"
else
  zone_id="${WEB_CLOUDFLARE_ZONE_ID:?Set WEB_CLOUDFLARE_ZONE_ID to the Cloudflare zone ID}"
  api_token="${WEB_CLOUDFLARE_API_TOKEN:?Set WEB_CLOUDFLARE_API_TOKEN to a Cloudflare cache purge token}"
fi

if [[ ! -f "${dist_dir}/index.html" ]]; then
  echo "No website build in ${dist_dir}. Run make web-build first." >&2
  exit 1
fi
if ! command -v aws >/dev/null 2>&1; then
  echo "Install the AWS CLI to deploy the website." >&2
  exit 1
fi

if ! site_origin="$(node --eval 'console.log(new URL(process.argv[1]).origin)' "${site_url}" 2>/dev/null)"; then
  echo "WEB_SITE_URL must be a URL such as https://example.com." >&2
  exit 1
fi
# Canonical links, the sitemap and social cards hold the site's URL, so only a
# build made for this URL may be uploaded.
if ! grep -qxF "Sitemap: ${site_origin}/sitemap-index.xml" "${dist_dir}/robots.txt"; then
  echo "The build in ${dist_dir} is not for ${site_origin}. Run make web-build with this WEB_SITE_URL." >&2
  exit 1
fi
# The purge covers only the site's own hostname, so other hostnames in the zone
# keep their cache.
site_host="${site_origin#*://}"

sync() {
  aws s3 sync "${dist_dir}" "s3://${bucket}" --only-show-errors ${dry_run:+"$dry_run"} "$@"
}

page_cache="public, max-age=300, s-maxage=86400"

# Hashed build assets never change. Older ones stay in the bucket so pages that
# browsers still hold keep working.
sync --exclude "*" --include "_astro/*" --cache-control "public, max-age=31536000, immutable"

# Pages and other files: browsers revalidate after five minutes, Cloudflare keeps
# them for a day, and each deploy purges Cloudflare's copies.
sync --delete --exclude "_astro/*" --exclude "*.md" --cache-control "${page_cache}"

# The AWS CLI cannot infer the Markdown media type for the per-page .md files.
sync --delete --exclude "*" --include "*.md" \
  --content-type "text/markdown; charset=utf-8" --cache-control "${page_cache}"

if [[ -n "${dry_run}" ]]; then
  echo "Dry run complete; nothing was uploaded to s3://${bucket} or purged from Cloudflare."
  exit 0
fi

if ! purge_response="$(curl --silent --show-error --fail-with-body --request POST \
  --header "Authorization: Bearer ${api_token}" \
  --header "Content-Type: application/json" \
  --data "{\"hosts\":[\"${site_host}\"]}" \
  "https://api.cloudflare.com/client/v4/zones/${zone_id}/purge_cache")"; then
  echo "Uploaded to s3://${bucket}, but the Cloudflare purge failed: ${purge_response}" >&2
  exit 1
fi
echo "Website uploaded to s3://${bucket} and purged from the Cloudflare cache for ${site_host}."
