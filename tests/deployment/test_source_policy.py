import subprocess
from pathlib import Path

import pytest

from voxos.release.source_policy import git_executable, is_private_path, lock_issues, source_issues

ROOT = Path(__file__).resolve().parents[2]
GIT = git_executable()


@pytest.mark.parametrize(
    "filename",
    [
        "terraform/hetzner/main.tf",
        "deploy/application.yaml",
        ".voxos/attachments/customer.png",
        ".secrets/service-account.json",
        ".secrets",
        ".env.production",
        "application.env",
        "database-runtime.env",
        "database.dump",
        "database-backup.sql",
        "database.sql.gz",
        "database.sqlite3",
        "firebase-admin.json",
        "service-account.local.json",
        "kubeconfig",
        "id_ed25519",
        "backup.tar.gz",
        "kubeconfig.production.yaml",
        "signing.p8",
        "app/android/installation.properties",
        "app/ios/Flutter/Installation.xcconfig",
        "app/ios/ExportOptions-TestFlight.plist",
        "app/ios/Runner/GoogleService-Info.plist",
        "app/android/app/google-services.json",
    ],
)
def test_installation_files_are_private_and_ignored(filename: str) -> None:
    assert is_private_path(filename)
    result = subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "-c", "core.excludesFile=/dev/null", "check-ignore", "--no-index", "-q", filename], cwd=ROOT, check=False
    )
    assert result.returncode == 0


@pytest.mark.parametrize(
    "filename",
    [
        ".env.example",
        "helm/examples/application.env.example",
        "helm/examples/application-values.yaml",
        "app/android/installation.properties.example",
        "app/ios/Flutter/Installation.xcconfig.example",
        "app/ios/ExportOptions-TestFlight.plist.example",
    ],
)
def test_public_templates_remain_publishable(filename: str) -> None:
    assert not is_private_path(filename)
    result = subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "check-ignore", "--no-index", "-q", filename], cwd=ROOT, check=False
    )
    assert result.returncode == 1


def test_force_added_private_files_are_rejected(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "init", "-q", str(tmp_path)], check=True
    )
    (tmp_path / ".gitignore").write_text("*.env\n")
    (tmp_path / "application.env").write_text("INSTALLATION_LABEL=example\n")
    subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "add", "--force", "application.env"], cwd=tmp_path, check=True
    )
    assert source_issues(tmp_path) == ["application.env: private installation path"]


def test_ignored_installation_files_are_not_read(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "init", "-q", str(tmp_path)], check=True
    )
    (tmp_path / ".gitignore").write_text("*.env\n")
    (tmp_path / "application.env").write_text("INSTALLATION_LABEL=example\n")
    assert source_issues(tmp_path) == []


@pytest.mark.parametrize(
    "registry",
    [
        "https://pypi.org/simple",
        "https://user:password@index.example.test/simple",  # pragma: allowlist secret - redaction fixture
    ],
)
def test_private_registry_errors_do_not_disclose_credentials(tmp_path: Path, registry: str) -> None:
    subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "init", "-q", str(tmp_path)], check=True
    )
    (tmp_path / "uv.lock").write_text(f'[[package]]\nname = "example"\nsource = {{ registry = "{registry}" }}\n')
    issues = source_issues(tmp_path)
    assert bool(issues) == ("example.test" in registry)
    assert "password" not in "\n".join(issues)


def test_source_symlinks_cannot_export_private_files(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603 - resolved Git executable and synthetic fixture paths
        [GIT, "init", "-q", str(tmp_path)], check=True
    )
    (tmp_path / "public.txt").symlink_to("/private/installation.env")
    assert source_issues(tmp_path) == ["public.txt: source exports require regular files"]


def test_deleted_working_copy_cannot_hide_an_indexed_private_file(tmp_path: Path) -> None:
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)  # noqa: S603 - synthetic repository
    path = tmp_path / "application.env"
    path.write_text("INSTALLATION_LABEL=example\n")
    subprocess.run([GIT, "add", "--force", "application.env"], cwd=tmp_path, check=True)  # noqa: S603 - fixture path
    path.unlink()
    assert source_issues(tmp_path) == ["application.env: private installation path"]


def test_safe_working_copy_cannot_hide_an_indexed_symlink(tmp_path: Path) -> None:
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)  # noqa: S603 - synthetic repository
    path = tmp_path / "public.txt"
    path.symlink_to("/private/installation.env")
    subprocess.run([GIT, "add", "public.txt"], cwd=tmp_path, check=True)  # noqa: S603 - fixture path
    path.unlink()
    path.write_text("ordinary source")
    assert source_issues(tmp_path) == ["public.txt: source exports require regular files"]


def test_safe_working_copy_cannot_hide_an_indexed_private_lock(tmp_path: Path) -> None:
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)  # noqa: S603 - synthetic repository
    path = tmp_path / "uv.lock"
    path.write_text('[[package]]\nsource = { registry = "https://index.example.test/simple" }\n')
    subprocess.run([GIT, "add", "uv.lock"], cwd=tmp_path, check=True)  # noqa: S603 - fixture path
    path.write_text('[[package]]\nsource = { registry = "https://pypi.org/simple" }\n')
    assert source_issues(tmp_path) == ["uv.lock: dependencies must use the public PyPI index"]


@pytest.mark.parametrize("kind", ["sdist", "wheels"])
def test_public_registry_cannot_hide_a_private_download_url(kind: str) -> None:
    header = f"[package.{kind}]" if kind == "sdist" else f"[[package.{kind}]]"
    content = (
        '[[package]]\nsource = { registry = "https://pypi.org/simple" }\n'
        f'{header}\nurl = "https://downloads.example.test/package.whl"\n'
    )
    assert lock_issues(content.encode()) == ["uv.lock: package artifacts must use public PyPI download URLs"]


def test_direct_private_sources_are_rejected_without_disclosing_the_url() -> None:
    content = b'[[package]]\nsource = { git = "https://git.example.test/project" }\n'
    assert lock_issues(content) == ["uv.lock: dependencies must use the public PyPI index"]


@pytest.mark.parametrize("staged", [False, True])
def test_lock_symlinks_are_rejected_without_reading_private_targets(tmp_path: Path, staged: bool) -> None:
    subprocess.run([GIT, "init", "-q", str(tmp_path)], check=True)  # noqa: S603 - synthetic repository
    (tmp_path / ".gitignore").write_text("private/\n")
    (tmp_path / "private").mkdir()
    (tmp_path / "private/target.toml").write_text("deliberately invalid private TOML")
    (tmp_path / "uv.lock").symlink_to("private/target.toml")
    if staged:
        subprocess.run([GIT, "add", "uv.lock"], cwd=tmp_path, check=True)  # noqa: S603 - fixture path
    assert source_issues(tmp_path) == ["uv.lock: source exports require regular files"]
