import shutil
import subprocess
from datetime import date, timedelta

import pytest

from conftest import NOW, TESTDATA
from unripe import lockfiles
from unripe.check import Problem, check, instead, judge
from unripe.model import Package
from unripe.policy import Allow, Policy, Rule

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


def test_scan_is_the_library_entry_point(fake, tmp_path):
    from datetime import datetime, timezone

    import unripe

    fake.base = datetime.now(timezone.utc)
    (tmp_path / "package-lock.json").write_text(LEFTPAD % FRESH)
    fake.add("npm", "left-pad", "1.3.0")
    fake.add("npm", "fresh", "2.0.0", timedelta(hours=1))
    findings = unripe.scan([str(tmp_path)], min_age="3d", exclude=["left-pad"], cache=False)
    assert {(f.package.name, f.state) for f in findings} == {("fresh", "unripe"), ("left-pad", "excluded")}
    (tmp_path / "Cargo.lock").write_text("[[broken")
    with pytest.raises(Problem, match="Cargo.lock"):
        unripe.scan([str(tmp_path)])


@pytest.mark.parametrize(
    "lockfile, ecosystem, name, version, text",
    [
        # the PATH section's `rack (>= 2)` constraint comes first and is not the lock
        ("ruby/Gemfile.lock", "rubygems", "rack", "3.0.9", "    rack (3.0.9)"),
        # composer.lock keeps the v that unripe drops
        ("composer/composer.lock", "composer", "old/no-zone", "1.0.0", '            "name": "old/no-zone",'),
        ("pnpm-v6/pnpm-lock.yaml", "npm", "react", "18.2.0", "  /react@18.2.0:"),
        ("go/go.mod", "go", "github.com/pkg/errors", "v0.9.1", "require github.com/pkg/errors v0.9.1 // indirect"),
        ("npm-v3/package-lock.json", "npm", "ms", "2.1.2", '    "node_modules/is-number/node_modules/ms": {'),
        ("nuget/packages.lock.json", "nuget", "System.Text.Json", "8.0.5", '      "System.Text.Json": {'),
        ("gradle/gradle.lockfile", "maven", "com.fasterxml.jackson.core:jackson-core", "2.22.2", "com.fasterxml.jackson.core:jackson-core:2.22.2=compileClasspath"),
    ],
)
def test_line_points_at_the_locked_entry(lockfile, ecosystem, name, version, text):
    from unripe.check import line
    from unripe.model import Package

    rows = (TESTDATA / lockfile).read_text("utf-8").splitlines()
    number = line([row.lower() for row in rows], Package(ecosystem, name, version))
    assert rows[number - 1] == text


def lock(tmp_path, *packages):
    """A package-lock.json with these name and version pairs."""
    entries = ",\n".join(
        f'    "node_modules/{name}": {{"version": "{version}", "resolved": "https://registry.npmjs.org/{name}/-/{name}-{version}.tgz"}}'
        for name, version in packages
    )
    (tmp_path / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {"": {},\n' + entries + "\n}}")


def test_min_age_for_changes_what_is_too_new(fake, lookups, tmp_path):
    lock(tmp_path, ("react", "19.0.0"), ("@types/node", "22.0.0"))
    fake.add("npm", "react", "19.0.0", timedelta(days=5))
    fake.add("npm", "@types/node", "22.0.0", timedelta(hours=30))
    result = run([tmp_path], lookups, min_age_for={"npm": timedelta(days=7), "npm:@types/*": timedelta(days=1)})
    assert states(result) == {("react", "19.0.0"): "unripe", ("@types/node", "22.0.0"): "ok"}
    react = result.findings[0]
    assert react.minimum == timedelta(days=7) and react.ripe == NOW - timedelta(days=5) + timedelta(days=7)


def test_allow_lets_a_version_through_until_it_ends(fake, lookups, tmp_path):
    lock(tmp_path, ("axios", "1.13.2"), ("left-pad", "1.3.0"))
    fake.add("npm", "axios", "1.13.2", timedelta(hours=5))
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=400))
    current = [Allow(Rule.parse("npm:axios@1.13.2"), "fixes a CVE", date(2026, 10, 25))]
    result = run([tmp_path], lookups, allow=current)
    assert states(result)[("axios", "1.13.2")] == "allowed"
    assert result.findings[0].reason == "allowed until 2026-10-25: fixes a CVE"
    assert result.exitcode == 0 and result.notes == []

    ended = [Allow(Rule.parse("npm:axios@1.13.2"), "", date(2026, 10, 1)), Allow(Rule.parse("left-pad")), Allow(Rule.parse("nothing-here"))]
    result = run([tmp_path], lookups, allow=ended)
    assert states(result)[("axios", "1.13.2")] == "unripe" and result.exitcode == 1
    assert result.notes == [
        "the allow entry for npm:axios@1.13.2 ended on 2026-10-01, so it no longer applies",
        "the allow entry for left-pad is no longer needed: everything it covers is old enough now",
        "the allow entry for nothing-here matches nothing in these lockfiles",
    ]


def advisory(ident, name, fixed):
    return {"id": ident, "affected": [{"package": {"name": name, "ecosystem": "npm"}, "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": fixed}]}]}]}


def test_security_fixes_are_allowed_when_asked(fake, lookups, tmp_path):
    lock(tmp_path, ("axios", "1.13.2"), ("fresh", "2.0.0"), ("later", "1.0.1"))
    for name, version in (("axios", "1.13.2"), ("fresh", "2.0.0"), ("later", "1.0.1")):
        fake.add("npm", name, version, timedelta(hours=5))
    # malware reports never count, and only the version an advisory names as the fix does
    fake.advisories = [advisory("GHSA-aaaa-bbbb-cccc", "axios", "1.13.2"), advisory("MAL-2026-1", "fresh", "2.0.0"), advisory("GHSA-dddd-eeee-ffff", "later", "1.0.0")]
    assert states(run([tmp_path], lookups))[("axios", "1.13.2")] == "unripe"
    assert fake.posts == []
    result = run([tmp_path], lookups, security_fixes=True)
    assert states(result) == {("axios", "1.13.2"): "allowed", ("fresh", "2.0.0"): "unripe", ("later", "1.0.1"): "unripe"}
    assert next(f for f in result.findings if f.state == "allowed").reason == "fixes GHSA-aaaa-bbbb-cccc"


def test_suggests_the_newest_old_enough_version_in_the_same_line(fake, lookups, tmp_path):
    lock(tmp_path, ("lib", "2.3.1"))
    fake.add("npm", "lib", "2.3.1", timedelta(hours=5))
    fake.add("npm", "lib", "2.3.0", timedelta(days=20))
    fake.add("npm", "lib", "2.2.9", timedelta(days=40))
    fake.add("npm", "lib", "2.4.0-beta.1", timedelta(days=10))  # a prerelease is never suggested for a release
    fake.add("npm", "lib", "2.3.0-unpublished.0", timedelta(days=30))
    fake.add("npm", "lib", "2.2.10", timedelta(days=30))  # unpublished below, so never suggested
    fake.add("npm", "lib", "3.0.0", timedelta(days=9))  # another major
    fake.add("npm", "lib", "1.9.9", timedelta(days=500))
    fake.yanked.add(("npm", "lib", "2.3.0"))
    result = run([tmp_path], lookups)
    assert result.findings[0].instead == ("2.2.10", NOW - timedelta(days=30))
    assert run([tmp_path], lookups, min_age=timedelta(days=60)).findings[0].instead is None
    assert check([str(tmp_path)], Policy(), lookups, now=NOW, suggest=False).findings[0].instead is None


@pytest.mark.parametrize(
    "ecosystem, version, older, expected",
    [
        ("pypi", "2.32.4", ["2.32.3", "2.33.0rc1", "2.31.0"], "2.32.3"),
        ("cargo", "0.4.2", ["0.4.1", "0.3.9"], "0.4.1"),  # under 1.0 the minor is the line
        ("rubygems", "1.16.1-x86_64-linux", ["1.16.0", "1.16.0-x86_64-linux", "1.16.0-arm64-darwin"], "1.16.0-x86_64-linux"),
        ("nuget", "8.0.1", ["8.0.1-preview.1", "8.0.0"], "8.0.0"),
        ("composer", "3.5.1", ["3.5.0", "3.6.0-RC1"], "3.5.0"),
        ("jsr", "1.0.14", ["1.0.13"], "1.0.13"),
    ],
)
def test_suggestions_on_other_registries(fake, lookups, ecosystem, version, older, expected):
    name = "@std/pkg" if ecosystem == "jsr" else "vendor/pkg" if ecosystem == "composer" else "pkg"
    fake.add(ecosystem, name, version, timedelta(hours=1))
    for index, other in enumerate(older):
        fake.add(ecosystem, name, other, timedelta(days=10 + index))
    finding = judge(Package(ecosystem, name, version), Policy(), lookups, NOW)
    assert finding.state == "unripe"
    assert instead(finding, lookups, NOW)[0] == expected


def test_an_unpublished_npm_version_is_unknown(fake, tmp_path):
    from unripe.registries import Cache, Registries

    lock(tmp_path, ("event-stream", "3.3.6"))
    fake.add("npm", "event-stream", "3.3.6", timedelta(days=400))
    fake.yanked.add(("npm", "event-stream", "3.3.6"))
    result = run([tmp_path], Registries(Cache(None)))
    assert states(result) == {("event-stream", "3.3.6"): "unknown"}
    assert "unpublished from the npm registry" in result.findings[0].reason
    # checking as of a past moment, it was still up then, so its age decides
    assert states(run([tmp_path], Registries(Cache(None), now=NOW))) == {("event-stream", "3.3.6"): "ok"}


def test_deno_jsr_versions_are_dated_by_jsr(fake, lookups):
    for package in lockfiles.read((TESTDATA / "deno/deno.lock").read_text(), "deno.lock"):
        fake.add(package.ecosystem, package.name, package.version, timedelta(days=30))
    fake.add("jsr", "@std/assert", "1.0.13", timedelta(hours=2))
    result = run([TESTDATA / "deno"], lookups)
    assert states(result)[("@std/assert", "1.0.13")] == "unripe" and result.count("ok") == 6


def test_actions_are_dated_by_their_release(fake, lookups, tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    old, new, loose = "a" * 40, "b" * 40, "c" * 40
    (workflows / "ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n"
        f"      - uses: actions/checkout@{old}\n"
        "      - uses: actions/checkout@v7\n"
        "      - uses: actions/cache@main\n"
        f"      - uses: someone/thing@{loose}\n"
    )
    fake.tags["actions/checkout"] = {"v7.0.1": new, "v7": new, "v6.0.0": old, "v6": old}
    fake.tags["actions/cache"] = {"v4.0.0": "d" * 40}
    fake.tags["someone/thing"] = {}
    fake.releases[("actions/checkout", "v6.0.0")] = NOW - timedelta(days=90)
    fake.releases[("actions/checkout", "v7.0.1")] = NOW - timedelta(hours=10)
    fake.commits[("someone/thing", loose)] = NOW - timedelta(days=50)
    assert run([tmp_path], lookups).lockfiles == []
    result = run([tmp_path], lookups, workflows=True)
    assert states(result) == {
        ("actions/checkout", old): "ok",
        ("actions/checkout", "v7"): "unripe",
        ("actions/cache", "main"): "unknown",
        ("someone/thing", loose): "ok",
    }
    assert "a branch or an unknown tag" in next(f.reason for f in result.findings if f.state == "unknown")
    # a moving tag is looked up again on each run, never taken from the cache
    assert lookups.cache.get(Package("actions", "actions/checkout", "v7")) is None
    assert lookups.cache.get(Package("actions", "actions/checkout", old)) == NOW - timedelta(days=90)
