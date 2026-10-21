"""uv.lock, poetry.lock, Pipfile.lock and pinned requirements files."""

from __future__ import annotations

import json
import re
import tomllib

from unripe.model import Package

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*===?\s*([^\s;,\\#]+)")


def normal(name: str) -> str:
    """A PyPI name the way the registry files it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pypi(source) -> bool:
    """Whether a lockfile's source entry points at PyPI itself."""
    if not source:
        return True
    if isinstance(source, dict):
        registry = str(source.get("registry") or source.get("url") or "")
        return "pypi.org" in registry or "pythonhosted.org" in registry
    return False


def uv(text: str, lockfile: str) -> list[Package]:
    data = tomllib.loads(text)
    found = []
    for entry in data.get("package") or []:
        source = entry.get("source") or {}
        # editable, virtual, directory, git and url sources are not on PyPI
        if not isinstance(source, dict) or "registry" not in source or not pypi(source):
            continue
        if entry.get("name") and entry.get("version"):
            found.append(Package("pypi", normal(entry["name"]), str(entry["version"]), lockfile))
    return found


def poetry(text: str, lockfile: str) -> list[Package]:
    data = tomllib.loads(text)
    found = []
    for entry in data.get("package") or []:
        source = entry.get("source")
        if source and not (source.get("type") == "legacy" and pypi(source)):
            continue
        if entry.get("name") and entry.get("version"):
            found.append(Package("pypi", normal(entry["name"]), str(entry["version"]), lockfile))
    return found


def pipfile(text: str, lockfile: str) -> list[Package]:
    data = json.loads(text)
    found = []
    for group in ("default", "develop"):
        for name, entry in (data.get(group) or {}).items():
            if not isinstance(entry, dict) or any(key in entry for key in ("git", "path", "file", "editable")):
                continue
            version = str(entry.get("version") or "")
            if version.startswith("=="):
                found.append(Package("pypi", normal(name), version.lstrip("="), lockfile))
    return found


def requirements(text: str, lockfile: str) -> list[Package]:
    """Only exact pins (`name==1.2.3`) say what gets installed; ranges are skipped."""
    found = []
    # pip-compile output continues lines with a backslash before its --hash lines
    joined = re.sub(r"\\\r?\n", " ", text)
    for line in joined.splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")) or "://" in line or " @ " in line:
            continue
        match = PIN.match(line)
        if match:
            found.append(Package("pypi", normal(match.group(1)), match.group(2), lockfile))
    return found
