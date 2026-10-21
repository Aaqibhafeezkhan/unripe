"""
A fake of every registry unripe asks, answering in each registry's real shape,
so tests run with no network and still cover how URLs are built and answers read.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unripe import registries
from unripe.registries import Cache, Missing, Registries, Unreachable

TESTDATA = Path(__file__).parent / "testdata"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeRegistry:
    def __init__(self) -> None:
        self.times: dict[tuple[str, str], dict[str, datetime]] = {}
        self.down: set[str] = set()  # ecosystems that answer 503
        self.urls: list[str] = []
        # ages are measured back from here; the cli tests move it to the real clock
        self.base = NOW

    def add(self, ecosystem: str, name: str, version: str, age: timedelta | None = None, at: datetime | None = None) -> datetime:
        moment = at or self.base - (age if age is not None else timedelta(days=400))
        self.times.setdefault((ecosystem, name), {})[version] = moment
        return moment

    def versions(self, ecosystem: str, name: str) -> dict[str, datetime]:
        if ecosystem in self.down:
            raise Unreachable(f"{ecosystem}: HTTP 503")
        found = self.times.get((ecosystem, name))
        if found is None:
            raise Missing(name)
        return found

    def fetchtext(self, url: str, accept: str = "*/*") -> str:
        self.urls.append(url)
        return json.dumps(self.answer(url)) if not url.startswith("https://index.crates.io/") else self.crates(url)

    def answer(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(parsed.path)
        if parsed.netloc == "registry.npmjs.org":
            name = path.lstrip("/")
            times = {"created": "2010-01-01T00:00:00.000Z", "modified": iso(NOW)}
            times.update({version: iso(moment) for version, moment in self.versions("npm", name).items()})
            return {"name": name, "time": times}
        if parsed.netloc == "pypi.org":
            match = re.fullmatch(r"/pypi/([^/]+)/([^/]+)/json", path)
            moment = self.versions("pypi", match.group(1)).get(match.group(2))
            if moment is None:
                raise Missing(url)
            # a wheel uploaded an hour after the sdist; the sdist is the publish time
            return {
                "info": {"name": match.group(1), "version": match.group(2)},
                "urls": [
                    {"filename": "x.whl", "upload_time_iso_8601": iso(moment + timedelta(hours=1))},
                    {"filename": "x.tar.gz", "upload_time_iso_8601": iso(moment)},
                ],
            }
        if parsed.netloc == "proxy.golang.org" or parsed.netloc == "goproxy.example.com":
            match = re.fullmatch(r"/(.+)/@v/(.+)\.info", path)
            unescape = lambda text: re.sub(r"!([a-z])", lambda m: m.group(1).upper(), text)  # noqa: E731
            moment = self.versions("go", unescape(match.group(1))).get(unescape(match.group(2)))
            if moment is None:
                raise Missing(url)
            return {"Version": unescape(match.group(2)), "Time": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if parsed.netloc == "repo.packagist.org":
            name = re.fullmatch(r"/p2/(.+)\.json", path).group(1)
            entries = []
            for number, (version, moment) in enumerate(sorted(self.versions("composer", name).items(), reverse=True)):
                # minified like the real p2 files: the first entry is full, later ones list changes
                entry = {"version": f"v{version}", "version_normalized": f"{version}.0", "time": moment.isoformat()}
                if number == 0:
                    entry.update({"name": name, "type": "library", "license": ["MIT"]})
                entries.append(entry)
            return {"minified": "composer/2.0", "packages": {name: entries}}
        if parsed.netloc == "rubygems.org":
            name = re.fullmatch(r"/api/v1/versions/(.+)\.json", path).group(1)
            out = []
            for version, moment in self.versions("rubygems", name).items():
                number, _, platform = version.partition("-")
                out.append({"number": number, "platform": platform or "ruby", "created_at": iso(moment)})
            return out
        raise AssertionError(f"the fake registry was asked an unexpected url: {url}")

    def crates(self, url: str) -> str:
        name = url.rsplit("/", 1)[1]
        assert url == f"https://index.crates.io/{registries.crateindex(name)}"
        lines = []
        for version, moment in self.versions("cargo", name).items():
            lines.append(json.dumps({"name": name, "vers": version, "deps": [], "cksum": "00", "features": {}, "yanked": False, "pubtime": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}))
        return "\n".join(lines) + "\n"


@pytest.fixture
def fake(monkeypatch) -> FakeRegistry:
    registry = FakeRegistry()
    monkeypatch.setattr(registries, "fetchtext", registry.fetchtext)
    monkeypatch.delenv("GOPROXY", raising=False)
    monkeypatch.delenv("UNRIPE_NPM_REGISTRY", raising=False)
    monkeypatch.delenv("UNRIPE_PYPI_URL", raising=False)
    return registry


@pytest.fixture
def lookups(fake) -> Registries:
    """Registries backed by the fake, with an in-memory cache."""
    return Registries(Cache(None))


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """Never touch the real cache directory from a test."""
    monkeypatch.setenv("UNRIPE_CACHE", str(tmp_path_factory.mktemp("cache")))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Unit tests never reach a real registry; a test that wants urlopen patches it itself."""

    def refuse(request, timeout=None):
        raise AssertionError(f"a test tried to reach the network: {getattr(request, 'full_url', request)}")

    monkeypatch.setattr(registries.urllib.request, "urlopen", refuse)
