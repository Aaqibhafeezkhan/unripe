"""
Lockfiles for app platforms and infrastructure: Dart and Flutter's
pubspec.lock, CocoaPods' Podfile.lock, Swift's Package.resolved and
Terraform's .terraform.lock.hcl. Read line by line or as JSON, without a
YAML or HCL library.
"""

from __future__ import annotations

import json
import re

from unripe.model import Package

PUB_HOSTS = ("https://pub.dev", "https://pub.dartlang.org")
# what CocoaPods has called its public specs repository over the years
TRUNK = ("trunk", "https://github.com/cocoapods/specs.git", "https://github.com/cocoapods/specs", "https://cdn.cocoapods.org/")
GITHUB = re.compile(r"^(?:https?://|git@|ssh://git@)github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)


def unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def pubspec(text: str, lockfile: str) -> list[Package]:
    """
    pubspec.lock: packages hosted on pub.dev. SDK packages (flutter itself),
    git, path and other hosts are skipped.
    """
    found = []
    entries: list[dict[str, str]] = []
    inside = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "packages:"
            continue
        if not inside:
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        if indent == 2:
            entries.append({"key": unquote(key)})
        elif entries and indent == 4:
            entries[-1][key] = unquote(value)
        elif entries and indent == 6 and key in ("name", "url"):
            entries[-1][f"description.{key}"] = unquote(value)
    for entry in entries:
        if entry.get("source") != "hosted" or not entry.get("version"):
            continue
        host = entry.get("description.url", PUB_HOSTS[0]).rstrip("/")
        if host not in PUB_HOSTS:
            continue
        found.append(Package("pub", entry.get("description.name") or entry["key"], entry["version"], lockfile))
    return found


def podfile(text: str, lockfile: str) -> list[Package]:
    """
    Podfile.lock: pods from the public CocoaPods specs repository. Subspecs
    (`Firebase/Analytics`) are their root pod. Pods from a path, git or a
    private specs repository are skipped.
    """
    section = repo = None
    locked: dict[str, str] = {}
    trunk: set[str] = set()
    elsewhere: set[str] = set()
    repos_listed = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith(" "):
            section = line.strip().rstrip(":")
            repos_listed = repos_listed or section == "SPEC REPOS"
            continue
        if section == "PODS" and line.startswith("  - "):
            match = re.match(r'^"?([^\s"(]+)"? \(([^)]+)\)', line[4:].strip())
            if match:
                locked.setdefault(match.group(1).split("/")[0], match.group(2))
        elif section == "SPEC REPOS":
            if line.startswith("  ") and not line.startswith("   "):
                repo = unquote(line.strip().rstrip(":")).lower()
            elif line.startswith("    - "):
                name = unquote(line[6:].strip())
                (trunk if repo in TRUNK else elsewhere).add(name)
        elif section in ("EXTERNAL SOURCES", "CHECKOUT OPTIONS") and line.startswith("  ") and not line.startswith("   "):
            elsewhere.add(unquote(line.strip().rstrip(":")))
    found = []
    for name, version in locked.items():
        # lockfiles from before CocoaPods 1.7 have no SPEC REPOS: anything not from elsewhere is trunk
        if name in elsewhere or (repos_listed and name not in trunk):
            continue
        found.append(Package("cocoapods", name, version, lockfile))
    return found


def swiftresolved(text: str, lockfile: str) -> list[Package]:
    """
    Package.resolved, versions 1 to 3: packages that live on GitHub, at their
    version, or at their commit when pinned to a branch or revision.
    """
    data = json.loads(text)
    pins = data.get("pins") if isinstance(data, dict) else None
    if pins is None and isinstance(data, dict):
        pins = (data.get("object") or {}).get("pins")  # version 1
    found = []
    for pin in pins or []:
        if not isinstance(pin, dict):
            continue
        match = GITHUB.match(str(pin.get("location") or pin.get("repositoryURL") or ""))
        state = pin.get("state") or {}
        if not match or not isinstance(state, dict):
            continue
        ref = state.get("version") or state.get("revision")
        if ref:
            found.append(Package("swift", f"{match.group(1)}/{match.group(2)}", str(ref), lockfile))
    return found


def terraform(text: str, lockfile: str) -> list[Package]:
    """
    .terraform.lock.hcl: providers from the public Terraform registry. Other
    registries, including OpenTofu's, are skipped.
    """
    found = []
    source = None
    for line in text.splitlines():
        start = re.match(r'^\s*provider\s+"([^"]+)"\s*\{', line)
        if start:
            source = start.group(1).lower()
            continue
        version = re.match(r'^\s*version\s*=\s*"([^"]+)"', line)
        if version and source:
            host, _, name = source.partition("/")
            if host == "registry.terraform.io" and name.count("/") == 1:
                found.append(Package("terraform", name, version.group(1), lockfile))
            source = None
    return found
