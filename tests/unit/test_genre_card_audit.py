from pathlib import Path


def test_sha_bound_genre_sources_have_portable_line_endings() -> None:
    attributes = Path(".gitattributes").read_text(encoding="utf-8").splitlines()

    assert "docs/story-contracts.md text eol=lf" in attributes
    assert "configs/genre-quality-cards/** text eol=lf" in attributes
