import json
from datetime import timedelta

import pytest

from conftest import NOW
from unripe.check import Result
from unripe.model import Finding, Package
from unripe.policy import Policy
from unripe.report import ago, render


def result(unknown="fail", unreadable=()):
    fresh = Finding(
        Package("npm", "fresh", "2.0.0", "web/package-lock.json"),
        "unripe",
        NOW - timedelta(hours=5),
        5 * 3600,
        locations=(("web/package-lock.json", 10),),
    )
    lost = Finding(
        Package("cargo", "ghost", "0.1.0", "Cargo.lock"),
        "unknown",
        reason="not found on crates.io",
        locations=(("Cargo.lock", 3),),
    )
    fine = Finding(Package("pypi", "idna", "3.6", "uv.lock"), "ok", NOW - timedelta(days=900), 900 * 86400, locations=(("uv.lock", None),))
    skipped = Finding(Package("npm", "@myorg/ui", "1.0.0"), "excluded", reason="matches exclude npm:@myorg/*")
    return Result(
        findings=[fresh, lost, fine, skipped],
        policy=Policy(unknown=unknown),
        lockfiles=["web/package-lock.json", "Cargo.lock", "uv.lock"],
        unreadable=list(unreadable),
        checked_at=NOW,
    )


@pytest.mark.parametrize(
    "seconds, said",
    [(59, "0m"), (40 * 60, "40m"), (5 * 3600, "5h"), (47 * 3600, "47h"), (52 * 3600, "2d 4h"), (3 * 86400, "3d"), (30 * 86400, "4w"), (800 * 86400, "2y"), (-5, "in the future"), (None, "?")],
)
def test_ago(seconds, said):
    assert ago(seconds) == said


def test_text_lists_problems_and_where_they_are():
    out = render(result(), "text")
    assert "unripe: checked 4 versions in 3 lockfiles, minimum age 3d" in out
    assert "unripe: 1 version published less than 3d ago" in out
    assert "fresh 2.0.0" in out and "5h old" in out and "old enough at 2026-10-23 07:00 UTC" in out
    assert "web/package-lock.json:10" in out
    assert "ghost 0.1.0" in out and "not found on crates.io" in out
    assert "idna" not in out
    assert out.rstrip().endswith("fail: 1 unripe, 1 unknown, 1 ok, 1 excluded")


def test_text_verbose_lists_everything():
    out = render(result(), "text", verbose=True)
    assert "idna 3.6" in out and "@myorg/ui" in out


def test_text_has_no_em_dashes_or_odd_characters():
    for form in ("text", "markdown"):
        assert all(ord(c) < 128 for c in render(result(unreadable=[("a", "a: broken")]), form))


def test_json_has_every_finding():
    data = json.loads(render(result(), "json"))
    assert data["result"] == "fail" and data["exit_code"] == 1
    assert data["policy"] == {"min_age": "3d", "min_age_seconds": 259200, "unknown": "fail", "exclude": []}
    assert data["summary"] == {"unripe": 1, "unknown": 1, "ok": 1, "excluded": 1}
    fresh = data["findings"][0]
    assert fresh["name"] == "fresh" and fresh["state"] == "unripe"
    assert fresh["published"] == "2026-10-20T07:00:00+00:00"
    assert fresh["old_enough_at"] == "2026-10-23T07:00:00+00:00"
    assert fresh["locations"] == [{"lockfile": "web/package-lock.json", "line": 10}]


def test_sarif_shape():
    data = json.loads(render(result(unreadable=[("bun.lockb", "bun.lockb: binary")]), "sarif"))
    assert data["version"] == "2.1.0"
    run = data["runs"][0]
    rules = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
    results = run["results"]
    assert [r["ruleId"] for r in results] == ["unripe/too-new", "unripe/unknown-age", "unripe/unreadable"]
    assert {r["ruleId"] for r in results} <= rules
    first = results[0]["locations"][0]["physicalLocation"]
    assert first == {"artifactLocation": {"uri": "web/package-lock.json"}, "region": {"startLine": 10}}
    assert results[0]["partialFingerprints"] == {"unripe/v1": "npm:fresh@2.0.0"}
    assert results[2]["locations"][0]["physicalLocation"]["region"] == {"startLine": 1}
    assert all(r["level"] == "error" for r in results)


def test_sarif_unknown_is_a_warning_when_allowed():
    data = json.loads(render(result(unknown="warn"), "sarif"))
    levels = {r["ruleId"]: r["level"] for r in data["runs"][0]["results"]}
    assert levels == {"unripe/too-new": "error", "unripe/unknown-age": "warning"}


def test_markdown_table():
    out = render(result(), "markdown")
    assert out.startswith("### unripe: 1 version too new, 1 with an unknown age\n")
    assert "| too new | npm `fresh` | 2.0.0 | 5h | 2026-10-23 07:00 UTC | web/package-lock.json:10 |" in out
    assert "| unknown | cargo `ghost` | 0.1.0 | ? | not found on crates.io | Cargo.lock:3 |" in out


def test_markdown_when_everything_passes():
    clean = Result(findings=[], policy=Policy(), lockfiles=["uv.lock"], checked_at=NOW, since="abc123")
    out = render(clean, "markdown")
    assert out.startswith("### unripe: every version is old enough")
    assert "Checked 0 versions added since abc123 in 1 lockfile." in out
    assert "|" not in out


def test_markdown_escapes_pipes():
    odd = Finding(Package("npm", "a|b", "1.0.0"), "unknown", reason="x | y", locations=(("p", 1),))
    out = render(Result(findings=[odd], policy=Policy(), lockfiles=["p"], checked_at=NOW), "markdown")
    assert "a\\|b" in out and "x \\| y" in out
