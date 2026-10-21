"""
When was a version published? Asked of each ecosystem's own public registry.

A publish time never changes once a version exists, so every answer is cached
on disk for good, and a second run on the same lockfile asks nothing.

A registry that cannot answer (down, rate limited, private package) is an
"unknown", never an "ok": failing open is exactly what an attacker wants.
"""

from __future__ import annotations

import email.utils
import fnmatch
import gzip
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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


def ask(url: str, accept: str = "*/*", method: str = "GET") -> tuple[dict[str, str], bytes]:
    """
    One request, retrying the failures worth retrying, as the response headers
    (lower cased names) and body, gzip undone. A 404 is an answer, not a failure.
    """
    delay = 1.0
    last = ""
    for attempt in range(TRIES):
        headers = {"User-Agent": AGENT, "Accept": accept, "Accept-Encoding": "gzip"}
        request = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                found = {name.lower(): value for name, value in response.headers.items()}
                body = response.read()
                if found.get("content-encoding") == "gzip" and body:
                    body = gzip.decompress(body)
                return found, body
        except urllib.error.HTTPError as error:
            if error.code in (404, 410):
                raise Missing(url) from error
            last = f"HTTP {error.code}"
            if error.code not in (429, 500, 502, 503, 504):
                break
            retry = error.headers.get("Retry-After")
            if retry and retry.isdigit():
                delay = max(delay, min(float(retry), 60.0))
        except (urllib.error.URLError, TimeoutError, OSError, EOFError, gzip.BadGzipFile) as error:
            last = str(getattr(error, "reason", error))
        if attempt < TRIES - 1:
            time.sleep(delay + random.random() * 0.5)
            delay *= 2
    raise Unreachable(f"{url}: {last}")


def fetchtext(url: str, accept: str = "*/*") -> str:
    """GET a body as text."""
    return ask(url, accept)[1].decode("utf-8-sig", errors="replace")


def fetchmodified(url: str) -> datetime:
    """When a file was last changed, from a HEAD request's Last-Modified header."""
    headers, _ = ask(url, method="HEAD")
    try:
        stamp = email.utils.parsedate_to_datetime(headers["last-modified"])
    except (KeyError, TypeError, ValueError) as error:
        raise Unreachable(f"{url}: no usable Last-Modified header") from error
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


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
        # versions learned in passing (an npm document lists every version);
        # kept for this run only, so the files on disk hold what was checked
        self.passing: dict[tuple[str, str, str], datetime] = {}

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
            if value is None:
                return self.passing.get(package.key)
        try:
            return when(value) if value else None
        except (TypeError, ValueError):
            return None

    def put(self, ecosystem: str, name: str, version: str, published: datetime) -> None:
        with self.lock:
            table, value = self.table(ecosystem), published.isoformat()
            if table.get(f"{name}@{version}") != value:
                table[f"{name}@{version}"] = value
                self.dirty.add(ecosystem)

    def recall(self, table: str, key: str) -> str | None:
        with self.lock:
            return self.table(table).get(key)

    def note(self, table: str, key: str, value: str) -> None:
        with self.lock:
            if self.table(table).get(key) != value:
                self.table(table)[key] = value
                self.dirty.add(table)

    def learn(self, ecosystem: str, name: str, version: str, published: datetime) -> None:
        with self.lock:
            self.passing[(ecosystem, name, version)] = published

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


def gosumdb(module: str) -> bool:
    """Whether the public Go checksum database should be asked about a module, as the go command would."""
    setting = (os.environ.get("GOSUMDB") or "sum.golang.org").strip()
    if setting == "off" or not setting.startswith("sum.golang.org"):
        return False
    private = os.environ.get("GONOSUMDB") or os.environ.get("GOPRIVATE") or ""
    for pattern in filter(None, (part.strip() for part in private.split(","))):
        # like the go command: a pattern matches a path prefix with as many elements as it has
        prefix = "/".join(module.split("/")[: pattern.count("/") + 1])
        if fnmatch.fnmatchcase(prefix, pattern):
            return False
    return True


class SumDB:
    """
    When a Go module version first appeared, from the public checksum database.

    sum.golang.org gives every module version a record number, in the order it
    first saw them, and nobody can change that order afterwards. It keeps no
    times, but index.golang.org lists versions with the time the module proxy
    first fetched them. Looking a few of those up gives record numbers at known
    times, and a version's record number can be placed between them.

    The record number at a past moment never changes, so calibration points are
    cached like publish times. They sit on whole hours (whole days further back)
    so that runs close together share them.
    """

    # hours back that calibration points sit at, newest first. dense in the
    # first month, where policies live, because the database grows unevenly
    LADDER = [1, 3, 6, 12, 24, 36, 48, 60, 72, 84, 96, 108, 120, 144, 168, 192, 216, 240, 264, 288, 336,
              384, 432, 504, 600, 720, 1080, 1440, 2160, 2880, 4320, 6480, 8760]

    def __init__(self, now: datetime, cache: Cache | None = None, url: str = "https://sum.golang.org", index: str = "https://index.golang.org/index"):
        self.now = now.astimezone(timezone.utc)
        self.cache = cache or Cache(None)
        self.url = url
        self.index = index
        self.lock = threading.Lock()
        hour = self.now.replace(minute=0, second=0, microsecond=0)
        day = hour.replace(hour=0)
        # on whole hours, and whole days further back, so runs close together share points
        self.moments = [self.now] + [(hour if hours < 48 else day) - timedelta(hours=hours) for hours in self.LADDER]
        self.numbers: dict[int, int] = {}

    def record(self, module: str, version: str) -> int:
        body = fetchtext(f"{self.url}/lookup/{goescape(module)}@{goescape(version)}")
        try:
            return int(body.splitlines()[0])
        except (IndexError, ValueError) as error:
            raise Unreachable(f"{self.url} answered a lookup in a form unripe does not know") from error

    def latest(self) -> int:
        lines = fetchtext(f"{self.url}/latest").splitlines()
        try:
            return int(lines[1])
        except (IndexError, ValueError) as error:
            raise Unreachable(f"{self.url} answered /latest in a form unripe does not know") from error

    def seen(self, moment: datetime) -> int:
        """The record number of the first version the proxy fetched at or after a moment."""
        since = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        remembered = self.cache.recall("gosumdb", since)
        if remembered is not None:
            return int(remembered)
        for line in fetchtext(f"{self.index}?since={since}&limit=20").splitlines():
            try:
                entry = json.loads(line)
                number = self.record(entry["Path"], entry["Version"])
            except (ValueError, KeyError, TypeError, Missing):
                # a version the checksum database refused: try the next one
                continue
            self.cache.note("gosumdb", since, str(number))
            return number
        raise Unreachable(f"{self.index} gave nothing to calibrate against at {since}")

    def point(self, rung: int) -> tuple[datetime, int]:
        """The time and record number at one rung of the ladder; rung 0 is the database as it is now."""
        with self.lock:
            if rung not in self.numbers:
                self.numbers[rung] = self.latest() if rung == 0 else self.seen(self.moments[rung])
            return self.moments[rung], self.numbers[rung]

    def first(self, record: int) -> tuple[datetime, datetime, datetime] | None:
        """
        When the version with this record number first appeared: the earliest
        and latest it can have been, and a best guess. None if over a year ago.
        Inside a narrow gap between two points the guess is interpolated; inside
        a wide one it is the latest, the youngest the version could be.
        """
        # the newest rung the record is not older than, by binary search, since numbers fall as rungs go back
        low, high = 1, len(self.moments) - 1
        if record < self.point(high)[1]:
            return None
        while low < high:
            middle = (low + high) // 2
            if record >= self.point(middle)[1]:
                high = middle
            else:
                low = middle + 1
        older, newer = self.point(low), self.point(low - 1)
        width = newer[0] - older[0]
        if width > max(timedelta(days=1), (self.now - older[0]) * 0.1):
            return older[0], newer[0], newer[0]
        span = max(newer[1] - older[1], 1)
        fraction = min(max((record - older[1]) / span, 0.0), 1.0)
        return older[0], newer[0], older[0] + width * fraction


CENTRAL = "https://repo1.maven.org/maven2"
GOOGLE = "https://dl.google.com/dl/android/maven2"
PLUGINS = "https://plugins.gradle.org/m2"
ANDROID = ("androidx.", "com.android.", "com.google.android.", "com.google.firebase", "com.google.gms", "com.google.mlkit", "com.google.ar.")


def mavenrepos(group: str, artifact: str, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """
    The Maven repositories to try for an artifact, most likely first, then any
    extra ones from the config file or UNRIPE_MAVEN_REPOS (comma separated).
    """
    if artifact.endswith(".gradle.plugin"):
        usual = [PLUGINS, CENTRAL, GOOGLE]
    elif group.startswith(ANDROID) or group in ("com.google.firebase", "com.google.gms"):
        usual = [GOOGLE, CENTRAL, PLUGINS]
    else:
        usual = [CENTRAL, GOOGLE, PLUGINS]
    more = list(extra) + (os.environ.get("UNRIPE_MAVEN_REPOS") or "").split(",")
    for url in (url.strip().rstrip("/") for url in more):
        if url and url not in usual:
            usual.append(url)
    return usual


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

    def __init__(
        self,
        cache: Cache,
        npmurl: str | None = None,
        pypiurl: str | None = None,
        now: datetime | None = None,
        mavenrepos: list[str] | tuple[str, ...] = (),
    ):
        self.cache = cache
        self.mavenextra = list(mavenrepos)
        self.sumdb = SumDB(now or datetime.now(timezone.utc), cache)
        self.npmurl = (npmurl or os.environ.get("UNRIPE_NPM_REGISTRY") or "https://registry.npmjs.org").rstrip("/")
        self.pypiurl = (pypiurl or os.environ.get("UNRIPE_PYPI_URL") or "https://pypi.org").rstrip("/")
        self.lookups: dict[str, Callable[[Package], datetime]] = {
            "npm": self.npm,
            "pypi": self.pypi,
            "cargo": self.cargo,
            "go": self.go,
            "composer": self.composer,
            "rubygems": self.rubygems,
            "nuget": self.nuget,
            "maven": self.maven,
        }
        # registries whose one answer covers every version are asked once per package
        self.pending: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()

    def published(self, package: Package) -> datetime:
        if package.published:
            return package.published
        stamp = self.cache.get(package)
        if stamp is None:
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
                self.cache.learn(package.ecosystem, package.name, version, stamp)
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
        # the proxy's time is the commit's, which the author chooses
        committed = when(data["Time"])
        if not gosumdb(package.name):
            return committed
        try:
            record = self.sumdb.record(package.name, package.version)
        except Missing:
            # not in the public checksum database (a private module, say): the commit time is all there is
            return committed
        window = self.sumdb.first(record)
        if window is None:
            return committed
        earliest, _, guess = window
        # a commit inside the window is the usual case, a version tagged soon after it was made.
        # a commit from before the window is the one to distrust: an old commit tagged
        # recently, or a commit time set back on purpose
        return committed if committed >= earliest else guess

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

    def nuget(self, package: Package) -> datetime:
        name, version = urllib.parse.quote(package.name.lower()), urllib.parse.quote(package.version.lower())
        try:
            leaf = fetch(f"https://api.nuget.org/v3/registration5-gz-semver2/{name}/{version}.json")
        except Missing:
            # the registration can lag a new version by an hour or more while it is already
            # installable. the package file's Last-Modified is a few minutes after publishing,
            # never before, so it can only make a version look younger
            return fetchmodified(f"https://api.nuget.org/v3-flatcontainer/{name}/{version}/{name}.{version}.nupkg")
        if not isinstance(leaf, dict) or not leaf.get("published"):
            raise Missing(f"{package} is not on nuget.org")
        published = when(str(leaf["published"]))
        if published.year > 1900:
            return published
        # nuget.org rewrites an unlisted version's publish time to 1900; the catalog keeps when it was created
        entry = leaf.get("catalogEntry")
        catalog = fetch(entry) if isinstance(entry, str) and entry.startswith("https://api.nuget.org/") else {}
        if not isinstance(catalog, dict) or not catalog.get("created"):
            raise Missing(f"{package} is unlisted on nuget.org and its catalog entry has no time")
        return when(str(catalog["created"]))

    def maven(self, package: Package) -> datetime:
        group, _, artifact = package.name.partition(":")
        path = f"{group.replace('.', '/')}/{artifact}/{package.version}/{artifact}-{package.version}.pom"
        unreachable = None
        for repository in mavenrepos(group, artifact, self.mavenextra):
            try:
                # repositories never change a published file, so its Last-Modified is when it was published
                return fetchmodified(f"{repository}/{path}")
            except Missing:
                continue
            except Unreachable as error:
                unreachable = error
        if unreachable:
            raise unreachable
        raise Missing(f"{package} is in none of the Maven repositories unripe knows")
