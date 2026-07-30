"""Helpers for checks that inspect text-based frontend assets."""
from pathlib import Path
from typing import Iterable, Iterator


TEXT_FRONTEND_SUFFIXES = frozenset({
    ".html", ".js", ".css", ".svg", ".json", ".webmanifest", ".xml", ".txt",
})


def frontend_text_assets(root: Path) -> Iterator[Path]:
    """Yield frontend files explicitly classified as UTF-8 text assets."""
    return (
        path for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in TEXT_FRONTEND_SUFFIXES
    )


def assert_no_frontend_secrets(root: Path, forbidden: Iterable[str]) -> None:
    """Check every text asset without attempting to decode binary assets."""
    contents = {
        path: path.read_text(encoding="utf-8")
        for path in frontend_text_assets(root)
    }
    for secret in forbidden:
        locations = [str(path) for path, text in contents.items() if secret in text]
        assert not locations, f"{secret!r} found in frontend assets: {locations}"
