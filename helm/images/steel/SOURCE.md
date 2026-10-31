# Steel service source and licenses

This image combines Steel Browser 0.5.3 with MuPDF 1.28.1 under AGPLv3 terms.
The original Steel Apache license is `/app/LICENSE`. The full AGPL text is
`AGPL-3.0.txt` beside this file.

Download the modified Steel source and complete MuPDF source at `/source/`
on the service's HTTP port, or copy `/usr/share/voxos/source` from a stopped
container. The Steel source archive includes the dependency locks, Dockerfile,
and Voxos patches used to build the service. Runtime test captures are excluded.
Rebuild with the files under `voxos-build` as the Docker build context.

The MuPDF archive includes its C engine, WebAssembly bindings, build scripts,
and bundled third-party source. Its release commit is
`20061bd45183f5a2bff8f43675e4da91e5ac2901`, matching the npm 1.28.1 package.
It is Artifex's unmodified `mupdf-1.28.1-source.tar.gz`; the image build
downloads a copy from the Voxos repository's `mupdf-1.28.1-source` release and
checks its SHA-256.

Voxos changes enforce bearer authentication, Chromium origin/site isolation,
bounded logging, supported dependency APIs, minimal runtime packaging, and
this source-download route. Modified upstream files carry change notices.
Retain the source-download route and accompanying notices when redistributing
the service. License terms on Apache, MIT, and other separately licensed
portions remain applicable.
