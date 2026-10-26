from pathlib import Path

import orjson

from voxos.product_docs.validation import validate_wiki_docs, write_manifest


def test_wiki_validation_accepts_current_manifest(tmp_path: Path) -> None:
    docs_dir = tmp_path / "wiki"
    docs_dir.mkdir()
    (docs_dir / "Home.md").write_text(
        "# Home\n\nLast updated: 2026-07-05\n\n[Setup](Setup.md)\n",
        encoding="utf-8",
    )
    (docs_dir / "Setup.md").write_text(
        "# Setup\n\nLast updated: 2026-07-05\n",
        encoding="utf-8",
    )
    (docs_dir / "_Sidebar.md").write_text(
        "# Sidebar\n\nLast updated: 2026-07-05\n\n- [Home](Home.md)\n",
        encoding="utf-8",
    )
    write_manifest(docs_dir)

    assert validate_wiki_docs(docs_dir, check_manifest=True) == ()


def test_wiki_validation_reports_broken_link_and_stale_manifest(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "wiki"
    docs_dir.mkdir()
    home_path = docs_dir / "Home.md"
    home_path.write_text(
        "# Home\n\nLast updated: 2026-07-05\n\n[Missing](Missing.md)\n",
        encoding="utf-8",
    )
    (docs_dir / "_Sidebar.md").write_text(
        "# Sidebar\n\nLast updated: 2026-07-05\n\n- [Home](Home.md)\n",
        encoding="utf-8",
    )
    write_manifest(docs_dir)
    home_path.write_text(
        "# Home\n\nLast updated: 2026-07-05\n\n[Still missing](Missing.md)\n",
        encoding="utf-8",
    )

    errors = validate_wiki_docs(docs_dir, check_manifest=True)

    assert any("missing link target" in error for error in errors)
    assert any(error == "Manifest stale for page: Home.md" for error in errors)


def test_wiki_manifest_uses_short_source_fingerprint(tmp_path: Path) -> None:
    docs_dir = tmp_path / "wiki"
    docs_dir.mkdir()
    (docs_dir / "Home.md").write_text(
        "# Home\n\nLast updated: 2026-07-05\n",
        encoding="utf-8",
    )
    (docs_dir / "_Sidebar.md").write_text(
        "# Sidebar\n\nLast updated: 2026-07-05\n\n- [Home](Home.md)\n",
        encoding="utf-8",
    )

    manifest_path = write_manifest(docs_dir)
    manifest_json = orjson.loads(manifest_path.read_bytes())
    first_page = manifest_json["pages"][0]

    assert "source_hash" not in first_page
    assert len(first_page["source_fingerprint"]) == 19
    assert first_page["source_fingerprint"].count("-") == 3
    assert validate_wiki_docs(docs_dir, check_manifest=True) == ()
