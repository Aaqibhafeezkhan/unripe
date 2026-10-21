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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

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


def fetch(url: str) -> dict | list:
    """GET some JSON, retrying the failures worth retrying. A 404 is an answer, not a failure."""
    delay = 1.0
    last = ""
    for attempt in range(TRIES):
        request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
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
                    self.data[ecosystem] = json.loads((self.directory / f"{ecosystem}.json").read_text("utf-8"))
                except (OSError, ValueError):
                    pass
        return self.data[ecosystem]

    def get(self, package: Package) -> datetime | None:
        with self.lock:
            value = self.table(package.ecosystem).get(f"{package.name}@{package.version}")
        return when(value) if value else None

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
                    temporary = target.with_suffix(".tmp")
                    temporary.write_text(json.dumps(self.data[ecosystem], sort_keys=True), "utf-8")
                    os.replace(temporary, target)
                self.dirty.clear()
            except OSError:
                pass


def cachedir() -> Path:
    base = os.environ.get("UNRIPE_CACHE") or os.environ.get("XDG_CACHE_HOME")
    if os.environ.get("UNRIPE_CACHE"):
        return Path(base)
    if base:
        return Path(base) / "unripe"
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "unripe" / "cache"
    return Path.home() / ".cache" / "unripe"


def goescape(module: str) -> str:
    """The Go module proxy spells capital letters as `!` plus the lower case letter."""
    return "".join(f"!{c.lower()}" if c.isupper() else c for c in module)


class Registries:
    """Looks publish times up, through the cache. Thread-safe: unripe asks many at once."""

    def __init__(self, cache: Cache, npm: str = "https://registry.npmjs.org", pypi: str = "https://pypi.org"):
        self.cache = cache
        self.npm = npm.rstrip("/")
        self.pypi = pypi.rstrip("/")
        # one npm document holds every version's time; fetch each package once
        self.pending: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()

    def published(self, package: Package) -> datetime:
        if package.published:
            return package.published
        cached = self.cache.get(package)
        if cached:
            return cached
        lookup = getattr(self, package.ecosystem)
        stamp = lookup(package)
        self.cache.put(package.ecosystem, package.name, package.version, stamp)
        return stamp

    def once(self, key: str) -> threading.Lock:
        with self.guard:
            return self.pending.setdefault(key, threading.Lock())

    def npm_(self, package: Package) -> datetime:  # pragma: no cover - named to keep getattr simple
        return self.npm_lookup(package)

    def npm_lookup(self, package: Package) -> datetime:
        with self.once(f"npm:{package.name}"):
            cached = self.cache.get(package)
            if cached:
                return cached
            name = urllib.parse.quote(package.name, safe="@")
            data = fetch(f"{self.npm}/{name.replace('/', '%2F')}")
            times = data.get("time") or {}
            for version, stamp in times.items():
                if version not in ("created", "modified"):
                    try:
                        self.cache.put("npm", package.name, version, when(stamp))
                    except ValueError:
                        continue
            if package.version not in times:
                raise Missing(f"{package} is not in the npm registry")
            return when(times[package.version])

    def pypi_lookup(self, package: Package) -> datetime:
        data = fetch(f"{self.pypi}/pypi/{urllib.parse.quote(package.name)}/{urllib.parse.quote(package.version)}/json")
        stamps = [when(f["upload_time_iso_8601"]) for f in data.get("urls") or [] if f.get("upload_time_iso_8601")]
        if not stamps:
            raise Missing(f"{package} has no files on PyPI")
        # the first file uploaded is when the version became installable
        return min(stamps)

    def cargo_lookup(self, package: Package) -> datetime:
        data = fetch(f"https://crates.io/api/v1/crates/{urllib.parse.quote(package.name)}/{urllib.parse.quote(package.version)}")
        stamp = (data.get("version") or {}).get("created_at")
        if not stamp:
            raise Missing(f"{package} is not on crates.io")
        return when(stamp)

    def go_lookup(self, package: Package) -> datetime:
        proxy = (os.environ.get("GOPROXY") or "https://proxy.golang.org").split(",")[0].split("|")[0]
        if proxy in ("direct", "off"):
            proxy = "https://proxy.golang.org"
        data = fetch(f"{proxy.rstrip('/')}/{goescape(package.name)}/@v/{goescape(package.version)}.info")
        if not data.get("Time"):
            raise Missing(f"{package} has no time in the module proxy")
        return when(data["Time"])

    def composer_lookup(self, package: Package) -> datetime:
        data = fetch(f"https://repo.packagist.org/p2/{package.name}.json")
        for entry in (data.get("packages") or {}).get(package.name) or []:
            if str(entry.get("version", "")).lstrip("v") == package.version and entry.get("time"):
                return when(entry["time"])
        raise Missing(f"{package} is not on packagist")

    def rubygems_lookup(self, package: Package) -> datetime:
        number, _, platform = package.version.partition("-")
        data = fetch(f"https://rubygems.org/api/v1/versions/{urllib.parse.quote(package.name)}.json")
        for entry in data if isinstance(data, list) else []:
            if entry.get("number") == number and (entry.get("platform") or "ruby") == (platform or "ruby"):
                return when(entry["created_at"])
        raise Missing(f"{package} is not on rubygems.org")

    def __getattr__(self, name: str):
        # `published` looks registries up by ecosystem name: npm -> npm_lookup
        if name in ("npm", "pypi"):
            raise AttributeError(name)
        lookup = f"{name}_lookup"
        if lookup in type(self).__dict__:
            return getattr(self, lookup)
        raise AttributeError(name)
