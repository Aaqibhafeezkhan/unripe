"""
npm, pnpm, yarn and bun lockfiles.

Everything here is parsed without a YAML library: pnpm and yarn lockfiles are
regular enough to read line by line, and keeping unripe free of dependencies
matters more than keeping these parsers short.
"""

from __future__ import annotations

import json
import re

from unripe.model import Package

SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")


def semver(version: str) -> bool:
    return bool(SEMVER.match(version or ""))


def split(spec: str) -> tuple[str, str]:
    """`name@version` or `@scope/name@version` into its two halves."""
    at = spec.rfind("@")
    if at <= 0:
        return spec, ""
    return spec[:at], spec[at + 1:]


def package(name: str, version: str, lockfile: str) -> Package | None:
    # an npm alias locks the real package under another name: `npm:real@1.2.3`
    if version.startswith("npm:"):
        name, version = split(version[4:])
    if not name or not semver(version):
        return None
    return Package("npm", name, version, lockfile)


def packagelock(text: str, lockfile: str) -> list[Package]:
    """package-lock.json and npm-shrinkwrap.json, versions 1, 2 and 3."""
    data = json.loads(text)
    found: list[Package] = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for key, entry in packages.items():
            if not key or "node_modules/" not in key or not isinstance(entry, dict):
                continue
            if entry.get("link") or entry.get("inBundle"):
                continue
            resolved = str(entry.get("resolved") or "")
            if resolved and not resolved.startswith(("http://", "https://")):
                continue
            name = entry.get("name") or key.rsplit("node_modules/", 1)[1]
            item = package(name, str(entry.get("version") or ""), lockfile)
            if item:
                found.append(item)
        return found

    # version 1 nests dependencies inside dependencies
    def walk(deps: dict) -> None:
        for name, entry in (deps or {}).items():
            if not isinstance(entry, dict):
                continue
            if not entry.get("bundled"):
                item = package(name, str(entry.get("version") or ""), lockfile)
                if item:
                    found.append(item)
            walk(entry.get("dependencies") or {})

    walk(data.get("dependencies") or {})
    return found


def pnpm(text: str, lockfile: str) -> list[Package]:
    """
    pnpm-lock.yaml, lockfile versions 5 to 9.

    Only the keys of the top-level `packages:` map are needed. They look like
    `/name/1.2.3` (v5), `/name@1.2.3` (v6) or `name@1.2.3` (v9), with peer
    suffixes like `_react@18.0.0` or `(react@18.0.0)` that are not part of the
    version.
    """
    found: list[Package] = []
    inside = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "packages:"
            continue
        if not inside or not line.startswith("  ") or line.startswith("   ") or not line.rstrip().endswith(":"):
            continue
        key = line.strip()[:-1].strip("'\"")
        if any(marker in key for marker in ("link:", "file:", "git+", "github.com/", "://")):
            continue
        key = key.lstrip("/").split("(", 1)[0]
        name, version = split(key)
        if not version or "/" in version:
            # v5 keys separate the version with a slash
            name, _, version = key.rpartition("/")
        version = version.split("_", 1)[0]
        item = package(name, version, lockfile)
        if item:
            found.append(item)
    return found


def yarn(text: str, lockfile: str) -> list[Package]:
    """yarn.lock from yarn 1 (classic) and yarn 2+ (berry)."""
    found: list[Package] = []
    spec = None
    fields: dict[str, str] = {}

    def flush() -> None:
        if spec is None:
            return
        resolution = fields.get("resolution", "")
        version = fields.get("version", "")
        if resolution:
            # berry: `name@npm:1.2.3`; anything else (workspace, patch, git) is not from npm
            name, _, rest = resolution.partition("@npm:")
            if not rest or name.startswith("@") and "/" not in name:
                return
            item = package(name, rest, lockfile)
        else:
            resolved = fields.get("resolved", "")
            name = ""
            if "/-/" in resolved:
                # https://registry.yarnpkg.com/@scope/name/-/name-1.2.3.tgz
                name = resolved.split("/-/", 1)[0].split("/", 3)[-1]
            if not name:
                first = spec.split(",", 1)[0].strip().strip("\"'")
                if "@npm:" in first:
                    first = first.split("@npm:", 1)[1]
                name = split(first)[0]
            if resolved and not resolved.startswith(("http://", "https://")):
                return
            item = package(name, version, lockfile)
        if item:
            found.append(item)

    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if not line.startswith(" "):
            flush()
            spec = line.rstrip().rstrip(":")
            fields = {}
            if spec.strip("\"'") == "__metadata":
                spec = None
            continue
        if spec is None or line.startswith("    "):
            continue
        stripped = line.strip()
        if stripped.endswith(":"):
            continue
        if ":" in stripped and not stripped.startswith(("version \"", "resolved \"")):
            field, _, value = stripped.partition(":")
        else:
            field, _, value = stripped.partition(" ")
        fields[field.strip()] = value.strip().strip("\"'")
    flush()
    return found


def jsonc(text: str) -> str:
    """JSON with comments and trailing commas, made plain JSON. Strings are left alone."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                i += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def bun(text: str, lockfile: str) -> list[Package]:
    """bun.lock, bun's text lockfile. The binary bun.lockb is not readable and is reported as such."""
    data = json.loads(jsonc(text))
    found: list[Package] = []
    for entry in (data.get("packages") or {}).values():
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], str):
            continue
        name, version = split(entry[0])
        if ":" in version:
            # workspace:, github:, git+, file: and link: are not from the registry
            continue
        item = package(name, version, lockfile)
        if item:
            found.append(item)
    return found
