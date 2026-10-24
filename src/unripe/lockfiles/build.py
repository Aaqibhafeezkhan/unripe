"""
Build systems that pin their inputs: Nix's flake.lock, Bazel's
MODULE.bazel.lock, and the baseline a vcpkg.json manifest pins.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from unripe.model import Package

SHA = re.compile(r"^[0-9a-f]{40}$")
# where the NixOS channels publish each release of nixpkgs; the files never change once there
NIXOS = ("releases.nixos.org",)
GITHUB_ARCHIVE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/archive/([0-9a-f]{40})\.(?:tar\.gz|zip)$")
BCR = re.compile(r"^https://bcr\.bazel\.build/modules/([^/]+)/([^/]+)/MODULE\.bazel$")
VCPKG = ("https://github.com/microsoft/vcpkg", "https://github.com/microsoft/vcpkg.git")


def flake(text: str, lockfile: str) -> list[Package]:
    """
    flake.lock: inputs locked to a GitHub commit, and nixpkgs from a NixOS
    channel release. Inputs from other hosts, git servers or local paths
    are skipped.
    """
    data = json.loads(text)
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, dict):
        raise ValueError("not a flake lockfile")
    found: dict[tuple[str, str, str], Package] = {}
    for node in nodes.values():
        locked = node.get("locked") if isinstance(node, dict) else None
        if not isinstance(locked, dict):
            continue
        kind, rev = locked.get("type"), str(locked.get("rev") or "")
        name = ""
        if kind == "github" and locked.get("owner") and locked.get("repo") and SHA.match(rev):
            if str(locked.get("host") or "github.com") == "github.com":
                name = f"{locked['owner']}/{locked['repo']}"
        elif kind == "git" and SHA.match(rev):
            match = re.match(r"^(?:git\+)?https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", str(locked.get("url") or ""))
            if match:
                name = f"{match.group(1)}/{match.group(2)}"
        elif kind in ("tarball", "file"):
            url = str(locked.get("url") or "")
            archive = GITHUB_ARCHIVE.match(url)
            if archive:
                name, rev = f"{archive.group(1)}/{archive.group(2)}", archive.group(3)
            elif urllib.parse.urlsplit(url).hostname in NIXOS:
                # a channel release: dated by when the channel published it
                name, rev = url, rev or str(locked.get("narHash") or "")
        if name and rev:
            package = Package("nix", name, rev, lockfile)
            found.setdefault(package.key, package)
    return list(found.values())


def order(version: str) -> tuple:
    """Enough of Bazel's version ordering to pick the highest of several: numbers first, then the text."""
    return tuple(int(part) for part in re.findall(r"\d+", version)), version


def bazel(text: str, lockfile: str) -> list[Package]:
    """
    MODULE.bazel.lock (Bazel 7.2 and later): modules from the Bazel Central
    Registry. The lockfile lists every version Bazel read while resolving;
    it uses the highest one of each module, so that is the one checked.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a Bazel lockfile")
    hashes = data.get("registryFileHashes")
    if not isinstance(hashes, dict):
        if "moduleDepGraph" in data:
            raise ValueError("written by a Bazel older than 7.2, which does not record where modules came from")
        raise ValueError("not a Bazel lockfile")
    highest: dict[str, str] = {}
    for url in hashes:
        match = BCR.match(str(url))
        if match:
            name, version = match.group(1), match.group(2)
            if name not in highest or order(version) > order(highest[name]):
                highest[name] = version
    return [Package("bazel", name, version, lockfile) for name, version in sorted(highest.items())]


def vcpkg(text: str, lockfile: str) -> list[Package]:
    """
    vcpkg.json and vcpkg-configuration.json: the commit of microsoft/vcpkg the
    project takes every port's version from. Moving that baseline is how a
    project picks up new versions, so the baseline is what has to be old enough.
    """
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a vcpkg manifest")
    baselines = []
    if lockfile.replace("\\", "/").rsplit("/", 1)[-1] == "vcpkg.json":
        baselines.append(data.get("builtin-baseline"))
        data = data.get("vcpkg-configuration") if isinstance(data.get("vcpkg-configuration"), dict) else {}
    registry = data.get("default-registry")
    if isinstance(registry, dict):
        builtin = registry.get("kind") == "builtin"
        official = registry.get("kind") == "git" and str(registry.get("repository") or "").rstrip("/") in VCPKG
        if builtin or official:
            baselines.append(registry.get("baseline"))
    found = {str(sha) for sha in baselines if isinstance(sha, str) and SHA.match(sha)}
    return [Package("vcpkg", "microsoft/vcpkg", sha, lockfile) for sha in sorted(found)]
