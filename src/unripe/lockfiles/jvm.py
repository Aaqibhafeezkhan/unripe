"""Gradle's dependency lockfiles."""

from __future__ import annotations

from unripe.model import Package


def gradle(text: str, lockfile: str) -> list[Package]:
    """
    gradle.lockfile (Gradle 6 and later): one `group:artifact:version=configurations`
    per line, plus an `empty=` line. The older per-configuration files in
    gradle/dependency-locks/ have the same coordinates without the `=` part.
    Packages are named `group:artifact`, as Maven repositories know them.
    """
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("empty="):
            continue
        coordinate = line.split("=", 1)[0]
        parts = coordinate.split(":")
        if len(parts) != 3 or not all(parts):
            continue
        group, artifact, version = parts
        found.append(Package("maven", f"{group}:{artifact}", version, lockfile))
    return found
