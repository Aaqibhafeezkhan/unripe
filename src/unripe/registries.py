"""
When was a version published? Asked of each ecosystem's own public registry.

A publish time never changes once a version exists, so every answer is cached
on disk for good, and a second run on the same lockfile asks nothing.

A registry that cannot answer (down, rate limited, private package) is an
"unknown", never an "ok": failing open is exactly what an attacker wants.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from unripe import __version__
from unripe.model import Package

AGENT = f"unripe/{__version__} (+https://github.com/TexWoW/unripe)"
TIMEOUT = 20
TRIES = 4


class Missing(Exception):
    """The registry answered, and the version is not there."""


class Unreachable(Exception):
    """The registry could not be asked: network, rate limit or server error."""


def when(text: str) -> datetime:
    """An ISO timestamp as an aware datetime, whatever form the registry wrote it in."""
    stamp = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fetchtext(url: str, accept: str = "*/*") -> str:
    """GET a body as text, retrying the failures worth retrying. A 404 is an answer, not a failure."""
    delay = 1.0
    last = ""
    for attempt in range(TRIES):
        request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                raise Missing(url) from error
            last = f"HTTP {error.code}"
            if error.code not in (429, 500, 502, 503, 504):
                break
            retry = error.headers.get("Retry-After")
            if retry and retry.isdigit():
                delay = max(delay, min(float(retry), 60.0))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = str(getattr(error, "reason", error))
        if attempt < TRIES - 1:
            time.sleep(delay + random.random() * 0.5)
            delay *= 2
    raise Unreachable(f"{url}: {last}")


def fetch(url: str) -> dict | list:
    """GET some JSON. A body that is not JSON means the registry is not answering properly."""
    text = fetchtext(url, "application/json")
    try:
        return json.loads(text)
    except ValueError as error:
        raise Unreachable(f"{url}: answered with something that is not JSON") from error


class Cache:
    """Publish times on disk, one JSON file per ecosystem. Safe to delete at any time."""

    def __init__(self, directory: Path | None):
        self.directory = directory
        self.lock = threading.Lock()
        self.data: dict[str, dict[str, str]] = {}
        self.dirty: set[str] = set()

    def table(self, ecosystem: str) -> dict[str, str]:
        if ecosystem not in self.data:
            self.data[ecosystem] = {}
            if self.directory:
                try:
                    loaded = json.loads((self.directory / f"{ecosystem}.json").read_text("utf-8"))
                    if isinstance(loaded, dict):
                        self.data[ecosystem] = loaded
                except (OSError, ValueError):
                    pass
        return self.data[ecosystem]

    def get(self, package: Package) -> datetime | None:
        with self.lock:
            value = self.table(package.ecosystem).get(f"{package.name}@{package.version}")
        try:
            return when(value) if value else None
        except (TypeError, ValueError):
            return None

    def put(self, ecosystem: str, name: str, version: str, published: datetime) -> None:
        with self.lock:
            self.table(ecosystem)[f"{name}@{version}"] = published.isoformat()
            self.dirty.add(ecosystem)

    def save(self) -> None:
        if not self.directory:
            return
        with self.lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                for ecosystem in self.dirty:
                    target = self.directory / f"{ecosystem}.json"
                    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
                    temporary.write_text(json.dumps(self.data[ecosystem], sort_keys=True), "utf-8")
                    os.replace(temporary, target)
                self.dirty.clear()
            except OSError:
                pass


def cachedir() -> Path:
    if os.environ.get("UNRIPE_CACHE"):
        return Path(os.environ["UNRIPE_CACHE"])
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "unripe"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "unripe" / "cache"
    return Path.home() / ".cache" / "unripe"


def goescape(module: str) -> str:
    """The Go module proxy spells capital letters as `!` plus the lower case letter."""
    return "".join(f"!{c.lower()}" if c.isupper() else c for c in module)


def goproxy() -> str:
    """The first real proxy in GOPROXY, or the public one."""
    for entry in re.split(r"[,|]", os.environ.get("GOPROXY") or ""):
        entry = entry.strip()
        if entry and entry not in ("direct", "off"):
            return entry.rstrip("/")
    return "https://proxy.golang.org"


def crateindex(name: str) -> str:
    """Where a crate lives in the sparse index: `se/rd/serde`, `3/s/syn`, `2/cc`, `1/a`."""
    name = name.lower()
    if len(name) <= 2:
        return f"{len(name)}/{name}"
    if len(name) == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[:2]}/{name[2:4]}/{name}"


class Registries:
    """Looks publish times up, through the cache. Thread-safe: unripe asks many at once."""

    def __init__(self, cache: Cache, npmurl: str | None = None, pypiurl: str | None = None):
        self.cache = cache
        self.npmurl = (npmurl or os.environ.get("UNRIPE_NPM_REGISTRY") or "https://registry.npmjs.org").rstrip("/")
        self.pypiurl = (pypiurl or os.environ.get("UNRIPE_PYPI_URL") or "https://pypi.org").rstrip("/")
        self.lookups: dict[str, Callable[[Package], datetime]] = {
            "npm": self.npm,
            "pypi": self.pypi,
            "cargo": self.cargo,
            "go": self.go,
            "composer": self.composer,
            "rubygems": self.rubygems,
        }
        # registries whose one answer covers every version are asked once per package
        self.pending: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()

    def published(self, package: Package) -> datetime:
        if package.published:
            return package.published
        cached = self.cache.get(package)
        if cached:
            return cached
        lookup = self.lookups.get(package.ecosystem)
        if lookup is None:
            raise Missing(f"unripe has no registry for {package.ecosystem}")
        stamp = lookup(package)
        self.cache.put(package.ecosystem, package.name, package.version, stamp)
        return stamp

    def once(self, key: str) -> threading.Lock:
        with self.guard:
            return self.pending.setdefault(key, threading.Lock())

    def every(self, package: Package, fetchall: Callable[[], dict[str, datetime]]) -> datetime:
        """
        For a registry whose one answer covers every version of a package: fetch
        it once, cache every version's time, then answer for the version asked about.
        """
        with self.once(f"{package.ecosystem}:{package.name}"):
            cached = self.cache.get(package)
            if cached:
                return cached
            times = fetchall()
            for version, stamp in times.items():
                self.cache.put(package.ecosystem, package.name, version, stamp)
            if package.version not in times:
                raise Missing(f"{package} is not in the registry")
            return times[package.version]

    def npm(self, package: Package) -> datetime:
        def fetchall() -> dict[str, datetime]:
            data = fetch(f"{self.npmurl}/{urllib.parse.quote(package.name, safe='@')}")
            times = {}
            for version, stamp in ((data.get("time") if isinstance(data, dict) else None) or {}).items():
                if version in ("created", "modified"):
                    continue
                try:
                    times[version] = when(stamp)
                except (TypeError, ValueError, AttributeError):
                    continue
            return times

        return self.every(package, fetchall)

    def pypi(self, package: Package) -> datetime:
        name, version = urllib.parse.quote(package.name), urllib.parse.quote(package.version)
        data = fetch(f"{self.pypiurl}/pypi/{name}/{version}/json")
        files = data.get("urls") if isinstance(data, dict) else None
        stamps = [when(f["upload_time_iso_8601"]) for f in files or [] if f.get("upload_time_iso_8601")]
        if not stamps:
            raise Missing(f"{package} has no files on PyPI")
        # the first file uploaded is when the version became installable
        return min(stamps)

    def cargo(self, package: Package) -> datetime:
        def fetchall() -> dict[str, datetime]:
            # the sparse index is a CDN that carries every version's publish time,
            # and is what crates.io asks tools to use instead of its rate limited API
            body = fetchtext(f"https://index.crates.io/{crateindex(package.name)}")
            times = {}
            for line in body.splitlines():
                try:
                    entry = json.loads(line)
                    times[str(entry["vers"])] = when(entry["pubtime"])
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
            return times

        return self.every(package, fetchall)

    def go(self, package: Package) -> datetime:
        data = fetch(f"{goproxy()}/{goescape(package.name)}/@v/{goescape(package.version)}.info")
        if not isinstance(data, dict) or not data.get("Time"):
            raise Missing(f"{package} has no time in the module proxy")
        return when(data["Time"])

    def composer(self, package: Package) -> datetime:
        def fetchall() -> dict[str, datetime]:
            data = fetch(f"https://repo.packagist.org/p2/{package.name}.json")
            versions = ((data.get("packages") if isinstance(data, dict) else None) or {}).get(package.name) or []
            times = {}
            # p2 files are minified: each entry lists only what changed since the one before
            current: dict = {}
            for entry in versions:
                for key, value in entry.items():
                    if value == "__unset":
                        current.pop(key, None)
                    else:
                        current[key] = value
                if current.get("version") and current.get("time"):
                    try:
                        times[str(current["version"]).lstrip("v")] = when(str(current["time"]))
                    except ValueError:
                        continue
            return times

        return self.every(package, fetchall)

    def rubygems(self, package: Package) -> datetime:
        def fetchall() -> dict[str, datetime]:
            data = fetch(f"https://rubygems.org/api/v1/versions/{urllib.parse.quote(package.name)}.json")
            times = {}
            for entry in data if isinstance(data, list) else []:
                number, platform = entry.get("number"), entry.get("platform") or "ruby"
                if not number or not entry.get("created_at"):
                    continue
                # Gemfile.lock writes platform gems as `1.2.3-x86_64-linux`
                times[number if platform == "ruby" else f"{number}-{platform}"] = when(entry["created_at"])
            return times

        return self.every(package, fetchall)
