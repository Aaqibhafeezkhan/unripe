import json
from datetime import datetime, timezone

import pytest

from conftest import TESTDATA
from unripe import lockfiles
from unripe.lockfiles.javascript import jsonc, pnpmkey


def parse(relative: str):
    path = TESTDATA / relative
    return lockfiles.read(path.read_text("utf-8"), path)


def pairs(relative: str) -> set[tuple[str, str]]:
    return {(p.name, p.version) for p in parse(relative)}


NPM_V3 = {
    ("@types/node", "20.11.0"),
    ("ansi-regex", "5.0.1"),
    ("emoji-regex", "8.0.0"),
    ("is-fullwidth-code-point", "3.0.0"),
    ("is-number", "7.0.0"),
    ("left-pad", "1.3.0"),
    ("ms", "2.1.2"),
    ("ms", "2.1.3"),
    # locked under the alias string-width-cjs
    ("string-width", "4.2.3"),
    ("strip-ansi", "6.0.1"),
    ("undici-types", "5.26.5"),
}


def test_package_lock_v3_skips_links_git_and_direct_tarballs():
    assert pairs("npm-v3/package-lock.json") == NPM_V3


def test_package_lock_v1_walks_nested_and_skips_bundled_and_git():
    assert pairs("npm-v1/package-lock.json") == {
        ("debug", "4.3.4"),
        ("ms", "2.1.2"),
        ("ms", "2.1.3"),
        ("left-pad", "1.3.0"),
        ("string-width", "4.2.3"),
        ("wrapper", "1.0.0"),
    }


def test_every_package_records_its_lockfile_and_ecosystem():
    for package in parse("npm-v3/package-lock.json"):
        assert package.ecosystem == "npm"
        assert package.lockfile.endswith("npm-v3/package-lock.json")
        assert "\\" not in package.lockfile


def test_pnpm_v9():
    assert pairs("pnpm-v9/pnpm-lock.yaml") == (NPM_V3 - {("ms", "2.1.2")}) | {("react", "18.2.0")}


def test_pnpm_v6_drops_peer_suffix_and_skips_git_and_file():
    assert pairs("pnpm-v6/pnpm-lock.yaml") == {
        ("@babel/core", "7.24.0"),
        ("js-tokens", "4.0.0"),
        ("loose-envify", "1.4.0"),
        ("react", "18.2.0"),
        ("react-dom", "18.2.0"),
        ("scheduler", "0.23.0"),
    }


def test_pnpm_v5_slash_keys_with_peer_and_hash_suffixes():
    assert pairs("pnpm-v5/pnpm-lock.yaml") == {
        ("@babel/core", "7.24.0"),
        ("js-tokens", "4.0.0"),
        ("react", "18.2.0"),
        ("react-dom", "18.2.0"),
        ("styled-jsx", "5.1.1"),
    }


@pytest.mark.parametrize(
    "key, expected",
    [
        ("foo/1.0.0_react@18.0.0", ("foo", "1.0.0")),
        ("@scope/name/1.0.0_a@1.0.0+b@2.0.0", ("@scope/name", "1.0.0")),
        ("@scope/name@1.0.0", ("@scope/name", "1.0.0")),
        ("name@1.0.0-beta.1", ("name", "1.0.0-beta.1")),
    ],
)
def test_pnpm_keys(key, expected):
    assert pnpmkey(key) == expected


def test_yarn_classic_skips_git_and_file_and_decodes_scopes():
    assert pairs("yarn-classic/yarn.lock") == {
        ("@babel/code-frame", "7.23.5"),
        ("@scope/encoded", "2.0.1"),
        ("left-pad", "1.3.0"),
        ("string-width", "4.2.3"),
    }


def test_yarn_berry_skips_workspace_git_and_patch():
    assert pairs("yarn-berry/yarn.lock") == {
        ("@babel/code-frame", "7.23.5"),
        ("left-pad", "1.3.0"),
        ("resolve", "1.22.8"),
        ("string-width", "4.2.3"),
    }


def test_bun_lock_with_comments_and_trailing_commas():
    assert pairs("bun/bun.lock") == {
        ("@types/node", "20.11.0"),
        ("left-pad", "1.3.0"),
        ("string-width", "4.2.3"),
        ("undici-types", "5.26.5"),
    }


def test_jsonc_keeps_strings_intact():
    text = '{"a": "// not a comment", "b": "/* nor this */", "c": [1, 2,], /* gone */ "d": "x\\"y",}'
    assert json.loads(jsonc(text)) == {"a": "// not a comment", "b": "/* nor this */", "c": [1, 2], "d": 'x"y'}


def test_bun_lockb_is_reported_unreadable():
    path = TESTDATA / "bun-binary/bun.lockb"
    with pytest.raises(lockfiles.Unreadable, match="binary"):
        lockfiles.read(path.read_bytes().decode("utf-8", "replace"), path)


def test_uv_takes_upload_times_and_skips_editable_git_and_other_indexes():
    found = {p.name: p for p in parse("uv/uv.lock")}
    assert set(found) == {"idna", "six", "zope-interface"}
    # the earliest file: here the wheel, two seconds before the sdist
    assert found["idna"].published == datetime(2023, 11, 25, 15, 40, 52, 604000, tzinfo=timezone.utc)
    assert found["zope-interface"].published is None


def test_poetry_keeps_pypi_and_pypi_mirrors_only():
    assert pairs("poetry/poetry.lock") == {("certifi", "2024.2.2"), ("pytest", "8.0.0"), ("typing-extensions", "4.9.0")}


def test_pipfile_lock_exact_versions_only():
    assert pairs("pipenv/Pipfile.lock") == {("django", "5.0.2"), ("sqlparse", "0.4.4"), ("pytest", "8.0.0")}


def test_requirements_exact_pins_only():
    assert pairs("requirements/requirements.txt") == {("certifi", "2024.2.2"), ("django", "5.0.2"), ("zope-interface", "6.2")}


def test_cargo_lock_crates_io_only():
    assert pairs("cargo/Cargo.lock") == {("itoa", "1.0.10"), ("memchr", "2.7.1"), ("serde", "1.0.197")}


def test_go_mod_applies_replacements():
    assert pairs("go/go.mod") == {
        ("github.com/BurntSushi/toml", "v1.3.2"),
        ("github.com/new/thing", "v1.2.0"),
        ("github.com/pkg/errors", "v0.9.1"),
        ("golang.org/x/sync", "v0.6.0"),
        ("golang.org/x/text", "v0.14.0"),
    }


def test_composer_lock_carries_times_and_skips_dev_branches():
    found = {p.name: p for p in parse("composer/composer.lock")}
    assert set(found) == {"monolog/monolog", "old/no-zone", "psr/log", "symfony/console", "phpunit/phpunit"}
    assert found["monolog/monolog"].published == datetime(2023, 10, 27, 15, 32, 31, tzinfo=timezone.utc)
    assert found["old/no-zone"].published == datetime(2015, 10, 2, 12, 0, tzinfo=timezone.utc)
    assert found["old/no-zone"].version == "1.0.0"
    assert found["symfony/console"].published is None


def test_gemfile_lock_rubygems_org_only_with_platforms():
    assert pairs("ruby/Gemfile.lock") == {("nokogiri", "1.16.0-x86_64-linux"), ("racc", "1.7.3"), ("rack", "3.0.9")}


@pytest.mark.parametrize(
    "name, text",
    [
        ("package-lock.json", "{not json"),
        ("Cargo.lock", "[[package]\nname = "),
        ("uv.lock", "version = = 1"),
    ],
)
def test_broken_lockfiles_are_unreadable_not_empty(name, text):
    with pytest.raises(lockfiles.Unreadable, match="could not be read"):
        lockfiles.read(text, name)


def test_find_walks_directories_and_skips_installed_copies(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "package-lock.json").write_text("{}")
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "package-lock.json").write_text("{}")
    (tmp_path / "testdata").mkdir()
    (tmp_path / "testdata" / "go.mod").write_text("module x")
    (tmp_path / "requirements-dev.txt").write_text("")
    (tmp_path / "notes.txt").write_text("")
    found = {p.relative_to(tmp_path).as_posix() for p in lockfiles.find([str(tmp_path)])}
    assert found == {"app/package-lock.json", "requirements-dev.txt"}


def test_find_reads_a_file_named_twice_once(tmp_path):
    lock = tmp_path / "Cargo.lock"
    lock.write_text("")
    assert len(lockfiles.find([str(tmp_path), str(lock)])) == 1


def test_find_prefers_bun_lock_over_the_old_binary(tmp_path):
    (tmp_path / "bun.lock").write_text("{}")
    (tmp_path / "bun.lockb").write_bytes(b"\0")
    assert [p.name for p in lockfiles.find([str(tmp_path)])] == ["bun.lock"]
