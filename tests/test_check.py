import shutil
import subprocess
from datetime import timedelta

import pytest

from conftest import NOW, TESTDATA
from unripe.check import Problem, check
from unripe.policy import Policy

LEFTPAD = '''{
  "name": "app",
  "lockfileVersion": 3,
  "packages": {
    "": {"name": "app"},
    "node_modules/left-pad": {
      "version": "1.3.0",
      "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz"
    }%s
  }
}
'''
FRESH = ''',
    "node_modules/fresh": {
      "version": "2.0.0",
      "resolved": "https://registry.npmjs.org/fresh/-/fresh-2.0.0.tgz"
    }'''


def run(paths, lookups, **policy):
    return check([str(path) for path in paths], Policy(**policy), lookups, now=NOW)


def states(result):
    return {(f.package.name, f.package.version): f.state for f in result.findings}


def test_each_state(fake, lookups, tmp_path):
    (tmp_path / "package-lock.json").write_text(LEFTPAD % FRESH)
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=400))
    fake.add("npm", "fresh", "2.0.0", timedelta(hours=5))
    result = run([tmp_path], lookups)
    assert states(result) == {("left-pad", "1.3.0"): "ok", ("fresh", "2.0.0"): "unripe"}
    assert result.exitcode == 1
    unripe = result.findings[0]
    assert unripe.state == "unripe"
    assert round(unripe.age_seconds) == 5 * 3600
    assert len(unripe.locations) == 1
    path, line = unripe.locations[0]
    assert path.endswith("/package-lock.json") and "\\" not in path
    assert line == 10


def test_policy_boundary_is_inclusive(fake, lookups, tmp_path):
    (tmp_path / "package-lock.json").write_text(LEFTPAD % "")
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=3))
    assert run([tmp_path], lookups).exitcode == 0


def test_unknown_fails_by_default_and_warn_softens_it(fake, lookups, tmp_path):
    (tmp_path / "package-lock.json").write_text(LEFTPAD % "")
    strict = run([tmp_path], lookups)
    assert states(strict) == {("left-pad", "1.3.0"): "unknown"}
    assert "not found on the npm registry" in strict.findings[0].reason
    assert strict.exitcode == 1
    assert run([tmp_path], lookups, unknown="warn").exitcode == 0


def test_an_unreachable_registry_is_unknown(fake, lookups, tmp_path):
    (tmp_path / "package-lock.json").write_text(LEFTPAD % "")
    fake.add("npm", "left-pad", "1.3.0")
    fake.down.add("npm")
    result = run([tmp_path], lookups)
    assert result.findings[0].state == "unknown"
    assert "did not answer" in result.findings[0].reason


def test_excluded_packages_are_not_looked_up(fake, lookups, tmp_path):
    (tmp_path / "package-lock.json").write_text(LEFTPAD % FRESH)
    fake.add("npm", "left-pad", "1.3.0")
    result = run([tmp_path], lookups, exclude=["npm:fresh"])
    assert states(result)[("fresh", "2.0.0")] == "excluded"
    assert result.exitcode == 0
    assert all("fresh" not in url for url in fake.urls)


def test_versions_in_several_lockfiles_are_asked_about_once(fake, lookups, tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "package-lock.json").write_text(LEFTPAD % "")
    fake.add("npm", "left-pad", "1.3.0", timedelta(hours=1))
    result = run([tmp_path], lookups)
    assert len(result.findings) == 1
    assert len(fake.urls) == 1
    places = [path.rsplit("/", 2)[-2] for path, _ in result.findings[0].locations]
    assert sorted(places) == ["a", "b"]
    assert all(line == 6 for _, line in result.findings[0].locations)


def test_lockfile_times_are_used_without_asking(fake, lookups):
    result = run([TESTDATA / "uv"], lookups, unknown="warn")
    assert states(result)[("idna", "3.6")] == "ok"
    # zope.interface has no upload time in the fixture and the fake does not know it
    assert states(result)[("zope-interface", "6.2")] == "unknown"
    assert all("idna" not in url for url in fake.urls)


def test_every_fixture_checks_with_a_registry_that_knows_everything(fake, lookups):
    from unripe import lockfiles

    for path in lockfiles.find([str(TESTDATA)]):
        if path.name == "bun.lockb":
            continue
        for package in lockfiles.read(path.read_text("utf-8"), path):
            fake.add(package.ecosystem, package.name, package.version)
    result = check([str(TESTDATA)], Policy(), lookups, now=NOW)
    assert result.count("unknown") == 0
    assert result.count("unripe") == 0
    assert [path for path, _ in result.unreadable] == [p for p in result.lockfiles if p.endswith("bun.lockb")]
    assert result.exitcode == 2


def test_no_lockfiles_is_an_error(lookups, tmp_path):
    result = run([tmp_path], lookups)
    assert result.lockfiles == []
    assert result.exitcode == 2


def test_an_unreadable_lockfile_fails_the_run(fake, lookups, tmp_path):
    (tmp_path / "Cargo.lock").write_text("[[package]\n")
    result = run([tmp_path], lookups)
    assert result.exitcode == 2
    assert "could not be read" in result.unreadable[0][1]


def test_the_cache_is_saved_after_a_check(fake, tmp_path, monkeypatch):
    from unripe.registries import Cache, Registries

    (tmp_path / "lock").mkdir()
    (tmp_path / "lock" / "package-lock.json").write_text(LEFTPAD % "")
    fake.add("npm", "left-pad", "1.3.0")
    run([tmp_path / "lock"], Registries(Cache(tmp_path / "cache")))
    assert (tmp_path / "cache" / "npm.json").exists()


git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def commit(repo, message):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "README").write_text("x")
    commit(tmp_path, "start")
    return tmp_path


@git
def test_since_checks_only_versions_added(fake, lookups, repo):
    (repo / "package-lock.json").write_text(LEFTPAD % "")
    commit(repo, "lock")
    (repo / "package-lock.json").write_text(LEFTPAD % FRESH)
    fake.add("npm", "fresh", "2.0.0", timedelta(hours=1))
    result = check([str(repo)], Policy(), lookups, since="HEAD", now=NOW)
    assert states(result) == {("fresh", "2.0.0"): "unripe"}
    assert result.since == "HEAD"


@git
def test_since_treats_a_new_lockfile_as_all_new(fake, lookups, repo):
    (repo / "package-lock.json").write_text(LEFTPAD % "")
    fake.add("npm", "left-pad", "1.3.0")
    result = check([str(repo)], Policy(), lookups, since="HEAD", now=NOW)
    assert states(result) == {("left-pad", "1.3.0"): "ok"}


@git
def test_since_with_nothing_new_passes(fake, lookups, repo):
    (repo / "package-lock.json").write_text(LEFTPAD % "")
    commit(repo, "lock")
    result = check([str(repo)], Policy(), lookups, since="HEAD", now=NOW)
    assert result.findings == []
    assert result.exitcode == 0
    assert fake.urls == []


@git
def test_since_a_bad_ref_is_a_problem(lookups, repo):
    (repo / "package-lock.json").write_text(LEFTPAD % "")
    with pytest.raises(Problem, match="not a commit"):
        check([str(repo)], Policy(), lookups, since="no-such-branch", now=NOW)


@git
def test_since_works_in_a_subdirectory(fake, lookups, repo):
    (repo / "web").mkdir()
    (repo / "web" / "package-lock.json").write_text(LEFTPAD % "")
    commit(repo, "lock")
    (repo / "web" / "package-lock.json").write_text(LEFTPAD % FRESH)
    fake.add("npm", "fresh", "2.0.0", timedelta(days=30))
    result = check([str(repo)], Policy(), lookups, since="HEAD", now=NOW)
    assert states(result) == {("fresh", "2.0.0"): "ok"}
