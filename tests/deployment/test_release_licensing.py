from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, TypeAdapter

ROOT = Path(__file__).resolve().parents[2]


class SourceArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    url: str
    sha256: str
    license: str


class LockedPackage(BaseModel):
    version: str | None = None


class PackageLock(BaseModel):
    packages: dict[str, LockedPackage]


@pytest.mark.parametrize(
    ("project", "package", "source"),
    [
        ("whatsapp-bridge", "baileys", "baileys"),
        ("whatsapp-bridge", "libsignal", "libsignal"),
        ("whatsapp-bridge", "@img/sharp-libvips-linux-arm64", "sharp-libvips"),
        ("whatsapp-bridge", "@img/sharp-libvips-linux-x64", "sharp-libvips"),
        ("helm/images/steel", "mupdf", "mupdf"),
    ],
)
def test_shipped_sources_correspond_to_locked_dependencies(project: str, package: str, source: str) -> None:
    sources = TypeAdapter(dict[str, SourceArchive]).validate_json((ROOT / "licenses/sources.json").read_bytes())
    lock = PackageLock.model_validate_json((ROOT / project / "package-lock.json").read_bytes())
    assert lock.packages[f"node_modules/{package}"].version == sources[source].version


def test_distributed_source_downloads_are_integrity_checked() -> None:
    sources = TypeAdapter(dict[str, SourceArchive]).validate_json((ROOT / "licenses/sources.json").read_bytes())
    recipes = (ROOT / "Dockerfile").read_text() + (ROOT / "helm/images/steel/Dockerfile").read_text()
    for source in sources.values():
        assert f"ADD --checksum=sha256:{source.sha256} {source.url} " in recipes
