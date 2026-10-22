from datetime import timedelta

import pytest

from unripe import native


@pytest.mark.parametrize(
    "text, age",
    [
        ("P7D", timedelta(days=7)),
        ("PT36H", timedelta(hours=36)),
        ("P1W", timedelta(weeks=1)),
        ("7 days", timedelta(days=7)),
        ("3d", timedelta(days=3)),
        ("1 week", timedelta(weeks=1)),
        ("90 minutes", timedelta(minutes=90)),
        ("2026-10-01T00:00:00Z", None),
        ("P", None),
    ],
)
def test_spelled_durations(text, age):
    assert native.spelled(text) == age


def test_every_setting_is_read_in_its_own_unit(tmp_path):
    files = {
        ".npmrc": "registry=https://registry.npmjs.org/\nmin-release-age=3\n",
        "pnpm-workspace.yaml": "packages:\n  - web\nminimumReleaseAge: 4320  # three days\n",
        ".yarnrc.yml": 'npmMinimalAgeGate: "2d"\n',
        "bunfig.toml": "[install]\nminimumReleaseAge = 259200\n",
        "pyproject.toml": '[tool.uv]\nexclude-newer = "1 week"\n',
        "poetry.toml": "[solver]\nmin-release-age = 5\n",
        "deno.json": '{"minimumDependencyAge": "P2D"}\n',
        ".cargo/config.toml": '[registry]\nglobal-min-publish-age = "3 days"\n',
        ".bundle/config": '---\nBUNDLE_COOLDOWN: "4"\n',
        ".github/dependabot.yml": "updates:\n  - package-ecosystem: npm\n    cooldown:\n      default-days: 7\n",
        "renovate.json": '{"minimumReleaseAge": "3 days"}\n',
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    found = {setting.tool: setting.age for setting in native.find(tmp_path)}
    assert found == {
        "npm": timedelta(days=3),
        "pnpm": timedelta(days=3),
        "yarn": timedelta(days=2),
        "bun": timedelta(days=3),
        "uv": timedelta(weeks=1),
        "poetry": timedelta(days=5),
        "deno": timedelta(days=2),
        "cargo": timedelta(days=3),
        "bundler": timedelta(days=4),
        "dependabot": timedelta(days=7),
        "renovate": timedelta(days=3),
    }


def test_a_fixed_date_is_found_but_not_compared(tmp_path):
    (tmp_path / "uv.toml").write_text('exclude-newer = "2026-10-01T00:00:00Z"\n')
    [setting] = native.find(tmp_path)
    assert setting.tool == "uv" and setting.age is None and setting.raw == "2026-10-01T00:00:00Z"


def test_hints_follow_the_lockfiles_present(tmp_path):
    for name in ("package-lock.json", "bun.lock", "Gemfile.lock"):
        (tmp_path / name).write_text("{}")
    (tmp_path / "bunfig.toml").write_text("[install]\nminimumReleaseAge = 86400\n")
    tools = [hint.tool for hint in native.hints(tmp_path, native.find(tmp_path), timedelta(days=3))]
    assert tools == ["npm", "bundler"]


def test_deno_object_form(tmp_path):
    (tmp_path / "deno.jsonc").write_text('{\n  // three days\n  "minimumDependencyAge": {"age": "PT72H", "exclude": ["jsr:@std/*"]}\n}\n')
    assert [setting.age for setting in native.find(tmp_path)] == [timedelta(hours=72)]
