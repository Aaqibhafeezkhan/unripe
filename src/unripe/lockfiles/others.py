"""Cargo.lock, go.mod, composer.lock and Gemfile.lock."""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime

from unripe.model import Package

CRATES = ("registry+https://github.com/rust-lang/crates.io-index", "sparse+https://index.crates.io/")


def cargo(text: str, lockfile: str) -> list[Package]:
    data = tomllib.loads(text)
    found = []
    for entry in data.get("package") or []:
        # workspace members have no source; git and path crates are not on crates.io
        if entry.get("source") in CRATES and entry.get("name") and entry.get("version"):
            found.append(Package("cargo", entry["name"], str(entry["version"]), lockfile))
    return found


GOLINE = re.compile(r"^\s*([^\s()]+)\s+(v[^\s]+)")


def gomod(text: str, lockfile: str) -> list[Package]:
    """
    go.mod rather than go.sum: since go 1.17 go.mod lists every module in the
    build, while go.sum also keeps old versions that are never built.
    """
    required: dict[str, str] = {}
    replaced: dict[str, tuple[str, str] | None] = {}
    block = None
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].rstrip()
        if not line.strip():
            continue
        if block:
            if line.strip() == ")":
                block = None
                continue
            body = line
        else:
            words = line.split(None, 1)
            if words[0] in ("require", "replace") and len(words) > 1:
                if words[1].strip() == "(":
                    block = words[0]
                    continue
                block_word, body = words[0], words[1]
                handle(block_word, body, required, replaced)
            continue
        handle(block, body, required, replaced)
    found = []
    for module, version in required.items():
        if module in replaced:
            target = replaced[module]
            if target is None:
                continue
            module, version = target
        found.append(Package("go", module, version, lockfile))
    return found


def handle(kind: str, body: str, required: dict, replaced: dict) -> None:
    if kind == "require":
        match = GOLINE.match(body)
        if match:
            required[match.group(1)] = match.group(2)
        return
    left, arrow, right = body.partition("=>")
    if not arrow:
        return
    module = left.split()[0]
    target = right.split()
    if not target or target[0].startswith((".", "/")) or len(target) < 2:
        # replaced by a local directory: nothing to look up
        replaced[module] = None
    else:
        replaced[module] = (target[0], target[1])


def composer(text: str, lockfile: str) -> list[Package]:
    """composer.lock records each package's release time itself, so no registry is needed."""
    data = json.loads(text)
    found = []
    for group in ("packages", "packages-dev"):
        for entry in data.get(group) or []:
            name, version = entry.get("name"), str(entry.get("version") or "")
            if not name or not version or version.startswith("dev-"):
                continue
            published = None
            if entry.get("time"):
                try:
                    published = datetime.fromisoformat(str(entry["time"]))
                except ValueError:
                    published = None
            found.append(Package("composer", name, version.lstrip("v"), lockfile, published))
    return found


def gemfile(text: str, lockfile: str) -> list[Package]:
    """Gems under a `GEM` section whose remote is rubygems.org. Platform suffixes are kept for the lookup."""
    found = []
    section = remote = None
    specs = False
    for line in text.splitlines():
        if line and not line.startswith(" "):
            section, remote, specs = line.strip(), None, False
            continue
        if section != "GEM":
            continue
        stripped = line.strip()
        if line.startswith("  remote:"):
            remote = stripped.split(":", 1)[1].strip()
        elif line.startswith("  specs:"):
            specs = True
        elif specs and line.startswith("    ") and not line.startswith("     "):
            match = re.match(r"^(\S+) \(([^)]+)\)$", stripped)
            if match and remote and "rubygems.org" in remote:
                found.append(Package("rubygems", match.group(1), match.group(2), lockfile))
    return found
