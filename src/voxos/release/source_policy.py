"""Check the public source boundary without reading ignored installation files."""

import fnmatch
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

PRIVATE_ROOTS = frozenset({"deploy", "private", ".voxos", ".secrets", ".aws", ".kube"})
# Terraform holds installation infrastructure and state, so it is private at any depth.
PRIVATE_DIRECTORIES = frozenset({"terraform", ".terraform"})
PRIVATE_NAMES = frozenset(
    {
        "compose.production.yml",
        "installation.properties",
        "Installation.xcconfig",
        "ExportOptions-TestFlight.plist",
        "GoogleService-Info.plist",
        "google-services.json",
        "sentry.config.json",
        "application_default_credentials.json",
        "credentials.json",
        "auth.json",
        "kubeconfig",
        "id_rsa",
        "id_ecdsa",
        "id_ed25519",
        "id_dsa",
        ".terraform.lock.hcl",
        ".terraformrc",
        "terraform.rc",
        "backend.hcl",
    }
)
PRIVATE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.env.*",
    "*.tf",
    "*.tf.json",
    "*.tfbackend",
    "*.tfstate*",
    "*.tfvars*",
    "*.tfplan",
    "*.dump",
    "*.sql.gz",
    "*backup*.sql",
    "*dump*.sql",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "service-account*.json",
    "firebase-admin*.json",
    "*.age",
    "*.agekey",
    "*.pem",
    "*.key",
    "*.p8",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.mobileprovision",
    "*.sops.yaml",
    "*.private.yaml",
    "*.local.yaml",
    "kubeconfig*.yaml",
    "*.kubeconfig",
    "*.tar.gz",
    "*.tgz",
)


def git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        message = "Git is required to validate the public source boundary"
        raise RuntimeError(message)
    return executable


def is_private_path(filename: str) -> bool:
    path = PurePosixPath(filename)
    if path.parts[0] in PRIVATE_ROOTS or not PRIVATE_DIRECTORIES.isdisjoint(path.parts[:-1]):
        return True
    if path.name.endswith(".example"):
        return False
    return path.name in PRIVATE_NAMES or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in PRIVATE_PATTERNS)


def lock_issues(content: bytes) -> list[str]:
    lock = tomllib.loads(content.decode())
    for package in lock.get("package", []):
        source = package.get("source", {})
        if source not in ({"editable": "."}, {"virtual": "."}):
            registry = source.get("registry")
            if registry is None or not is_public_package_url(registry, "pypi.org", "/simple"):
                return ["uv.lock: dependencies must use the public PyPI index"]
        artifacts = [package.get("sdist", {}), *package.get("wheels", [])]
        if any(
            "url" in artifact and not is_public_package_url(artifact["url"], "files.pythonhosted.org")
            for artifact in artifacts
        ):
            return ["uv.lock: package artifacts must use public PyPI download URLs"]
    return []


def is_public_package_url(value: str, hostname: str, path: str | None = None) -> bool:
    url = urlsplit(value)
    return (
        url.scheme == "https"
        and url.netloc == hostname
        and (path is None or url.path.rstrip("/") == path)
        and not url.query
        and not url.fragment
    )


def source_issues(root: Path) -> list[str]:
    index = (
        subprocess.check_output(  # noqa: S603 - resolved Git executable and fixed read-only arguments
            [git_executable(), "ls-files", "--stage", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    issues: set[str] = set()
    for record in filter(None, index):
        metadata, filename = record.split("\t", 1)
        mode, object_id, stage = metadata.split()
        if stage != "0":
            issues.add(f"{filename}: resolve index conflicts before publication")
        if is_private_path(filename):
            issues.add(f"{filename}: private installation path")
        if mode not in {"100644", "100755"}:
            issues.add(f"{filename}: source exports require regular files")
        if filename == "uv.lock" and mode in {"100644", "100755"} and stage == "0":
            content = subprocess.check_output(  # noqa: S603 - object ID comes from the Git index
                [git_executable(), "cat-file", "blob", object_id], cwd=root
            )
            issues.update(lock_issues(content))
    filenames = (
        subprocess.check_output(  # noqa: S603 - resolved Git executable and fixed read-only arguments
            [git_executable(), "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    for filename in filter(None, filenames):
        path = root / filename
        if not path.exists() and not path.is_symlink():
            continue
        if is_private_path(filename):
            issues.add(f"{filename}: private installation path")
        if path.is_symlink():
            issues.add(f"{filename}: source exports require regular files")
    lock_path = root / "uv.lock"
    if not lock_path.is_symlink() and lock_path.is_file():
        issues.update(lock_issues(lock_path.read_bytes()))
    return sorted(issues)


def main() -> int:
    result = subprocess.check_output(  # noqa: S603 - resolved Git executable and fixed arguments
        [git_executable(), "rev-parse", "--show-toplevel"]
    )
    root = Path(result.decode().strip())
    issues = source_issues(root)
    for issue in issues:
        sys.stderr.write(issue + "\n")
    if issues:
        return 1
    sys.stdout.write("Public source boundaries validated\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
