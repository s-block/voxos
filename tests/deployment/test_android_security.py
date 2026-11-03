"""Protect Android release policies while retaining local debug networking."""

from pathlib import Path
from xml.etree import ElementTree

import pytest

ANDROID_SOURCE = Path(__file__).resolve().parents[2] / "app/android/app/src"
ANDROID = "{http://schemas.android.com/apk/res/android}"
BACKUP_DOMAINS = {
    "root",
    "file",
    "database",
    "sharedpref",
    "external",
    "device_root",
    "device_file",
    "device_database",
    "device_sharedpref",
}


def android_application() -> ElementTree.Element:
    manifest = ElementTree.parse(ANDROID_SOURCE / "main/AndroidManifest.xml")  # noqa: S314 - repository-owned Android manifest
    element = manifest.getroot().find("application")
    assert element is not None
    return element


def policy_resource(attribute: str) -> str:
    reference = android_application().get(ANDROID + attribute)
    assert reference is not None, f"Android must declare {attribute} explicitly"
    assert reference.startswith("@xml/")
    return reference.removeprefix("@xml/") + ".xml"


@pytest.mark.parametrize("variant, cleartext", [("main", "false"), ("debug", "true")])
def test_android_network_policy(variant: str, cleartext: str) -> None:
    assert android_application().get(ANDROID + "usesCleartextTraffic") == "false"
    resource = ANDROID_SOURCE / variant / "res/xml" / policy_resource("networkSecurityConfig")
    policy = ElementTree.parse(resource).getroot()  # noqa: S314 - repository-owned Android resource
    base = policy.find("base-config")
    assert base is not None
    assert base.get("cleartextTrafficPermitted") == cleartext
    for domain in policy.iter("domain-config"):
        assert domain.get("cleartextTrafficPermitted", cleartext) == cleartext


@pytest.mark.parametrize("transport", ["legacy", "cloud-backup", "device-transfer"])
def test_android_backup_excludes_every_app_storage_domain(transport: str) -> None:
    assert android_application().get(ANDROID + "allowBackup") == "false"
    attribute = "fullBackupContent" if transport == "legacy" else "dataExtractionRules"
    resource = ANDROID_SOURCE / "main/res/xml" / policy_resource(attribute)
    policy = ElementTree.parse(resource).getroot()  # noqa: S314 - repository-owned Android resource
    scope = policy if transport == "legacy" else policy.find(transport)
    assert scope is not None
    excluded = {entry.get("domain") for entry in scope.findall("exclude") if entry.get("path") == "."}
    assert excluded >= BACKUP_DOMAINS
    assert not scope.findall("include")
