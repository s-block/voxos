"""Validate deployable manifests and the chart's migration safety boundary."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "helm"
HELM = shutil.which("helm")
KUBECONFORM = shutil.which("kubeconform")
pytestmark = pytest.mark.skipif(
    HELM is None or KUBECONFORM is None,
    reason="Helm and kubeconform are required for deployment contract tests",
)


def render(mode: str, *extra: str) -> list[dict[str, Any]]:
    assert HELM is not None
    result = subprocess.run(  # noqa: S603 - fixed executable and test arguments
        [
            HELM,
            "template",
            "voxos-db" if mode == "database" else "voxos",
            str(CHART),
            "--values",
            str(CHART / "tests" / f"{mode}.yaml"),
            *extra,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


@pytest.mark.parametrize("mode", ["application", "database"])
def test_manifests_match_kubernetes_schema(mode: str, tmp_path: Path) -> None:
    manifest = tmp_path / "rendered.yaml"
    manifest.write_text(yaml.safe_dump_all(render(mode)))
    assert KUBECONFORM is not None
    subprocess.run(  # noqa: S603 - fixed executable and temporary manifest
        [KUBECONFORM, "-strict", "-summary", str(manifest)], check=True
    )


@pytest.mark.parametrize("installation", ["alpha", "beta"])
def test_operator_profiles_use_only_supplied_installation_parameters(installation: str, tmp_path: Path) -> None:
    assert HELM is not None
    origin = f"https://app.{installation}.example.test"
    values = {
        "images": {
            component: {"repository": f"registry.{installation}.example.test/{component}", "tag": "test"}
            for component in ("api", "frontend", "browserAgent", "steel")
        },
        "application": {"env": {"API_PUBLIC_BASE_URL": origin, "API_CORS_ALLOW_ORIGINS": origin}},
        "frontend": {"firebaseAuthHost": f"project-{installation}.firebaseapp.com"},
        "ingress": {
            "className": f"ingress-{installation}",
            "hosts": [{"host": f"app.{installation}.example.test", "component": "frontend"}],
            "tls": [{"secretName": f"tls-{installation}", "hosts": [f"app.{installation}.example.test"]}],
        },
        "attachments": {"storageClass": f"storage-{installation}"},
        "database": {"host": f"db-{installation}"},
        "networkPolicy": {
            "applicationReleaseName": f"app-{installation}",
            "databaseReleaseName": f"db-{installation}",
            "ingressController": {
                "namespaceSelector": {"kubernetes.io/metadata.name": f"ingress-{installation}"},
                "podSelector": {"app.kubernetes.io/name": f"controller-{installation}"},
            },
        },
    }
    profile = tmp_path / "values.yaml"
    profile.write_text(yaml.safe_dump(values))
    result = subprocess.run(  # noqa: S603 - resolved Helm executable and synthetic installation profile
        [
            HELM,
            "template",
            f"app-{installation}",
            str(CHART),
            "--namespace",
            f"namespace-{installation}",
            "--values",
            str(CHART / "examples/application-values.yaml"),
            "--values",
            str(profile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    ingress = next(document for document in documents if document["kind"] == "Ingress")
    assert ingress["spec"]["rules"][0]["host"] == f"app.{installation}.example.test"
    assert ingress["spec"]["ingressClassName"] == f"ingress-{installation}"
    pvc = next(document for document in documents if document["kind"] == "PersistentVolumeClaim")
    assert pvc["spec"]["storageClassName"] == f"storage-{installation}"
    api = next(
        document
        for document in documents
        if document["kind"] == "Deployment" and document["metadata"]["name"] == f"app-{installation}-api"
    )
    container = api["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == f"registry.{installation}.example.test/api:test"
    environment = {entry["name"]: entry.get("value") for entry in container["env"]}
    assert environment["API_PUBLIC_BASE_URL"] == origin
    assert environment["API_CORS_ALLOW_ORIGINS"] == origin
    assert "app.example.com" not in result.stdout
    manifest = tmp_path / "rendered.yaml"
    manifest.write_text(result.stdout)
    assert KUBECONFORM is not None
    subprocess.run(  # noqa: S603 - resolved schema validator and generated manifest
        [KUBECONFORM, "-strict", "-summary", str(manifest)], check=True
    )


def test_database_is_separate_and_storage_survives_release_deletion() -> None:
    database = render("database")
    application = render("application")
    assert not any(doc["kind"] in {"Deployment", "Job", "Ingress"} for doc in database)
    assert not any(doc["kind"] == "StatefulSet" for doc in application)
    for document in database + application:
        assert document["kind"] != "Secret"
        if document["kind"] == "PersistentVolumeClaim":
            assert document["metadata"]["annotations"] == {
                "helm.sh/resource-policy": "keep",
                "kustomize.toolkit.fluxcd.io/prune": "disabled",
            }
        if document["kind"] == "Service":
            assert document["spec"]["type"] == "ClusterIP"
    migration = next(doc for doc in application if doc["kind"] == "Job")
    assert migration["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert migration["spec"]["backoffLimit"] == 0
    container = migration["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"][1]["secretRef"]["name"] == "voxos-database-migration"
    api = next(doc for doc in application if doc["metadata"]["name"] == "voxos-api" and doc["kind"] == "Deployment")
    assert (
        api["spec"]["template"]["spec"]["containers"][0]["envFrom"][1]["secretRef"]["name"] == "voxos-database-runtime"
    )


def test_existing_claims_are_used_without_creating_new_volumes() -> None:
    for mode, setting in [
        ("database", "database.persistence.existingClaim"),
        ("application", "attachments.existingClaim"),
    ]:
        documents = render(mode, "--set", f"{setting}=retained-volume")
        assert not any(doc["kind"] == "PersistentVolumeClaim" for doc in documents)
        assert any(
            volume.get("persistentVolumeClaim", {}).get("claimName") == "retained-volume"
            for doc in documents
            if doc["kind"] in {"Deployment", "StatefulSet"}
            for volume in doc["spec"]["template"]["spec"].get("volumes", [])
        )


def test_database_uses_configured_private_registry_credentials() -> None:
    documents = render("database", "--set", "imagePullSecrets[0].name=private-registry")
    database = next(document for document in documents if document["kind"] == "StatefulSet")
    assert database["spec"]["template"]["spec"]["imagePullSecrets"] == [{"name": "private-registry"}]


def test_frontend_preserves_websocket_upgrades_for_media_streams() -> None:
    documents = render("application")
    config = next(document for document in documents if document["metadata"]["name"] == "voxos-frontend")
    nginx = config["data"]["default.conf"]
    assert "map $http_upgrade $voxos_connection_upgrade" in nginx
    api_location = nginx.split("location /api/ {", 1)[1].split("}", 1)[0]
    assert "proxy_http_version 1.1;" in api_location
    assert "proxy_set_header Upgrade $http_upgrade;" in api_location
    assert "proxy_set_header Connection $voxos_connection_upgrade;" in api_location


@pytest.mark.parametrize(
    "setting",
    [
        "application.migrationSecret=voxos-database-runtime",
        "frontend.firebaseAuthHost=host;invalid",
        "components.api.replicas=-1",
        "unknownOption=true",
    ],
)
def test_unsafe_or_misspelled_values_are_rejected(setting: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        render("application", "--set", setting)


def test_settings_override_defaults_without_duplicate_environment_keys() -> None:
    documents = render("application", "--set", "application.env.PGSSLMODE=require")
    for document in documents:
        if document["kind"] not in {"Deployment", "Job"}:
            continue
        container = document["spec"]["template"]["spec"]["containers"][0]
        environment = container.get("env", [])
        keys = [entry["name"] for entry in environment]
        assert len(keys) == len(set(keys))
        if "PGSSLMODE" in keys:
            assert next(entry["value"] for entry in environment if entry["name"] == "PGSSLMODE") == "require"


def test_setting_revision_rolls_application_and_mcp_processes() -> None:
    for revision in ("before-rotation", "after-rotation"):
        documents = render("application", "--set", f"application.settingsRevision={revision}")
        deployments = [document for document in documents if document["kind"] == "Deployment"]
        assert deployments
        for document in deployments:
            assert document["spec"]["template"]["metadata"]["annotations"]["voxos.io/settings-revision"] == revision


def test_flux_revision_chart_version_produces_valid_labels(tmp_path: Path) -> None:
    assert HELM is not None
    subprocess.run(  # noqa: S603 - fixed executable and packaging arguments
        [HELM, "package", str(CHART), "--version", "0.1.0+abcdef123456", "--destination", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(  # noqa: S603 - fixed executable and generated chart
        [HELM, "template", "voxos-db", str(tmp_path / "voxos-0.1.0+abcdef123456.tgz"), "--set", "mode=database"],
        check=True,
        capture_output=True,
        text=True,
    )
    for document in yaml.safe_load_all(result.stdout):
        if document:
            assert re.fullmatch(
                r"[A-Za-z0-9]([A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?", document["metadata"]["labels"]["helm.sh/chart"]
            )


def test_frontend_reports_release_revision() -> None:
    revision = "a" * 40
    documents = render("application", "--set", f"application.env.DEPLOYMENT_REVISION={revision}")
    configuration = next(
        document
        for document in documents
        if document["kind"] == "ConfigMap" and document["metadata"]["name"] == "voxos-frontend"
    )
    assert f'add_header X-Voxos-Revision "{revision}" always;' in configuration["data"]["default.conf"]


def test_monzo_token_is_copied_to_a_private_regular_file() -> None:
    documents = render("application", "--set", "mcp.monzo.enabled=true", "--set", "mcp.monzo.image=monzo:test")
    deployment = next(
        document
        for document in documents
        if document["kind"] == "Deployment" and document["metadata"]["name"] == "voxos-monzo-mcp"
    )
    pod = deployment["spec"]["template"]["spec"]
    assert pod["securityContext"]["fsGroup"] == 10001
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["endpoint-token"]["emptyDir"]["medium"] == "Memory"
    process = pod["containers"][0]
    assert "endpoint-token-source" not in {mount["name"] for mount in process["volumeMounts"]}
    initializer = pod["initContainers"][0]
    assert initializer["securityContext"]["runAsUser"] == process["securityContext"]["runAsUser"]
    assert "os.umask(0o077)" in initializer["command"][-1]


def test_whatsapp_health_probe_has_time_for_worker_imports() -> None:
    deployment = next(
        document
        for document in render("application")
        if document["kind"] == "Deployment" and document["metadata"]["name"] == "voxos-whatsapp-worker"
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    for name in ["startupProbe", "readinessProbe"]:
        assert container[name]["timeoutSeconds"] >= 15
        assert container[name]["periodSeconds"] >= container[name]["timeoutSeconds"]


def test_workloads_use_nonroot_readonly_containers_and_writable_scratch() -> None:
    for document in render("application") + render("database"):
        if document["kind"] not in {"Deployment", "StatefulSet", "Job"}:
            continue
        pod = document["spec"]["template"]["spec"]
        assert pod["automountServiceAccountToken"] is False
        assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
        for container in pod["containers"] + pod.get("initContainers", []):
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["readOnlyRootFilesystem"] is True
            assert security["capabilities"] == {"drop": ["ALL"]}
            assert security.get("runAsNonRoot", pod["securityContext"].get("runAsNonRoot")) is True
            assert security.get("runAsUser", pod["securityContext"].get("runAsUser", 0)) > 0
        assert any(volume.get("emptyDir") is not None for volume in pod["volumes"])


def test_browser_credentials_are_scoped_to_the_service_and_its_callers() -> None:
    deployments = [doc for doc in render("application") if doc["kind"] == "Deployment"]
    for deployment in deployments:
        name = deployment["metadata"]["name"]
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        environment = {entry["name"]: entry for entry in container.get("env", [])}
        if name == "voxos-browser-agent":
            assert "envFrom" not in container
            assert environment["BROWSER_AGENT_SERVICE_TOKEN"]["valueFrom"]["secretKeyRef"] == {
                "name": "voxos-browser-service",
                "key": "service-token",
            }
        if "BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN" in environment:
            assert (
                environment["BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN"]["valueFrom"]["secretKeyRef"]["key"]
                == "service-token"
            )
            assert name not in {"voxos-browser-agent", "voxos-steel-browser", "voxos-frontend"}


def _matches_selector(selector: dict[str, Any], labels: dict[str, str]) -> bool:
    if any(labels.get(key) != value for key, value in selector.get("matchLabels", {}).items()):
        return False
    return all(
        expression["operator"] == "In" and labels.get(expression["key"]) in expression["values"]
        for expression in selector.get("matchExpressions", [])
    )


def _pod_traffic_allowed(
    policies: list[dict[str, Any]], source: dict[str, str], destination: dict[str, str], port: int
) -> bool:
    """Check both ends of same-namespace traffic against Kubernetes additive policies."""
    for direction, labels, peer in (("Egress", source, destination), ("Ingress", destination, source)):
        selected = [
            policy
            for policy in policies
            if direction in policy["spec"]["policyTypes"] and _matches_selector(policy["spec"]["podSelector"], labels)
        ]
        if not selected:
            continue
        allowed = False
        for policy in selected:
            for rule in policy["spec"].get(direction.lower(), []):
                if not any(entry["port"] == port and entry["protocol"] == "TCP" for entry in rule["ports"]):
                    continue
                peers = rule.get("to" if direction == "Egress" else "from", [])
                if any(
                    "namespaceSelector" not in candidate
                    and "podSelector" in candidate
                    and _matches_selector(candidate["podSelector"], peer)
                    for candidate in peers
                ):
                    allowed = True
        if not allowed:
            return False
    return True


@pytest.mark.parametrize(
    ("source", "destination", "port", "allowed"),
    [
        ("steel-browser", "postgres", 5432, False),
        ("steel-browser", "api", 8000, False),
        ("steel-browser", "browser-agent", 3100, False),
        ("steel-browser", "monzo-mcp", 8000, False),
        ("browser-agent", "postgres", 5432, False),
        ("browser-agent", "api", 8000, False),
        ("browser-agent", "steel-browser", 3000, True),
        ("browser-agent", "steel-browser", 9222, False),
        ("frontend", "api", 8000, True),
        ("frontend", "postgres", 5432, False),
        ("monzo-mcp", "api", 8000, True),
        ("flightradar-mcp", "api", 8000, False),
        ("api", "postgres", 5432, True),
        ("scheduler", "postgres", 5432, True),
        ("migrate", "postgres", 5432, True),
        ("api", "browser-agent", 3100, True),
        ("agent-run-worker", "monzo-mcp", 8000, True),
        ("voice-worker", "flightradar-mcp", 8000, True),
        ("scheduler", "browser-agent", 3100, False),
    ],
)
def test_network_policy_blocks_lateral_access_and_preserves_required_flows(
    source: str, destination: str, port: int, allowed: bool
) -> None:
    policies = [doc for doc in render("application") + render("database") if doc["kind"] == "NetworkPolicy"]
    source_labels = {"app.kubernetes.io/instance": "voxos", "app.kubernetes.io/component": source}
    destination_labels = {
        "app.kubernetes.io/instance": "voxos-db" if destination == "postgres" else "voxos",
        "app.kubernetes.io/component": destination,
    }
    assert _pod_traffic_allowed(policies, source_labels, destination_labels, port) is allowed


def test_database_rejects_other_installations_in_the_same_namespace() -> None:
    policies = [doc for doc in render("database") if doc["kind"] == "NetworkPolicy"]
    assert not _pod_traffic_allowed(
        policies,
        {"app.kubernetes.io/instance": "unrelated", "app.kubernetes.io/component": "api"},
        {"app.kubernetes.io/instance": "voxos-db", "app.kubernetes.io/component": "postgres"},
        5432,
    )


def test_browser_sandbox_profiles_are_explicit_and_keep_privilege_restrictions() -> None:
    documents = render(
        "application",
        "--set",
        "browser.seccompProfile.type=Localhost",
        "--set",
        "browser.seccompProfile.localhostProfile=steel-seccomp.json",
        "--set",
        "browser.appArmorProfile.type=Localhost",
        "--set",
        "browser.appArmorProfile.localhostProfile=steel-browser",
    )
    deployment = next(
        doc for doc in documents if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "voxos-steel-browser"
    )
    pod = deployment["spec"]["template"]["spec"]
    assert pod["securityContext"]["seccompProfile"] == {
        "type": "Localhost",
        "localhostProfile": "steel-seccomp.json",
    }
    container = pod["containers"][0]
    assert container["securityContext"]["appArmorProfile"] == {
        "type": "Localhost",
        "localhostProfile": "steel-browser",
    }
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    environment = {entry["name"]: entry.get("value") for entry in container["env"]}
    assert environment["DISABLE_CHROME_SANDBOX"] == "false"
    assert container["startupProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/health", "port": "http"}
    assert container["livenessProbe"]["failureThreshold"] == 3


def test_public_egress_retains_private_ranges_and_operator_exclusions() -> None:
    documents = render(
        "application",
        "--set",
        "networkPolicy.publicEgress.enabled=true",
        "--set",
        "networkPolicy.publicEgress.additionalDeniedCIDRs[0]=203.0.113.4/32",
    )
    policy = next(
        doc for doc in documents if doc["kind"] == "NetworkPolicy" and doc["metadata"]["name"] == "voxos-public-egress"
    )
    peer = policy["spec"]["egress"][0]["to"][0]["ipBlock"]
    assert peer["cidr"] == "0.0.0.0/0"
    assert {"10.0.0.0/8", "169.254.0.0/16", "192.168.0.0/16", "203.0.113.4/32"} <= set(peer["except"])
    assert not any("podSelector" in destination for rule in policy["spec"]["egress"] for destination in rule["to"])


@pytest.mark.parametrize(
    "setting",
    [
        "browser.seccompProfile.type=Unconfined",
        "browser.appArmorProfile.type=Unconfined",
        "browser.seccompProfile.type=Localhost",
        "networkPolicy.ingressController.podSelector={}",
        "application.env.BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN=plaintext",
    ],
)
def test_invalid_sandbox_or_wildcard_ingress_configuration_is_rejected(setting: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        render("application", "--set", setting)


def test_firebase_admin_credentials_mount_only_into_python_workloads() -> None:
    documents = render("application", "--set", "application.firebaseAdminCredentialsSecret=firebase-admin")
    for document in documents:
        if document["kind"] not in {"Deployment", "Job"}:
            continue
        pod = document["spec"]["template"]["spec"]
        container = pod["containers"][0]
        volume = next((item for item in pod["volumes"] if item["name"] == "firebase-admin"), None)
        environment = {entry["name"]: entry for entry in container.get("env", [])}
        if "PGSSLMODE" in environment:
            assert volume is not None
            assert volume["secret"]["secretName"] == "firebase-admin"  # pragma: allowlist secret -- resource name
            assert volume["secret"]["defaultMode"] == 0o440
            assert pod["securityContext"]["fsGroup"] == 10001
            assert environment["GOOGLE_APPLICATION_CREDENTIALS"]["value"] == "/var/run/voxos-firebase/credentials.json"
            for process in pod["containers"] + pod.get("initContainers", []):
                mount = next(item for item in process["volumeMounts"] if item["name"] == "firebase-admin")
                assert mount["readOnly"] is True
        else:
            assert volume is None
            assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment
