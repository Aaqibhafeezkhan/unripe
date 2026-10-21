"""Finding lockfiles, and reading each one with the parser its name calls for."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from unripe.lockfiles import javascript, others, python
from unripe.model import Package

Parser = Callable[[str, str], list[Package]]

PARSERS: dict[str, Parser] = {
    "package-lock.json": javascript.packagelock,
    "npm-shrinkwrap.json": javascript.packagelock,
    "pnpm-lock.yaml": javascript.pnpm,
    "yarn.lock": javascript.yarn,
    "bun.lock": javascript.bun,
    "bun.lockb": javascript.bunbinary,
    "uv.lock": python.uv,
    "poetry.lock": python.poetry,
    "Pipfile.lock": python.pipfile,
    "Cargo.lock": others.cargo,
    "go.mod": others.gomod,
    "composer.lock": others.composer,
    "Gemfile.lock": others.gemfile,
}
REQUIREMENTS = re.compile(r"^(requirements|constraints)[\w.-]*\.txt$", re.IGNORECASE)
# never worth walking into: installed copies of dependencies, builds, caches, and go's test inputs
SKIP = {"node_modules", ".git", ".hg", ".venv", "venv", "env", "vendor", "target", "dist", "build", "__pycache__", ".tox", ".nox", ".cache", "testdata"}


class Unreadable(Exception):
    """A lockfile that exists but could not be parsed."""


def parser(path: str | Path) -> Parser | None:
    name = Path(path).name
    if name in PARSERS:
        return PARSERS[name]
    if REQUIREMENTS.match(name):
        return python.requirements
    return None


def find(paths: list[str]) -> list[Path]:
    """Every lockfile in the given files and directories, directories walked recursively."""
    found: list[Path] = []
    for raw in paths or ["."]:
        path = Path(raw)
        if path.is_file():
            if parser(path):
                found.append(path)
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for name in sorted(files):
                # bun 1.2 leaves the old binary lockfile next to the new text one
                if name == "bun.lockb" and "bun.lock" in files:
                    continue
                if parser(name):
                    found.append(Path(root) / name)
    return found


def read(text: str, path: str | Path) -> list[Package]:
    """The packages locked in one file's text. `path` decides the parser and is recorded on each package."""
    parse = parser(path)
    if parse is None:
        raise Unreadable(f"{path}: not a lockfile unripe knows")
    try:
        return parse(text, str(path).replace("\\", "/"))
    except Exception as error:  # a broken lockfile is reported, never mistaken for an empty one
        raise Unreadable(f"{path}: could not be read ({error})") from error
