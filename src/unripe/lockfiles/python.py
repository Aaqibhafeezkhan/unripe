"""uv.lock, pylock.toml, poetry.lock, Pipfile.lock and pinned requirements files."""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone

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
            found.append(Package("pypi", normal(entry["name"]), str(entry["version"]), lockfile, uploaded(entry)))
    return found


def uploaded(entry: dict) -> datetime | None:
    """uv.lock revision 3 and later records each file's upload time; the earliest is the publish time."""
    files = [entry.get("sdist")] + list(entry.get("wheels") or [])
    stamps = []
    for item in files:
        if isinstance(item, dict) and item.get("upload-time"):
            stamp = item["upload-time"]
            # pylock.toml writes a toml datetime, uv.lock a string
            if not isinstance(stamp, datetime):
                try:
                    stamp = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                except ValueError:
                    continue
            stamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc))
    return min(stamps) if stamps else None


def pylock(text: str, lockfile: str) -> list[Package]:
    """
    pylock.toml, the standard lockfile from PEP 751 (`pip lock`, `uv export
    --format pylock.toml`). Like uv.lock it can carry each file's upload time.
    """
    data = tomllib.loads(text)
    found = []
    for entry in data.get("packages") or []:
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("version"):
            continue
        # vcs, directory and archive packages are not from an index
        if any(key in entry for key in ("vcs", "directory", "archive")):
            continue
        files = [entry.get("sdist")] + list(entry.get("wheels") or [])
        urls = [str(item.get("url") or "") for item in files if isinstance(item, dict)]
        index = str(entry.get("index") or "")
        if index and not pypi({"url": index}):
            continue
        if not index and urls and not any(pypi({"url": url}) for url in urls):
            continue
        found.append(Package("pypi", normal(entry["name"]), str(entry["version"]), lockfile, uploaded(entry)))
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
