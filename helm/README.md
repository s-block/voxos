# Voxos Helm chart

The chart deploys the Voxos API, web frontend, durable-run worker, scheduler,
voice supervisor, WhatsApp worker, browser task service and Steel browser.
Optional FlightRadar24 and Monzo MCP services remain internal to the cluster.
Set `mcp.flightradar.image` before enabling FlightRadar24. Its image must include
the `supergateway` and `mcp-fr24api-mcp` executables with dependencies installed
at build time. MCP integrations require operator-supplied images and Secrets.

Use Kubernetes 1.34 or newer, Helm, persistent storage, and images built from
the same Voxos revision. Configure an ingress controller and TLS for public
access. Local clusters can use port forwarding.

## Configure an installation

Copy the [operator examples](examples/) into the ignored `private/` directory:

```bash
mkdir -p private
chmod 700 private
cp helm/examples/database-values.yaml private/database-values.yaml
cp helm/examples/application-values.yaml private/application-values.yaml
cp helm/examples/database-bootstrap.env.example private/database-bootstrap.env
cp helm/examples/database-runtime.env.example private/database-runtime.env
cp helm/examples/database-migration.env.example private/database-migration.env
cp helm/examples/application.env.example private/application.env
chmod 600 private/*
```

Set the four image tags or digests to one reviewed source revision. To use your
own registry, also override each `images.<component>.repository`. Public release
images use the `voxos-oss-api`, `voxos-oss-frontend`, `voxos-oss-browser-agent`,
and `voxos-oss-steel-browser` package names. Build contexts are the repository
root, `frontend/`, `browser-agent/`, and `helm/images/steel/`, respectively.
The frontend uses `frontend/Dockerfile.production`.

Choose your namespace, release names, storage classes, ingress class and
controller selectors. If release names change, update the network-policy
release names and the database Service host in both database URLs. Configure
DNS to reach the ingress controller and provision a TLS Secret in that namespace,
either with your certificate controller or `kubectl create secret tls` using
your certificate and private-key files.

The example uses `app.example.com` as a single public origin. Change it in
`ingress.hosts`, `ingress.tls`, `API_PUBLIC_BASE_URL`, `API_CORS_ALLOW_ORIGINS`,
and `FIREBASE_AUTH_DOMAIN`. The frontend proxies `/api/`, including streaming
and WebSocket connections, to the API. For separate origins, add an ingress
host with `component: api`, set `API_PUBLIC_BASE_URL` to that API origin for
callbacks, and include the frontend origin in `API_CORS_ALLOW_ORIGINS`. The web
frontend still uses its own origin and its `/api/` proxy.

Create your Firebase project and web, Android and iOS app registrations. Fill
all `FIREBASE_*` fields required by the [client configuration](../.env.example).
Use your configured mobile application IDs for the native registrations. Add
the frontend hostname to Firebase Authentication's authorized domains, enable
the sign-in providers you use, and register
`https://YOUR_FRONTEND_HOST/__/auth/handler` as the Google OAuth redirect URI.
Set `frontend.firebaseAuthHost` to your project's Firebase Hosting hostname so
the chart can proxy the authentication handler for the custom domain.

Save the Firebase Admin service-account configuration as
`private/firebase-admin.json` and grant it the Firebase Authentication read
permissions needed for session-revocation checks. Create its Secret before
installing the app; the operator example enables those checks.

Generate separate database passwords, browser service tokens, voice routing
and MCP OAuth signing secrets. Generate the AES-GCM key once with
`openssl rand -base64 32`; preserve it with encrypted off-cluster backups.
Alternatively configure an existing supported AWS or Google Cloud KMS key.
Use the runtime database role in `database-runtime.env` and the owner role in
`database-migration.env`, percent-encoding passwords in their URLs. Blank
credentials in the templates must be filled before creating Secrets.

Tenant provider, MCP and LiveKit credentials are entered through Voxos and
stored in its encrypted user vault. Application replicas share the deployment's
vault encryption and signing configuration.

## Database and application releases

Install two releases in the same namespace. `mode: database` runs PostgreSQL 18
with pgvector and initializes a restricted application role. `mode: application`
uses that database Service and runs a migration Job before installing or
upgrading application workloads. The database and all referenced Secrets must
already exist when this Job starts.

Create Secrets from private environment files:

```bash
kubectl create namespace voxos
kubectl -n voxos create secret generic voxos-database-bootstrap --from-env-file=private/database-bootstrap.env
kubectl -n voxos create secret generic voxos-database-runtime --from-env-file=private/database-runtime.env
kubectl -n voxos create secret generic voxos-database-migration --from-env-file=private/database-migration.env
kubectl -n voxos create secret generic voxos-settings --from-env-file=private/application.env
umask 077
openssl rand -hex 32 > private/browser-service-token
openssl rand -hex 32 > private/steel-service-token
kubectl -n voxos create secret generic voxos-browser-service --from-file=service-token=private/browser-service-token --from-file=steel-api-key=private/steel-service-token
kubectl -n voxos create secret generic voxos-firebase-admin --from-file=credentials.json=private/firebase-admin.json
```

`database-bootstrap.env` provides `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`POSTGRES_DB`, `APP_DB_OWNER`, `APP_DB_OWNER_PASSWORD`, `APP_DB_USER`,
`APP_DB_PASSWORD`, and `APP_DB_NAME`.
`APP_DB_NAME` must match `POSTGRES_DB`; all three roles must differ. Database and role
names use letters, digits, and underscores, starting with a letter or underscore.
The application role has `NOSUPERUSER` and `NOBYPASSRLS` privileges.

The runtime and migration files each provide `DATABASE_URL` using
`postgresql+asyncpg://` and the database Service hostname, default `voxos-db`.
The runtime role is `APP_DB_USER`. The migration role is `APP_DB_OWNER`, which
owns the application schema. The bootstrap administrator creates the vector
extension. Migrations grant runtime data access and remove Alembic write
privileges. API/worker init containers check that the runtime login cannot
assume the owner role, create schema objects, or bypass RLS before starting.
For an external database, the bootstrap Secret must at least provide
`APP_DB_USER`; provision the restricted runtime and owner roles separately.

`application.env` provides the Firebase client settings required by
`/api/client-config`, `FIREBASE_PROJECT_ID`, the credential encryption settings,
and shared voice/MCP signing settings. Follow the [environment reference](../.env.example).
API and worker replicas must use the same encryption keys and voice routing
settings. Tenant LiveKit credentials belong in the user vault.

```bash
helm upgrade --install voxos-db ./helm -n voxos --set mode=database --wait --timeout 5m
helm upgrade --install voxos ./helm -n voxos --values /private/path/application-values.yaml --wait --timeout 5m
```

Supply explicit application image tags or digests in the private values file:

```yaml
images:
  api: {tag: sha-0123456}
  frontend: {tag: sha-0123456}
  browserAgent: {tag: sha-0123456}
  steel: {repository: registry.example.test/steel-browser, tag: sha-0123456}
```

Build the Steel image from `helm/images/steel` and publish it to the registry
configured by `images.steel.repository`. The image runs Chromium as a non-root user
and authenticates its HTTP and DevTools endpoints.

The defaults reference the project container repositories. Operators can set
their own repositories; production deployments should pin image digests.
The frontend, browser task service, and Steel release workflows build amd64 and
arm64 images. Build the API for the architecture of its target nodes. A database
image change requires a planned PostgreSQL upgrade and a tested restore.

After filling the copied examples, install with:

```bash
helm upgrade --install voxos-db helm --namespace voxos --values private/database-values.yaml --wait
helm upgrade --install voxos helm --namespace voxos --values private/application-values.yaml --wait
```

Wait for the database to be ready before the application migration hook starts.
Keep the namespace and release names consistent with your private values.

## Configuration and access

The production frontend uses its current origin for API requests and obtains
Firebase settings from `/api/client-config`. Set `frontend.firebaseAuthHost` to
the installation's Firebase project host when using a custom authentication
domain; the frontend proxies `/__/auth/` to that host. Configure Firebase's
authorized domains for the browser addresses users will access.

The optional `frontend.publicConfigSecret` references a Secret with a
`runtime-config.js` key containing browser-visible configuration:

```javascript
globalThis.__VOXOS_CONFIG__ = Object.freeze({logoDevPublishableKey: "your-publishable-key"});
```

Ingress is disabled by default. Configure `ingress.className`, `ingress.hosts`
with `host` and `component` (`frontend` or `api`), and `ingress.tls` with existing
certificate Secret references. Keep hostnames, public URLs, CORS origins,
provider settings and credentials in private configuration. The Helm `application.env` map
contains non-credential overrides; the private environment file used to create
`application.existingSecret` also holds encryption and signing credentials.
`browser.existingSecret` holds a `service-token` shared by the browser task service
and its API/worker callers. Its `steel-api-key` authenticates the task service to
Steel, including DevTools connections. Use separate randomly generated values for these keys.

Set `application.settingsRevision` to a new revision whenever referenced settings
or runtime database Secrets change. This rolls the processes that load environment
variables at startup. Database password rotation also requires updating the actual
database role before rolling its clients.

For local access:

```bash
kubectl -n voxos port-forward --address 127.0.0.1 svc/voxos-frontend 18080:8080
```

Open `http://localhost:18080`. For restored-data rehearsals, set
`components.agent-run-worker.replicas`, `components.scheduler.replicas`,
`components.voice-worker.replicas`, and `components.whatsapp-worker.replicas`
to zero. Use an egress policy to prevent external effects.

## Workload and network security

Application containers run as non-root users, drop Linux capabilities and use
read-only root filesystems. Writable temporary directories are ephemeral;
attachments and database data use their configured PVCs.

Set `networkPolicy.enabled: true` with the ingress controller
`namespaceSelector` and `podSelector` labels, plus the cluster DNS labels under
`networkPolicy.dns`. Both releases need the setting. The defaults expect release
names `voxos` and `voxos-db`; update `applicationReleaseName` and
`databaseReleaseName` when using other names. For an external database, provide
a separate egress policy for its destination addresses and database port.
The policies restrict inbound and
outbound connections to the database, browser services and MCP credential broker.
Steel can receive connections from the browser task service only. These policies
require a network plugin that enforces Kubernetes NetworkPolicy.

For public provider and browser connections, either configure a separate egress
policy or enable `networkPolicy.publicEgress.enabled`. The chart permits public
IPv4 destinations while excluding private, loopback and link-local ranges. Add
cluster node public IP ranges and other installation-specific protected networks
to `networkPolicy.publicEgress.additionalDeniedCIDRs` in private values. Network
policies are additive, so remove broader allow policies before relying on these
restrictions.

Steel keeps Chromium sandboxing enabled. Configure `browser.seccompProfile` and
`browser.appArmorProfile` with compatible, preinstalled node profiles when the
container runtime defaults block Chromium user namespaces. Use `type: Localhost`
and `localhostProfile` for installed profiles. Keep the Kubernetes
`allowPrivilegeEscalation: false` and capability restrictions in place.

Configure HTTPS response headers, including HSTS, on the ingress controller so
API and frontend responses receive the same transport policy. Enable production
API authentication revocation checks and configure Firebase Admin credentials
using private settings. `application.firebaseAdminCredentialsSecret` references
a Secret with a `credentials.json` key containing Firebase Admin credentials.
The chart mounts it read-only for API/worker processes and sets
`GOOGLE_APPLICATION_CREDENTIALS`. Grant the identity only the Firebase Auth
read permissions needed to check user state, and enable
`FIREBASE_AUTH_CHECK_REVOKED` after verifying access.

## Storage and upgrades

Database and attachment PVCs have Helm retention and Flux pruning protection.
Use a StorageClass with `reclaimPolicy: Retain` and maintain encrypted off-cluster
backups of PostgreSQL, attachments and encryption keys. Namespace or cluster
deletion can still remove data. `database.persistence.existingClaim` and
`attachments.existingClaim` reuse existing volumes.

The attachment PVC uses `ReadWriteOnce` storage shared on one node. Multi-node
deployments need suitable shared storage or the supported S3 attachment backend;
set `attachments.enabled: false` and configure that backend through private
application settings. Keep the database release independent of app rollbacks.

A failed pre-install or pre-upgrade migration prevents the application rollout.
Successful migrations can change the schema before a later workload failure;
test schema compatibility with the previous image before relying on rollback.

Voice, WhatsApp and scheduler Deployments use `Recreate` to prevent overlap at
upgrade time. Configure worker concurrency and voice lease/shutdown settings
through the shared application Secret. API readiness uses `/health`; validate
database and credential health separately before routing traffic.

## Flux and validation

Flux can reconcile the chart from Git or OCI. Use a private GitOps repository,
SOPS-encrypted values Secrets and `HelmRelease.valuesFrom`. Reconcile infrastructure,
Secrets, database and application in dependency order. Suspend the application
release while restoring data. Set `HelmRelease.spec.serviceAccountName` and
`Kustomization.spec.serviceAccountName` to accounts with permissions scoped to
the application namespace. Keep deployment keys and private values outside
the public source and chart package.

```bash
helm lint helm --strict --values helm/tests/database.yaml
helm lint helm --strict --values helm/tests/application.yaml
uv run pytest tests/deployment/test_helm_chart.py -q
helm package helm
```

The Kubernetes artifacts workflow validates the chart and publishes portable
frontend, browser task service, and Steel images for main-branch revisions. A tag `helm-v<chart-version>`
publishes the matching chart to
`oci://ghcr.io/OWNER/REPOSITORY-oss-charts/voxos`. Release configuration supplies the image
versions appropriate for that deployment.

Set `application.env.DEPLOYMENT_REVISION` to the source commit or release
identifier to expose an `X-Voxos-Revision` header on API health and frontend
responses. A deployment workflow can check these headers after promoting
digest-pinned images through Flux. Store deployment credentials and private URLs
as GitHub Actions environment secrets; keep production promotion workflows and
installation values in the private GitOps repository.
