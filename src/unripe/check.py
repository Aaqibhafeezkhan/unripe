"""
The check itself: find lockfiles, read them, ask when each version was
published, and compare that with the policy.
"""

from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from unripe import lockfiles
from unripe.model import REGISTRY, STATES, Finding, Package
from unripe.policy import Policy
from unripe.registries import Missing, Registries, Unreachable

WORKERS = 16


class Problem(Exception):
    """Something that stops the check from running at all: a bad git ref, no git."""


@dataclass
class Result:
    findings: list[Finding]
    policy: Policy
    lockfiles: list[str] = field(default_factory=list)
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    since: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def count(self, state: str) -> int:
        return sum(1 for finding in self.findings if finding.state == state)

    def failing(self, finding: Finding) -> bool:
        return finding.state == "unripe" or (finding.state == "unknown" and self.policy.unknown == "fail")

    @property
    def exitcode(self) -> int:
        """0 clean, 1 unripe (or unknown under the strict default), 2 a lockfile that could not be read."""
        if self.unreadable or not self.lockfiles:
            return 2
        return 1 if any(self.failing(finding) for finding in self.findings) else 0


def display(path: Path) -> str:
    """A path as reports should show it: relative to here when it can be, always with forward slashes."""
    try:
        shown = os.path.relpath(path)
        if shown.startswith(".."):
            shown = str(path)
    except ValueError:  # another drive on windows
        shown = str(path)
    return shown.replace("\\", "/")


def line(rows: list[str], package: Package) -> int | None:
    """
    The line a package is locked on, for annotations, given the lockfile's
    lower cased lines. Most formats name the package and its version on one
    line (`name@1.2.3:`, `name (1.2.3)`); the rest put the version on a line
    just after the name, or for Pipfile.lock, further down.
    """
    names = {package.name.lower()}
    if package.ecosystem == "pypi":
        names |= {package.name.replace("-", "_"), package.name.replace("-", ".")}
    named = re.compile("|".join(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])" for name in names))
    # composer.lock keeps the `v` that unripe drops from versions
    version = re.compile(rf"(?<![\w.-])v?{re.escape(package.version.lower())}(?![\w.-])")
    hits = [number for number, row in enumerate(rows) if named.search(row)]
    # on one line: where the name comes first, so `/react@18.2.0:` beats `/react-dom@18.2.0(react@18.2.0):`
    same = [(named.search(rows[number]).start(), number) for number in hits if version.search(rows[number])]
    if same:
        return min(same)[1] + 1
    for window in (4, 40):
        for number in hits:
            if any(version.search(row) for row in rows[number:number + window]):
                return number + 1
    return hits[0] + 1 if hits else None


class Git:
    """Reads lockfiles as they were at an earlier commit."""

    def __init__(self, ref: str):
        self.ref = ref
        self.checked: set[str] = set()

    def run(self, directory: Path, *args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, timeout=60)
        except FileNotFoundError as error:
            raise Problem("--since needs git, and git was not found") from error
        except subprocess.TimeoutExpired as error:
            raise Problem(f"git took too long in {directory}") from error

    def show(self, path: Path) -> str | None:
        """The file's text at the ref, or None when it did not exist there."""
        directory = path.parent.resolve()
        if str(directory) not in self.checked:
            done = self.run(directory, "rev-parse", "--verify", "--quiet", f"{self.ref}^{{commit}}")
            if done.returncode != 0:
                raise Problem(
                    f"--since {self.ref}: not a commit in {display(directory)}. "
                    "in CI, fetch more history (actions/checkout with fetch-depth: 0)"
                )
            self.checked.add(str(directory))
        done = self.run(directory, "show", f"{self.ref}:./{path.name}")
        if done.returncode != 0:
            return None
        return done.stdout.decode("utf-8-sig", errors="replace")


def judge(
    package: Package,
    policy: Policy,
    registries: Registries,
    now: datetime,
    where: tuple[tuple[str, int | None], ...] = (),
) -> Finding:
    """What the policy says about one locked version."""
    rule = policy.excluded(package.ecosystem, package.name, package.version)
    if rule:
        return Finding(package, "excluded", reason=f"matches exclude {rule}", locations=where)
    registry = REGISTRY.get(package.ecosystem, package.ecosystem)
    try:
        published = registries.published(package)
    except Missing:
        return Finding(package, "unknown", reason=f"not found on {registry}", locations=where)
    except Unreachable as error:
        return Finding(package, "unknown", reason=f"{registry} did not answer ({error})", locations=where)
    except Exception as error:  # a registry answering nonsense is an unknown, never an ok
        return Finding(package, "unknown", reason=f"{registry} gave an answer unripe could not use ({error})", locations=where)
    age = (now - published).total_seconds()
    state = "ok" if age >= policy.min_age.total_seconds() else "unripe"
    return Finding(package, state, published, age, locations=where)


def check(
    paths: list[str],
    policy: Policy,
    registries: Registries,
    since: str | None = None,
    now: datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Result:
    now = now or datetime.now(timezone.utc)
    result = Result(findings=[], policy=policy, since=since, checked_at=now)
    git = Git(since) if since else None

    # every distinct version, with each lockfile it is locked in
    locked: dict[tuple[str, str, str], Package] = {}
    places: dict[tuple[str, str, str], list[str]] = {}
    texts: dict[str, str] = {}
    for path in lockfiles.find(paths):
        shown = display(path)
        result.lockfiles.append(shown)
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
            packages = lockfiles.read(text, shown)
            texts[shown] = text
        except OSError as error:
            result.unreadable.append((shown, f"{shown}: could not be opened ({error.strerror or error})"))
            continue
        except lockfiles.Unreadable as error:
            result.unreadable.append((shown, str(error)))
            continue
        if git:
            before = git.show(path)
            old: set[tuple[str, str, str]] = set()
            if before is not None:
                try:
                    old = {package.key for package in lockfiles.read(before, shown)}
                except lockfiles.Unreadable:
                    old = set()  # unreadable then: treat every version as new
            packages = [package for package in packages if package.key not in old]
        for package in packages:
            locked.setdefault(package.key, package)
            if shown not in places.setdefault(package.key, []):
                places[package.key].append(shown)

    findings: list[Finding] = []
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [
                pool.submit(judge, package, policy, registries, now, tuple((shown, None) for shown in places[package.key]))
                for package in locked.values()
            ]
            for done, future in enumerate(as_completed(futures), 1):
                findings.append(future.result())
                if progress:
                    progress(done, len(futures))
    finally:
        registries.cache.save()

    # line numbers only matter for what gets reported, and finding them is slow on big lockfiles
    rows: dict[str, list[str]] = {}
    for index, finding in enumerate(findings):
        if finding.state in ("unripe", "unknown"):
            spots = []
            for shown, _ in finding.locations:
                if shown not in rows:
                    rows[shown] = texts[shown].lower().splitlines()
                spots.append((shown, line(rows[shown], finding.package)))
            findings[index] = replace(finding, locations=tuple(spots))

    order = {state: index for index, state in enumerate(STATES)}
    findings.sort(key=lambda f: (order.get(f.state, 9), f.package.ecosystem, f.package.name.lower(), f.package.version))
    result.findings = findings
    return result
