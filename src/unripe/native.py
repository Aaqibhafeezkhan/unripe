"""
The cooldown settings package managers and update bots have of their own.

unripe checks what got locked; these stop a new version at install or update
time. `unripe config` reads them so the two can agree, and says which ones a
repository could turn on.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

ISO = re.compile(r"^P(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$", re.IGNORECASE)
WORDS = re.compile(r"^(\d+(?:\.\d+)?)\s*(minutes?|mins?|m|hours?|h|days?|d|weeks?|w)$", re.IGNORECASE)
UNIT = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


@dataclass(frozen=True)
class Setting:
    """One cooldown setting found in a file, or the hint for one that could be added."""

    tool: str
    file: str
    name: str
    age: timedelta | None  # None when it is set to something that is not a duration, like a fixed date
    raw: str = ""


@dataclass(frozen=True)
class Hint:
    """A setting a repository could add, because it uses that package manager and has none."""

    tool: str
    file: str
    line: str


def spelled(text: str) -> timedelta | None:
    """A duration as package managers spell them: `P7D`, `7 days`, `3d`, `1w`."""
    text = str(text).strip().strip("\"'")
    match = ISO.match(text)
    if match and any(match.groups()):
        weeks, days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
        return timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    match = WORDS.match(text)
    if match:
        return timedelta(seconds=float(match.group(1)) * UNIT[match.group(2)[0].lower()])
    return None


def number(text: str, unit: timedelta) -> timedelta | None:
    try:
        return float(str(text).strip().strip("\"'")) * unit
    except ValueError:
        return None


def ini(path: Path, key: str) -> str | None:
    """A `key=value` line from an .npmrc-like file."""
    for row in path.read_text("utf-8", errors="replace").splitlines():
        name, equals, value = row.partition("=")
        if equals and name.strip() == key and not row.lstrip().startswith((";", "#")):
            return value.strip()
    return None


def yaml(path: Path, key: str) -> str | None:
    """A top level `key: value` from a small YAML file, without a YAML parser."""
    for row in path.read_text("utf-8", errors="replace").splitlines():
        match = re.match(rf"^{re.escape(key)}\s*:\s*(.+?)\s*(#.*)?$", row)
        if match:
            return match.group(1)
    return None


def toml(path: Path, *keys: str):
    try:
        value = tomllib.loads(path.read_text("utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return None
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def find(directory: Path) -> list[Setting]:
    """Every cooldown setting in a repository's own files. Settings in home directories are not read."""
    found: list[Setting] = []

    def add(tool: str, file: str, name: str, value, age: timedelta | None) -> None:
        if value is not None:
            found.append(Setting(tool, file, name, age, str(value)))

    def at(name: str) -> Path | None:
        path = directory / name
        return path if path.is_file() else None

    if path := at(".npmrc"):
        value = ini(path, "min-release-age")
        add("npm", ".npmrc", "min-release-age", value, number(value, timedelta(days=1)) if value else None)
    if path := at("pnpm-workspace.yaml"):
        value = yaml(path, "minimumReleaseAge")
        add("pnpm", "pnpm-workspace.yaml", "minimumReleaseAge", value, number(value, timedelta(minutes=1)) if value else None)
    if path := at(".yarnrc.yml"):
        value = yaml(path, "npmMinimalAgeGate")
        if value:
            add("yarn", ".yarnrc.yml", "npmMinimalAgeGate", value, spelled(value) or number(value, timedelta(minutes=1)))
    if path := at("bunfig.toml"):
        value = toml(path, "install", "minimumReleaseAge")
        add("bun", "bunfig.toml", "minimumReleaseAge", value, number(value, timedelta(seconds=1)) if value is not None else None)
    for name, keys in (("pyproject.toml", ("tool", "uv", "exclude-newer")), ("uv.toml", ("exclude-newer",))):
        if path := at(name):
            value = toml(path, *keys)
            add("uv", name, "exclude-newer", value, spelled(value) if isinstance(value, str) else None)
    if path := at("poetry.toml"):
        value = toml(path, "solver", "min-release-age")
        add("poetry", "poetry.toml", "solver.min-release-age", value, number(value, timedelta(days=1)) if value is not None else None)
    for name in ("deno.json", "deno.jsonc"):
        if path := at(name):
            text = path.read_text("utf-8", errors="replace")
            # a duration, minutes, a date, or {"age": ..., "exclude": [...]}
            match = re.search(r'"minimumDependencyAge"\s*:\s*(\{[^}]*\}|"[^"]*"|[^,}\s]+)', text)
            if match:
                value = match.group(1)
                if value.startswith("{"):
                    inner = re.search(r'"age"\s*:\s*("[^"]*"|[^,}\s]+)', value)
                    value = inner.group(1) if inner else value
                value = value.strip('"')
                add("deno", name, "minimumDependencyAge", value, spelled(value) or number(value, timedelta(minutes=1)))
    if path := at(".cargo/config.toml"):
        value = toml(path, "registry", "global-min-publish-age")
        add("cargo", ".cargo/config.toml", "registry.global-min-publish-age", value, spelled(value) if isinstance(value, str) else None)
    if path := at(".bundle/config"):
        value = yaml(path, "BUNDLE_COOLDOWN")
        add("bundler", ".bundle/config", "BUNDLE_COOLDOWN", value, number(value, timedelta(days=1)) if value else None)
    for name in (".github/dependabot.yml", ".github/dependabot.yaml"):
        if path := at(name):
            days = re.findall(r"^\s*default-days\s*:\s*(\d+)", path.read_text("utf-8", errors="replace"), re.MULTILINE)
            for value in sorted(set(days), key=int):
                add("dependabot", name, "cooldown.default-days", value, timedelta(days=int(value)))
    for name in ("renovate.json", ".github/renovate.json", ".renovaterc.json", ".renovaterc"):
        if path := at(name):
            try:
                data = json.loads(path.read_text("utf-8", errors="replace"))
            except ValueError:
                continue
            value = data.get("minimumReleaseAge") if isinstance(data, dict) else None
            add("renovate", name, "minimumReleaseAge", value, spelled(value) if isinstance(value, str) else None)
    return found


def hints(directory: Path, found: list[Setting], age: timedelta) -> list[Hint]:
    """The settings a repository could add, going by which lockfiles it has here."""
    have = {setting.tool for setting in found}
    days = max(1, round(age / timedelta(days=1)))
    out = []

    def uses(*names: str) -> bool:
        return any((directory / name).is_file() for name in names)

    if uses("package-lock.json", "npm-shrinkwrap.json") and "npm" not in have:
        out.append(Hint("npm", ".npmrc", f"min-release-age={days}"))
    if uses("pnpm-lock.yaml") and "pnpm" not in have:
        out.append(Hint("pnpm", "pnpm-workspace.yaml", f"minimumReleaseAge: {int(age.total_seconds() // 60)}"))
    if uses("yarn.lock") and uses(".yarnrc.yml") and "yarn" not in have:
        out.append(Hint("yarn", ".yarnrc.yml", f'npmMinimalAgeGate: "{days}d"'))
    if uses("bun.lock") and "bun" not in have:
        out.append(Hint("bun", "bunfig.toml", f"[install]\nminimumReleaseAge = {int(age.total_seconds())}"))
    if uses("uv.lock") and "uv" not in have:
        out.append(Hint("uv", "pyproject.toml", f'[tool.uv]\nexclude-newer = "{days} days"'))
    if uses("poetry.lock") and "poetry" not in have:
        out.append(Hint("poetry", "poetry.toml", f"[solver]\nmin-release-age = {days}"))
    if uses("deno.lock") and "deno" not in have:
        out.append(Hint("deno", "deno.json", f'"minimumDependencyAge": "P{days}D"'))
    if uses("Gemfile.lock") and "bundler" not in have:
        out.append(Hint("bundler", ".bundle/config", f'BUNDLE_COOLDOWN: "{days}"'))
    return out
