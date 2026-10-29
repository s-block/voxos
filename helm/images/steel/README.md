# Steel browser image

This image builds Steel Browser 0.5.3 from a verified upstream source archive,
uses Node 24 and the current Debian security packages, and applies a locked
set of patched JavaScript dependencies. It runs as UID 1000 with the Chromium
sandbox enabled. The Steel API and CDP transports require the same bearer token.
Chromium accepts its default trusted DevTools origins and retains site isolation.

The Steel/MuPDF service is distributed under AGPLv3 terms, retaining the
upstream Apache and other component notices. Its modified source and complete
MuPDF source are available at `/source/` and in `/usr/share/voxos/source` in
the image. See [source and license instructions](SOURCE.md). Runtime packaging
includes compiled service assets and excludes captured test pages and
dependency test directories.

Build the image and provide a random 32-byte hexadecimal `STEEL_SERVICE_TOKEN`:

```sh
docker build -t voxos/steel-browser:local helm/images/steel
openssl rand -hex 32
```

Configure `images.steel` with the published image repository and digest.
Create the chart's browser Secret with separate `service-token` and
`steel-api-key` keys; use the generated Steel token as `steel-api-key`.

Install `seccomp.json` in each Linux node's kubelet seccomp directory as
`steel-seccomp.json`. The profile retains the container runtime's syscall
restrictions and permits Chromium's user-namespace operations, including
its sandbox's `chroot` call. On Ubuntu with AppArmor user-namespace restrictions,
load `apparmor.profile` as the `voxos-steel` profile before scheduling the browser.
Set `browser.seccompProfile.type` and `browser.appArmorProfile.type` to `Localhost`,
with `localhostProfile` values `steel-seccomp.json` and `voxos-steel` respectively.
The container needs no added Linux capabilities or privilege escalation.

The `/health` endpoint exposes browser readiness without session data. Browser
session, file, UI, and DevTools endpoints require bearer authentication, including
upstream loopback API access. Browser tasks use ephemeral sessions; account
credentials and supported provider profiles are managed by Voxos.

See [Steel Browser](https://github.com/steel-dev/steel-browser),
[Chromium Linux sandboxing](https://chromium.googlesource.com/chromium/src/+/0e94f26e8/docs/linux_sandboxing.md),
and [Playwright container sandbox guidance](https://playwright.dev/docs/docker).
