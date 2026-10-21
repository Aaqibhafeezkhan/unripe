"""The few things unripe passes around: a locked package, and what it found out about it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# the registries unripe can ask for a publish time
ECOSYSTEMS = ("npm", "pypi", "cargo", "go", "composer", "rubygems", "nuget", "maven")
REGISTRY = {
    "npm": "the npm registry",
    "pypi": "PyPI",
    "cargo": "crates.io",
    "go": "the Go module proxy",
    "composer": "Packagist",
    "rubygems": "rubygems.org",
    "nuget": "nuget.org",
    "maven": "the Maven repositories (add more with maven-repositories)",
}
STATES = ("unripe", "unknown", "ok", "excluded")


@dataclass(frozen=True, order=True)
class Package:
    """One locked version of one package, and the lockfile it came from."""

    ecosystem: str
    name: str
    version: str
    lockfile: str = field(default="", compare=False)
    # some lockfiles carry the publish time themselves (composer.lock does)
    published: datetime | None = field(default=None, compare=False, hash=False)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.ecosystem, self.name, self.version

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Finding:
    """
    What unripe concluded about one package.

    `state` is one of:
    - "ok": published long enough ago
    - "unripe": published more recently than the policy allows
    - "unknown": the registry could not say. never quietly treated as ok
    - "excluded": skipped by an exclude rule
    """

    package: Package
    state: str
    published: datetime | None = None
    age_seconds: float | None = None
    reason: str = ""
    # every lockfile that locks this version, with the line it is on when known
    locations: tuple[tuple[str, int | None], ...] = ()

    @property
    def age_days(self) -> float | None:
        return None if self.age_seconds is None else self.age_seconds / 86400
