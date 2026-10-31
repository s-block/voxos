# Third-party software and source

Voxos-authored source is available under the MIT license in `LICENSE`.
Runtime images also contain separately licensed programs and libraries.
Their copyright notices and license terms apply to those components.

## WhatsApp bridge

The API image includes the WhatsApp bridge, Baileys 7.0.0-rc14 (MIT), and
libsignal 6.0.0 (GPLv3). The combined WhatsApp program is distributed under
GPLv3 terms; the MIT notices on its MIT-licensed portions remain in force.
Its source, build configuration, and the exact upstream Baileys and libsignal
sources are included under `/usr/share/voxos/source` in the API image.
The full GPLv3 text is `/usr/share/voxos/licenses/GPL-3.0.txt`.

Sharp's libvips 8.18.7 shared library includes LGPLv3 components and Cairo
under MPL 1.1, alongside separately licensed libraries. The image includes
the versioned sources for all 28 native components, the five upstream build
patches, sharp-libvips 1.3.4 build scripts, and librsvg's locked Rust dependency
sources in `librsvg-rust-vendor.tar.gz`. Cargo source configuration accompanies
the Rust archive. Image builds verify source versions against the installed
native bundle metadata.

The full LGPLv3 and MPL 1.1 texts and sharp-libvips's component notices are
under `/usr/share/voxos/licenses`. Package-level notices remain in the
installed dependencies. Shared libraries and the bridge can be replaced by
rebuilding the image from the included sources and build configuration.

## Steel browser

The Steel image contains a modified Steel Browser 0.5.3 (Apache-2.0) and
MuPDF 1.28.1 (AGPL-3.0-or-later). The combined Steel service is distributed
under AGPLv3 terms, retaining Apache and other applicable notices.
Its exact modified source, build configuration, and complete MuPDF source
are included under `/usr/share/voxos/source` in that image.
Steel serves this directory at `/source/` for users interacting with the
service. Upstream license text is `/app/LICENSE`; AGPLv3 terms accompany
the source files.

The modifications cover service authentication, Chromium isolation, logging,
dependency/API compatibility, runtime packaging, and the source-download route.
Modified upstream files carry a notice identifying the Voxos changes.

## Obtain source from an image

Create a stopped container, copy its source directory, then remove the stopped
container. This does not start the application:

```sh
docker create --name voxos-source IMAGE
docker cp voxos-source:/usr/share/voxos/source ./voxos-source
docker rm voxos-source
```

Use the exact image digest for the release whose source is needed. Keep source
access and the corresponding notices available for every distributed version.
The pinned source archives, SHA-256 digests, and dependency versions are listed
in `licenses/sources.json`. Package-level notices remain with the installed
dependencies; Debian package notices remain under `/usr/share/doc`.
