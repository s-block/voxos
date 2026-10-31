FROM node:22-bookworm-slim AS whatsapp-bridge-build

WORKDIR /build/whatsapp-bridge

COPY whatsapp-bridge/package.json whatsapp-bridge/package-lock.json ./
RUN npm ci

COPY whatsapp-bridge/tsconfig.json whatsapp-bridge/tsconfig.build.json ./
COPY whatsapp-bridge/src/ src/
RUN npm run build \
    && npm prune --omit=dev

FROM --platform=$BUILDPLATFORM rust:1.97.0-slim-bookworm@sha256:6d220bf85c74e842a79da63997af8d2e74455c0b8847d8bb3a5888572334991d AS librsvg-rust-source

RUN apt-get update \
    && apt-get install -y --no-install-recommends xz-utils \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:852b18e1a00b8605528825a27dc7748bff2a5dd254028f59dc22a34ea57e81b6 https://download.gnome.org/sources/librsvg/2.63/librsvg-2.63.2.tar.xz /tmp/librsvg.tar.xz
RUN mkdir /librsvg /vendor \
    && tar -xJf /tmp/librsvg.tar.xz -C /librsvg --strip-components=1 \
    && cd /librsvg \
    && cargo vendor --locked --versioned-dirs /vendor > /vendor-config.toml \
    && tar -czf /librsvg-rust-vendor.tar.gz -C / vendor vendor-config.toml

FROM node:22-bookworm-slim AS redistribution-source

WORKDIR /source
COPY whatsapp-bridge/src ./whatsapp-bridge/src
COPY whatsapp-bridge/package.json whatsapp-bridge/package-lock.json whatsapp-bridge/tsconfig.json whatsapp-bridge/tsconfig.build.json ./whatsapp-bridge/
COPY LICENSE Dockerfile THIRD_PARTY_NOTICES.md ./
COPY licenses ./licenses
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/node_modules/@img /tmp/native-bundles
RUN node --input-type=module <<'NODE'
import { readFileSync } from "node:fs";
const sources = JSON.parse(readFileSync("licenses/sources.json", "utf8"));
const versions = JSON.parse(readFileSync(`/tmp/native-bundles/sharp-libvips-linux-${process.arch}/versions.json`, "utf8"));
for (const [library, version] of Object.entries(versions)) {
  const source = sources[library === "vips" ? "libvips" : `native-${library}`];
  if (!source || source.version !== version) {
    throw new Error(`Missing matching redistribution source for ${library} ${version}`);
  }
}
NODE
RUN mkdir /sources \
    && tar -czf /sources/voxos-whatsapp-source.tar.gz .
ADD --checksum=sha256:e768616335cdeac4e853f72d36329381527c3166099c614601cc5317c94b6713 https://codeload.github.com/WhiskeySockets/Baileys/tar.gz/7e7b0757e3f9f3c7789fb1cfd2f241d5002a199a /sources/baileys-7.0.0-rc14.tar.gz
ADD --checksum=sha256:e8237ac721bdf8c622d2f13a4035b69acccb9f0c5999aa5e88944c4f171dd174 https://codeload.github.com/WhiskeySockets/libsignal-node/tar.gz/bcea72df9ec34d9d9140ab30619cf479c7c144c7 /sources/libsignal-6.0.0.tar.gz
ADD --checksum=sha256:5baaead3b0bb20ffdb9e9ff09aa9fda08620923df77b63b436654cb5e0b3bf94 https://github.com/libvips/libvips/releases/download/v8.18.7/vips-8.18.7.tar.xz /sources/libvips-8.18.7.tar.xz
ADD --checksum=sha256:a8e96acb479c9bc1a5b7950f8f25aa6c8e1ef3a19755b45cf2f00d506aeaccc0 https://codeload.github.com/lovell/sharp-libvips/tar.gz/v1.3.4 /sources/sharp-libvips-1.3.4.tar.gz
ADD --checksum=sha256:f7a1cbd7579baaf575c66f9d99fb6295e9b0684a28b095967cfda17857595303 https://github.com/frida/proxy-libintl/archive/0.5.tar.gz /sources/native-proxy-libintl-0.5.tar.gz
ADD --checksum=sha256:f9c65aa9c852eb8255b636fd9f07ce1c406f061ec19a2e7d508b318ca0c907d1 https://github.com/zlib-ng/zlib-ng/archive/2.3.3.tar.gz /sources/native-zlib-ng-2.3.3.tar.gz
ADD --checksum=sha256:7da3e2d9a171eb0a038f592ecad3ff2bb2550f3496d87b3b29ad0cf4430c0db4 https://github.com/libffi/libffi/releases/download/v3.8.0/libffi-3.8.0.tar.gz /sources/native-ffi-3.8.0.tar.gz
ADD --checksum=sha256:17d15cac2af80a33271127408e0abc2748eb297c595c2a26409e81e14e7d1b8f https://download.gnome.org/sources/glib/2.90/glib-2.90.0.tar.xz /sources/native-glib-2.90.0.tar.xz
ADD --checksum=sha256:b1f2930a11292e529de137c7eb723850e0cc0969b1a2b0358196389312acd272 https://gist.github.com/kleisauke/284d685efa00908da99ea6afbaaf39ae/raw/bdad5489a61c217850631571caf57f5db6ea8b2c/glib-without-gregex.patch /sources/native-patch-glib-2.90.0.patch
ADD --checksum=sha256:98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821 https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.4.tar.xz /sources/native-xml2-2.15.4.tar.xz
ADD --checksum=sha256:4a055ed6575e61ca46c3172be3c753cc16c9becd0f99ec71d58dd0e471476c0c https://github.com/libexif/libexif/releases/download/v0.6.26/libexif-0.6.26.tar.xz /sources/native-exif-0.6.26.tar.xz
ADD --checksum=sha256:bfc54f7bab59fbc921012014a8032e4cba4abd46db47d46b76416a8c0b2815c8 https://github.com/mm2/Little-CMS/releases/download/lcms2.19.1/lcms2-2.19.1.tar.gz /sources/native-lcms-2.19.1.tar.gz
ADD --checksum=sha256:8ca0c52746174603500f0adb6f2a215d69c9ca2aab2acb3caa06fb791d8d01bf https://storage.googleapis.com/aom-releases/libaom-3.15.1.tar.gz /sources/native-aom-3.15.1.tar.gz
ADD --checksum=sha256:fd9036064c4432f0550d15072ddf34956a248279ee9aeaff0fba3fa0f77d8f1a https://github.com/strukturag/libheif/releases/download/v1.23.5/libheif-1.23.5.tar.gz /sources/native-heif-1.23.5.tar.gz
ADD --checksum=sha256:b680167bd5d9afebdd576a12f8c49d2c9066a3b92ef96a2b2c5f7829b8600fe5 https://github.com/mozilla/mozjpeg/archive/0826579.tar.gz /sources/native-mozjpeg-0826579.tar.gz
ADD --checksum=sha256:a65f87cadf3a6052e44d6314c906f6e2891352f82646b8a6f0b8fcf2c1891e7c https://github.com/mozilla/mozjpeg/commit/f90668e0e4fb79c81e1f24a0ccc0e2090af761bf.patch /sources/native-patch-mozjpeg-0826579.patch
ADD --checksum=sha256:a9d4df463d36a6e5f9c29bd6f4967312d17e996c1854f3511f833924eb1993cf https://github.com/pnggroup/libpng/archive/v1.6.58.tar.gz /sources/native-png-1.6.58.tar.gz
ADD --checksum=sha256:47d2a84b7b1052975c9d50a3d4e3cacbf57b43d84a4c3131210848ead9964dfb https://github.com/lovell/libimagequant/archive/v2.4.1.tar.gz /sources/native-imagequant-2.4.1.tar.gz
ADD --checksum=sha256:e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564 https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz /sources/native-webp-1.6.0.tar.gz
ADD --checksum=sha256:62b15f44c10106065211efa22a2dc77937bd97ee1f6e3afd2c9659010de112eb https://gitlab.com/libtiff/libtiff/-/archive/v4.7.2/libtiff-v4.7.2.tar.gz /sources/native-tiff-4.7.2.tar.gz
ADD --checksum=sha256:e72241ac9524bb653ae52ced768b508045d4438726a303f10181a38f764a453c https://github.com/google/highway/archive/1.4.0.tar.gz /sources/native-highway-1.4.0.tar.gz
ADD --checksum=sha256:dc49de6b01a266eef4876a4dd34d9842c475d3e28ff2eff63bd2fb760ab56261 https://github.com/freetype/freetype/archive/VER-2-14-3.tar.gz /sources/native-freetype-2.14.3.tar.gz
ADD --checksum=sha256:1e727b8933ec51a77a9a9d9afcf8e688bce45d907c13e36ab7393fe36e703182 https://github.com/libexpat/libexpat/releases/download/R_2_8_5/expat-2.8.5.tar.xz /sources/native-expat-2.8.5.tar.xz
ADD --checksum=sha256:888c934f9d95648ecb9163dc8e23ab80a476ecb81a8f1154704a227b5b676dde https://github.com/libarchive/libarchive/releases/download/v3.8.9/libarchive-3.8.9.tar.xz /sources/native-archive-3.8.9.tar.xz
ADD --checksum=sha256:9ae01e1d53acdef56010c5451cd34aa41d325b2faccd8606448d8fa01b2496b3 https://gitlab.freedesktop.org/fontconfig/fontconfig/-/archive/2.18.3/fontconfig-2.18.3.tar.gz /sources/native-fontconfig-2.18.3.tar.gz
ADD --checksum=sha256:97ec7488a46b974e18e08ee774af93173e7e1dbf738424796eb315ebcb735ec5 https://github.com/harfbuzz/harfbuzz/archive/14.5.0.tar.gz /sources/native-harfbuzz-14.5.0.tar.gz
ADD --checksum=sha256:d09c44ebc3bd5bee7021c79f922fe8fb2fb57f7320f55e97ff9914d2346a591c https://cairographics.org/releases/pixman-0.46.4.tar.gz /sources/native-pixman-0.46.4.tar.gz
ADD --checksum=sha256:1c767308174337a74694da0f3ec069c271452163a1ef4540964c50c301f157d4 https://cairographics.org/releases/cairo-1.18.6.tar.xz /sources/native-cairo-1.18.6.tar.xz
ADD --checksum=sha256:6949dcde27d41cebad1fd741fcafc36d55a1020d2d872d4a6eb3914caabbada2 https://github.com/fribidi/fribidi/releases/download/v1.0.17/fribidi-1.0.17.tar.xz /sources/native-fribidi-1.0.17.tar.xz
ADD --checksum=sha256:342385b6ca3b7c73455d7c80a13b7dbe4489e00bc3bd4c5bd6ed4dce421e374a https://download.gnome.org/sources/pango/1.58/pango-1.58.2.tar.xz /sources/native-pango-1.58.2.tar.xz
ADD --checksum=sha256:852b18e1a00b8605528825a27dc7748bff2a5dd254028f59dc22a34ea57e81b6 https://download.gnome.org/sources/librsvg/2.63/librsvg-2.63.2.tar.xz /sources/native-rsvg-2.63.2.tar.xz
ADD --checksum=sha256:8b841d2ec8feb964fa76859075e0e0c41b69136ba143cf0d26469d9bebc19231 https://github.com/GNOME/librsvg/commit/9106011db93d701728ac2c9da50c9ab2c1bb5dc6.patch /sources/native-patch-rsvg-2.63.2.patch
ADD --checksum=sha256:83a70a15bc2da41f081a44ebc58ee48e2e1d524a6d3fdb4a24064afa08d5ad4d https://github.com/dloebl/cgif/archive/v0.5.4.tar.gz /sources/native-cgif-0.5.4.tar.gz
ADD --checksum=sha256:aa8d193bb887c348c419780511dd03b374f4e07af8812b6d3f80c8537cf1ef2c https://github.com/google/libultrahdr/archive/v2.0.2.tar.gz /sources/native-uhdr-2.0.2.tar.gz
ADD --checksum=sha256:57658db2a11ae964755e513b01db21bcfa408c350b11b3a6ef9bc8576fb749ba https://patch-diff.githubusercontent.com/raw/google/libultrahdr/pull/383.patch /sources/native-patch-uhdr-2.0.2.patch
ADD --checksum=sha256:256b7d6f8c973bb53b20cff5171c7117d6b35d3aa4e72d22cb906f4fe1e2c890 https://gist.githubusercontent.com/lovell/313a6901e9db1bf285f2a1f1180499e4/raw/3988223c7dfa4d22745d9392034b0117abef1446/libvips-cpp-soversion.patch /sources/native-patch-vips-8.18.7.patch
COPY --from=librsvg-rust-source /librsvg-rust-vendor.tar.gz /sources/
RUN chmod 0644 /sources/*


FROM python:3.12-slim

ARG OCI_SOURCE=""
ARG OCI_REVISION=""

LABEL org.opencontainers.image.source=$OCI_SOURCE \
    org.opencontainers.image.revision=$OCI_REVISION

# Apply available base-image security updates before installing system dependencies.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /bin/uv
RUN python -m pip uninstall --yes pip

WORKDIR /app

# The Python supervisor owns the bridge as a stdio child process. Copy the
# pinned Node runtime and production-only bridge artifacts into the same image;
# no package installation or remote version lookup occurs at worker startup.
COPY --from=whatsapp-bridge-build /usr/local/bin/node /usr/local/bin/node
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/package.json /app/whatsapp-bridge/package.json
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/dist/ /app/whatsapp-bridge/dist/
COPY --from=whatsapp-bridge-build /build/whatsapp-bridge/node_modules/ /app/whatsapp-bridge/node_modules/
COPY --from=redistribution-source /sources/ /usr/share/voxos/source/
COPY licenses/ /usr/share/voxos/licenses/
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/voxos/
RUN node --version \
    && test -f /app/whatsapp-bridge/dist/index.js

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .
COPY README.md .
COPY scripts/ scripts/

# Install the project
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Health check for FastAPI server
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run FastAPI with uvicorn (no database/Redis required)
CMD ["uvicorn", "voxos.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
