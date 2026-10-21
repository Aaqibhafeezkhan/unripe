"""The policy: how old a version must be, what to skip, and what an unknown means."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from unripe.model import ECOSYSTEMS

UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)
DEFAULT_AGE = timedelta(days=3)
CONFIGS = (".unripe.toml", "pyproject.toml")
KEYS = {"min-age", "exclude", "unknown", "paths", "maven-repositories"}


class PolicyError(ValueError):
    """A policy value or config file unripe cannot use."""


def duration(text: str | int | float) -> timedelta:
    """`90m`, `72h`, `3d` or `1w`. A bare number is a number of days."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        days = float(text)
    else:
        raw = str(text).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            days = float(raw)
        else:
            match = DURATION.match(raw)
            if not match:
                raise PolicyError(f"{text!r} is not a duration; use something like 90m, 72h, 3d or 1w")
            return timedelta(seconds=float(match.group(1)) * UNITS[match.group(2).lower()])
    return timedelta(days=days)


def spoken(age: timedelta) -> str:
    """A duration the way a person would say it: `3d`, `36h`, `90m`."""
    seconds = int(age.total_seconds())
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


@dataclass(frozen=True)
class Rule:
    """One exclude pattern: a name or glob, optionally scoped to one ecosystem (`npm:@types/*`)."""

    pattern: str
    ecosystem: str | None = None

    @classmethod
    def parse(cls, text: str) -> "Rule":
        text = text.strip()
        prefix, colon, rest = text.partition(":")
        if colon and prefix.lower() in ECOSYSTEMS and rest:
            return cls(rest.strip(), prefix.lower())
        return cls(text)

    def matches(self, ecosystem: str, name: str, version: str) -> bool:
        if self.ecosystem and self.ecosystem != ecosystem:
            return False
        pattern = self.pattern
        if "@" in pattern[1:]:
            # `name@version` pins the rule to one version
            at = pattern.rfind("@")
            if not fnmatch.fnmatchcase(version, pattern[at + 1:]):
                return False
            pattern = pattern[:at]
        candidate = name
        if ecosystem == "pypi":
            candidate, pattern = name.lower(), re.sub(r"[-_.]+", "-", pattern).lower()
        elif ecosystem == "nuget":
            # nuget ids ignore case
            candidate, pattern = name.lower(), pattern.lower()
        return fnmatch.fnmatchcase(candidate, pattern)


@dataclass
class Policy:
    min_age: timedelta = DEFAULT_AGE
    exclude: list[str] = field(default_factory=list)
    unknown: str = "fail"  # or "warn"
    paths: list[str] = field(default_factory=list)
    # extra Maven repositories to look in after the usual ones
    maven_repositories: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.unknown not in ("fail", "warn"):
            raise PolicyError(f"unknown must be fail or warn, not {self.unknown!r}")
        if self.min_age < timedelta(0):
            raise PolicyError("min-age cannot be negative")
        self.rules = [Rule.parse(text) for text in self.exclude if text.strip()]

    def excluded(self, ecosystem: str, name: str, version: str) -> str | None:
        """The exclude pattern that covers this package, if any."""
        for rule in self.rules:
            if rule.matches(ecosystem, name, version):
                return f"{rule.ecosystem}:{rule.pattern}" if rule.ecosystem else rule.pattern
        return None


def config(directory: Path | str = ".", path: Path | str | None = None) -> tuple[dict, Path | None]:
    """
    The settings from `.unripe.toml` (top level keys) or `[tool.unripe]` in
    pyproject.toml, looked for in `directory`. An explicit `path` must exist.
    """
    candidates = [Path(path)] if path else [Path(directory) / name for name in CONFIGS]
    for candidate in candidates:
        if not candidate.is_file():
            if path:
                raise PolicyError(f"{candidate}: no such config file")
            continue
        try:
            data = tomllib.loads(candidate.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise PolicyError(f"{candidate}: could not be read ({error})") from error
        if candidate.name == "pyproject.toml":
            data = data.get("tool", {}).get("unripe")
            if data is None:
                if path:
                    raise PolicyError(f"{candidate}: has no [tool.unripe] table")
                continue
        if not isinstance(data, dict):
            raise PolicyError(f"{candidate}: the unripe settings should be a table")
        unexpected = set(data) - KEYS
        if unexpected:
            raise PolicyError(f"{candidate}: unripe does not know {', '.join(sorted(unexpected))} (it knows {', '.join(sorted(KEYS))})")
        return data, candidate
    return {}, None


def build(settings: dict, min_age=None, exclude=(), unknown=None, source: Path | None = None) -> Policy:
    """A Policy from config settings, with command line values on top. Excludes from both apply."""
    where = f"{source}: " if source else ""

    def strings(key: str) -> list[str]:
        value = settings.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise PolicyError(f"{where}{key} should be a list of strings")
        return value

    try:
        age = duration(min_age if min_age is not None else settings.get("min-age", "3d"))
    except PolicyError as error:
        raise PolicyError(f"{where if min_age is None else ''}min-age: {error}") from error
    return Policy(
        min_age=age,
        exclude=strings("exclude") + list(exclude),
        unknown=unknown or str(settings.get("unknown", "fail")),
        paths=strings("paths"),
        maven_repositories=strings("maven-repositories"),
    )
