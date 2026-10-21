"""The few things unripe passes around: a locked package, and what it found out about it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# the registries unripe can ask for a publish time
ECOSYSTEMS = ("npm", "pypi", "cargo", "go", "composer", "rubygems")


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

    @property
    def age_days(self) -> float | None:
        return None if self.age_seconds is None else self.age_seconds / 86400
