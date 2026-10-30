#!/usr/bin/env bash
# Uploads the built website in web/dist to its S3 bucket, then invalidates the
# CloudFront distribution that serves it.
#
#   WEB_SITE_URL                     required: the site's public URL, such as https://example.com
#   WEB_S3_BUCKET                    required: private bucket that holds the site
#   WEB_CLOUDFRONT_DISTRIBUTION_ID   required: distribution that serves the bucket
#   WEB_DEPLOY_DRY_RUN=1             optional: list the changes without uploading or invalidating
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
  distribution_id="${WEB_CLOUDFRONT_DISTRIBUTION_ID:?Set WEB_CLOUDFRONT_DISTRIBUTION_ID to the CloudFront distribution ID}"
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

sync() {
  aws s3 sync "${dist_dir}" "s3://${bucket}" --only-show-errors ${dry_run:+"$dry_run"} "$@"
}

page_cache="public, max-age=300, s-maxage=86400"

# Hashed build assets never change. Older ones stay in the bucket so pages that
# browsers or CloudFront still hold keep working.
sync --exclude "*" --include "_astro/*" --cache-control "public, max-age=31536000, immutable"

# Pages and other files: browsers revalidate after five minutes, CloudFront keeps
# them for a day, and each deploy invalidates CloudFront's copies.
sync --delete --exclude "_astro/*" --exclude "*.md" --cache-control "${page_cache}"

# The AWS CLI cannot infer the Markdown media type for the per-page .md files.
sync --delete --exclude "*" --include "*.md" \
  --content-type "text/markdown; charset=utf-8" --cache-control "${page_cache}"

if [[ -n "${dry_run}" ]]; then
  echo "Dry run complete; nothing was uploaded to s3://${bucket} or invalidated in CloudFront."
  exit 0
fi

if ! invalidation_id="$(aws cloudfront create-invalidation --distribution-id "${distribution_id}" \
  --paths "/*" --query "Invalidation.Id" --output text)"; then
  echo "Uploaded to s3://${bucket}, but the CloudFront invalidation failed." >&2
  exit 1
fi
echo "Website uploaded to s3://${bucket} for ${site_origin}; requested CloudFront invalidation ${invalidation_id}."
