"""Safely install a trusted APWorld into this MultiworldGG checkout."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
WORLDS_DIR = ROOT / "worlds"
MAX_SIZE = 100 * 1024 * 1024


def inspect_apworld(source: Path) -> tuple[str, dict]:
    if source.suffix.lower() != ".apworld":
        raise ValueError("The source file must end in .apworld")
    if not source.is_file():
        raise ValueError(f"File not found: {source}")
    if source.stat().st_size > MAX_SIZE:
        raise ValueError("APWorld is larger than the 100 MiB installation limit")

    try:
        with zipfile.ZipFile(source) as archive:
            members = [PurePosixPath(name) for name in archive.namelist() if name and not name.endswith("/")]
            unsafe = [name for name in members if name.is_absolute() or ".." in name.parts]
            if unsafe:
                raise ValueError(f"Archive contains an unsafe path: {unsafe[0]}")

            roots = {name.parts[0] for name in members if name.parts}
            if len(roots) != 1:
                raise ValueError("APWorld must contain exactly one top-level package directory")
            package = next(iter(roots))
            if not package.isidentifier():
                raise ValueError(f"Invalid Python package name: {package}")
            if PurePosixPath(package, "__init__.py") not in members:
                raise ValueError("APWorld package is missing __init__.py")

            manifest_path = PurePosixPath(package, "archipelago.json")
            if manifest_path not in members:
                raise ValueError("APWorld package is missing archipelago.json")
            manifest = json.loads(archive.read(str(manifest_path)))
    except zipfile.BadZipFile as exc:
        raise ValueError("File is not a valid ZIP-based APWorld") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("archipelago.json is not valid UTF-8 JSON") from exc

    if not isinstance(manifest, dict) or not manifest.get("game"):
        raise ValueError("archipelago.json must contain a non-empty game name")
    return package, manifest


def install(source: Path, replace: bool = False) -> Path:
    package, manifest = inspect_apworld(source)
    destination = WORLDS_DIR / f"{package.lower()}.apworld"
    if destination.exists() and not replace:
        raise FileExistsError(f"{destination.name} already exists; use --replace to update it")
    if (WORLDS_DIR / package).is_dir() or (WORLDS_DIR / package.lower()).is_dir():
        raise FileExistsError(f"A source world directory already exists for package {package}")

    with tempfile.NamedTemporaryFile(dir=WORLDS_DIR, prefix=".apworld-", delete=False) as temporary:
        temp_path = Path(temporary.name)
        with source.open("rb") as incoming:
            shutil.copyfileobj(incoming, temporary)
    try:
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)

    version = manifest.get("world_version") or manifest.get("version") or "unknown"
    print(f"Installed {manifest['game']} ({version}) as worlds/{destination.name}")
    print("Rebuild/restart MultiworldGG to load the new world.")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and install a trusted .apworld file into worlds/."
    )
    parser.add_argument("file", type=Path, help="path to the .apworld file")
    parser.add_argument("--replace", action="store_true", help="replace an installed APWorld")
    args = parser.parse_args()
    try:
        install(args.file.expanduser().resolve(), args.replace)
    except (ValueError, FileExistsError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
