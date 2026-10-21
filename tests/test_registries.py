import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from conftest import NOW
from unripe import registries
from unripe.model import Package
from unripe.registries import Cache, Missing, Registries, Unreachable, crateindex, goescape, goproxy


@pytest.mark.parametrize(
    "ecosystem, name, version",
    [
        ("npm", "@types/node", "20.11.0"),
        ("pypi", "django", "5.0.2"),
        ("cargo", "serde", "1.0.197"),
        ("go", "github.com/BurntSushi/toml", "v1.3.2"),
        ("composer", "monolog/monolog", "3.5.0"),
        ("rubygems", "nokogiri", "1.16.0-x86_64-linux"),
    ],
)
def test_every_ecosystem_answers(fake, lookups, ecosystem, name, version):
    moment = fake.add(ecosystem, name, version, timedelta(days=10))
    assert lookups.published(Package(ecosystem, name, version)) == moment


def test_npm_scoped_names_are_one_path_segment(fake, lookups):
    fake.add("npm", "@types/node", "20.11.0")
    lookups.published(Package("npm", "@types/node", "20.11.0"))
    assert fake.urls == ["https://registry.npmjs.org/@types%2Fnode"]


def test_npm_fetches_a_package_once_for_all_its_versions(fake, lookups):
    for version in ("2.1.2", "2.1.3"):
        fake.add("npm", "ms", version)
    lookups.published(Package("npm", "ms", "2.1.2"))
    lookups.published(Package("npm", "ms", "2.1.3"))
    assert len(fake.urls) == 1


def test_pypi_takes_the_first_file_uploaded(fake, lookups):
    moment = fake.add("pypi", "django", "5.0.2")
    # the fake uploads a wheel an hour after the sdist
    assert lookups.published(Package("pypi", "django", "5.0.2")) == moment


def test_cargo_reads_the_sparse_index(fake, lookups):
    fake.add("cargo", "syn", "2.0.50")
    lookups.published(Package("cargo", "syn", "2.0.50"))
    assert fake.urls == ["https://index.crates.io/3/s/syn"]


@pytest.mark.parametrize(
    "name, path",
    [("a", "1/a"), ("cc", "2/cc"), ("syn", "3/s/syn"), ("serde", "se/rd/serde"), ("Inflector", "in/fl/inflector")],
)
def test_crate_index_paths(name, path):
    assert crateindex(name) == path


def test_go_escapes_capitals(fake, lookups):
    fake.add("go", "github.com/BurntSushi/toml", "v1.3.2")
    lookups.published(Package("go", "github.com/BurntSushi/toml", "v1.3.2"))
    assert fake.urls == ["https://proxy.golang.org/github.com/!burnt!sushi/toml/@v/v1.3.2.info"]
    assert goescape("github.com/Azure/Go") == "github.com/!azure/!go"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", "https://proxy.golang.org"),
        ("direct", "https://proxy.golang.org"),
        ("https://goproxy.example.com,direct", "https://goproxy.example.com"),
        ("off", "https://proxy.golang.org"),
        ("direct|https://goproxy.example.com/", "https://goproxy.example.com"),
    ],
)
def test_goproxy(monkeypatch, value, expected):
    monkeypatch.setenv("GOPROXY", value)
    assert goproxy() == expected


def test_composer_expands_minified_entries(fake, lookups):
    older = fake.add("composer", "psr/log", "2.0.0", timedelta(days=900))
    fake.add("composer", "psr/log", "3.0.0", timedelta(days=800))
    assert lookups.published(Package("composer", "psr/log", "2.0.0")) == older


def test_composer_unset_fields_are_dropped(monkeypatch, lookups):
    payload = {
        "packages": {
            "a/b": [
                {"name": "a/b", "version": "v2.0.0", "time": "2024-01-02T00:00:00+00:00"},
                {"version": "v1.0.0", "time": "__unset"},
            ]
        }
    }
    monkeypatch.setattr(registries, "fetchtext", lambda url, accept="*/*": json.dumps(payload))
    with pytest.raises(Missing):
        lookups.published(Package("composer", "a/b", "1.0.0"))


def test_rubygems_matches_platform(fake, lookups):
    plain = fake.add("rubygems", "nokogiri", "1.16.0", timedelta(days=5))
    linux = fake.add("rubygems", "nokogiri", "1.16.0-x86_64-linux", timedelta(days=4))
    assert lookups.published(Package("rubygems", "nokogiri", "1.16.0")) == plain
    assert lookups.published(Package("rubygems", "nokogiri", "1.16.0-x86_64-linux")) == linux


def test_missing_and_unreachable_are_different(fake, lookups):
    with pytest.raises(Missing):
        lookups.published(Package("npm", "nope", "1.0.0"))
    fake.add("cargo", "serde", "1.0.0")
    fake.down.add("cargo")
    with pytest.raises(Unreachable):
        lookups.published(Package("cargo", "serde", "1.0.0"))


def test_a_version_the_registry_lacks_is_missing(fake, lookups):
    fake.add("npm", "left-pad", "1.3.0")
    with pytest.raises(Missing):
        lookups.published(Package("npm", "left-pad", "9.9.9"))


def test_times_carried_by_the_lockfile_need_no_registry(fake, lookups):
    moment = NOW - timedelta(days=2)
    assert lookups.published(Package("composer", "x/y", "1.0.0", published=moment)) == moment
    assert fake.urls == []


def test_cache_survives_a_restart(fake, tmp_path):
    moment = fake.add("npm", "left-pad", "1.3.0")
    first = Registries(Cache(tmp_path))
    first.published(Package("npm", "left-pad", "1.3.0"))
    first.cache.save()
    asked = len(fake.urls)
    second = Registries(Cache(tmp_path))
    assert second.published(Package("npm", "left-pad", "1.3.0")) == moment
    assert len(fake.urls) == asked
    assert json.loads((tmp_path / "npm.json").read_text())["left-pad@1.3.0"].startswith("2025-")


def test_a_corrupt_cache_is_ignored(fake, tmp_path):
    (tmp_path / "npm.json").write_text("{broken")
    fake.add("npm", "left-pad", "1.3.0")
    assert Registries(Cache(tmp_path)).published(Package("npm", "left-pad", "1.3.0"))


def test_registry_urls_from_the_environment(monkeypatch):
    monkeypatch.setenv("UNRIPE_NPM_REGISTRY", "https://npm.example.com/")
    monkeypatch.setenv("UNRIPE_PYPI_URL", "https://pypi.example.com")
    found = Registries(Cache(None))
    assert found.npmurl == "https://npm.example.com"
    assert found.pypiurl == "https://pypi.example.com"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_retries_server_errors_then_answers(monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        assert request.get_header("User-agent").startswith("unripe/")
        return Response(b'{"ok": true}')

    monkeypatch.setattr(registries.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(registries.time, "sleep", lambda seconds: None)
    assert registries.fetch("https://example.com/x") == {"ok": True}
    assert len(calls) == 3


def test_fetch_gives_up_as_unreachable(monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(registries.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(registries.time, "sleep", lambda seconds: None)
    with pytest.raises(Unreachable, match="no route"):
        registries.fetch("https://example.com/x")


def test_fetch_404_is_missing_without_retrying(monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(registries.urllib.request, "urlopen", urlopen)
    with pytest.raises(Missing):
        registries.fetch("https://example.com/x")
    assert calls == [1]


def test_fetch_non_json_is_unreachable(monkeypatch):
    monkeypatch.setattr(registries.urllib.request, "urlopen", lambda request, timeout: Response(b"<html>"))
    with pytest.raises(Unreachable, match="not JSON"):
        registries.fetch("https://example.com/x")


def test_when_reads_every_registry_style():
    utc = timezone.utc
    assert registries.when("2024-01-02T03:04:05.678Z") == datetime(2024, 1, 2, 3, 4, 5, 678000, tzinfo=utc)
    assert registries.when("2024-01-02T03:04:05+00:00") == datetime(2024, 1, 2, 3, 4, 5, tzinfo=utc)
    assert registries.when("2024-01-02T03:04:05") == datetime(2024, 1, 2, 3, 4, 5, tzinfo=utc)
