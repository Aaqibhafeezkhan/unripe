"""NuGet's packages.lock.json."""

from __future__ import annotations

import json
import re

from unripe.model import Package

NUMBERS = re.compile(r"^(\d+(?:\.\d+){0,3})(-[0-9A-Za-z.-]+)?$")


def normal(version: str) -> str:
    """
    A version the way nuget.org files it: build metadata dropped, at least
    three parts, a fourth part only when it isn't zero, no leading zeros, and
    lower case. `1.0` and `1.0.0.0` are both `1.0.0`.
    """
    version = version.strip().split("+", 1)[0]
    match = NUMBERS.match(version)
    if not match:
        return version.lower()
    parts = [str(int(part)) for part in match.group(1).split(".")]
    parts += ["0"] * (3 - len(parts))
    if len(parts) == 4 and parts[3] == "0":
        parts = parts[:3]
    return ".".join(parts) + (match.group(2) or "").lower()


def nuget(text: str, lockfile: str) -> list[Package]:
    """
    packages.lock.json, versions 1 and 2. Every target framework has its own
    list; project references are local and skipped. The lockfile doesn't say
    which feed a package came from, so everything is looked up on nuget.org.
    """
    data = json.loads(text)
    found = []
    for packages in (data.get("dependencies") or {}).values():
        for name, entry in (packages or {}).items():
            if not isinstance(entry, dict) or entry.get("type") == "Project":
                continue
            resolved = str(entry.get("resolved") or "")
            if name and resolved:
                found.append(Package("nuget", name, normal(resolved), lockfile))
    return found
