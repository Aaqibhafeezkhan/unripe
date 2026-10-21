import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import TESTDATA
from unripe import __version__, cli

LOCK = (TESTDATA / "npm-v3" / "package-lock.json").read_text("utf-8")
NAMES = [
    ("@types/node", "20.11.0"),
    ("ansi-regex", "5.0.1"),
    ("emoji-regex", "8.0.0"),
    ("is-fullwidth-code-point", "3.0.0"),
    ("is-number", "7.0.0"),
    ("left-pad", "1.3.0"),
    ("ms", "2.1.2"),
    ("ms", "2.1.3"),
    ("string-width", "4.2.3"),
    ("strip-ansi", "6.0.1"),
    ("undici-types", "5.26.5"),
]


@pytest.fixture
def project(fake, tmp_path, monkeypatch):
    """A directory with one npm lockfile whose versions the fake registry knows, all old."""
    (tmp_path / "package-lock.json").write_text(LOCK)
    fake.base = datetime.now(timezone.utc)
    for name, version in NAMES:
        fake.add("npm", name, version, timedelta(days=400))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_clean_run_exits_zero(project, capsys):
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "checked 11 versions in 1 lockfile" in out
    assert "pass: 0 unripe, 0 unknown, 11 ok, 0 excluded" in out


def test_check_is_the_default_command(project, capsys):
    assert cli.main([]) == 0
    assert cli.main(["."]) == 0
    assert cli.main(["--min-age", "2d"]) == 0


def test_a_fresh_version_exits_one(project, fake, capsys):
    fake.add("npm", "left-pad", "1.3.0", timedelta(hours=2))
    assert cli.main(["check", "."]) == 1
    out = capsys.readouterr().out
    assert "left-pad 1.3.0" in out and "2h old" in out
    assert cli.main(["check", "--min-age", "1h"]) == 0
    assert cli.main(["check", "--exclude", "left-pad"]) == 0


def test_unknown_exits_one_unless_warned(project, fake, capsys):
    fake.times.pop(("npm", "ms"))
    assert cli.main(["check"]) == 1
    assert cli.main(["check", "--unknown", "warn"]) == 0


def test_json_output_to_a_file_with_summary_and_sarif(project, fake, capsys):
    fake.add("npm", "ms", "2.1.3", timedelta(hours=1))
    code = cli.main(["check", "--format", "json", "--output", "out.json", "--summary", "summary.md", "--sarif", "out.sarif"])
    assert code == 1
    assert capsys.readouterr().out == ""
    data = json.loads((project / "out.json").read_text())
    assert data["summary"]["unripe"] == 1
    assert (project / "summary.md").read_text().startswith("### unripe: 1 version too new")
    assert json.loads((project / "out.sarif").read_text())["runs"][0]["results"][0]["ruleId"] == "unripe/too-new"


def test_summary_appends(project):
    (project / "summary.md").write_text("earlier step\n")
    cli.main(["check", "--summary", "summary.md"])
    assert (project / "summary.md").read_text().startswith("earlier step\n### unripe:")


def test_config_file_is_read(project, fake, capsys):
    fake.add("npm", "left-pad", "1.3.0", timedelta(days=5))
    assert cli.main(["check"]) == 0
    (project / ".unripe.toml").write_text('min-age = "1w"\n')
    assert cli.main(["check"]) == 1
    assert cli.main(["check", "--min-age", "3d"]) == 0


def test_config_paths_are_used_when_none_are_given(project, capsys):
    (project / "web").mkdir()
    (project / "package-lock.json").rename(project / "web" / "package-lock.json")
    (project / ".unripe.toml").write_text('paths = ["web"]\n')
    assert cli.main(["check"]) == 0
    assert "web/package-lock.json" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, message",
    [
        (["check", "--min-age", "soon"], "not a duration"),
        (["check", "missing-dir"], "no such file or directory: missing-dir"),
        (["check", "--config", "nope.toml"], "no such config file"),
    ],
)
def test_usage_errors_exit_two(project, capsys, argv, message):
    assert cli.main(argv) == 2
    assert message in capsys.readouterr().err


def test_no_lockfiles_exits_two(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check"]) == 2
    assert "no lockfiles found" in capsys.readouterr().out


def test_bad_since_ref_exits_two(project, capsys):
    assert cli.main(["check", "--since", "no-such-ref"]) == 2
    assert "--since no-such-ref" in capsys.readouterr().err


def test_explain(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fake.base = datetime.now(timezone.utc)
    fake.add("pypi", "typing-extensions", "4.9.0", timedelta(days=400))
    fake.add("npm", "fresh", "2.0.0", timedelta(hours=3))
    assert cli.main(["explain", "pypi", "Typing_Extensions", "4.9.0"]) == 0
    assert "old enough" in capsys.readouterr().out
    assert cli.main(["explain", "npm", "fresh", "2.0.0"]) == 1
    assert "unripe: the minimum age is 3d" in capsys.readouterr().out
    assert cli.main(["explain", "npm", "missing", "1.0.0"]) == 1
    assert "publish time unknown" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exit:
        cli.main(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out.strip() == f"unripe {__version__}"
