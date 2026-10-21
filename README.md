# unripe

Fail the build when a locked dependency version is younger than you trust.

unripe reads every lockfile in a repository, asks each package registry when
every locked version was published, and fails when any of them is newer than
your policy (3 days by default). One command and one policy for npm, pnpm,
yarn, bun, PyPI, Cargo, Go, Composer and RubyGems.

```
$ uvx unripe check
unripe: checked 412 versions in 3 lockfiles, minimum age 3d

unripe: 1 version published less than 3d ago
  npm      keyv 6.0.0  5h old, published 2026-08-04 09:12 UTC, old enough at 2026-08-07 09:12 UTC
           web/package-lock.json:2214

fail: 1 unripe, 0 unknown, 411 ok, 0 excluded
```

## Why

Malicious releases are usually found and pulled within hours or days. If you
never install a version until it has been public for a few days, most of them
never reach you.

- In August 2026 a self-spreading npm worm published malicious versions of
  [444 packages](https://cybernews.com/security/npm-supply-chain-attack-shai-hulud-worm/)
  in a few hours, starting with `keyv@6.0.0`, and stole CI and cloud
  credentials from anyone who installed them.
- In March 2026 two backdoored LiteLLM releases (1.82.7 and 1.82.8)
  [were on PyPI](https://docs.litellm.ai/blog/security-update-march-2026)
  for between 40 minutes and three hours, depending on who you ask, before
  they were quarantined.

Both were caught fast. A 3 day wait would have skipped both.

Package managers have started adding their own "minimum release age"
settings, under different names and units, and several ecosystems have none.
unripe is for the parts they do not cover:

- one policy across a repository that mixes ecosystems
- versions already pinned in a lockfile, not only new installs
- a report for pull requests that only looks at versions the PR adds
- Go, Composer and RubyGems, which have no setting of their own

## Install

unripe needs Python 3.11 or later and has no dependencies of its own. A tool
that checks your supply chain should not bring one.

```
uvx unripe check          # run without installing
pipx install unripe       # or install it
pip install unripe
```

## What it reads

| Lockfile | Asks |
|---|---|
| `package-lock.json`, `npm-shrinkwrap.json` (v1 to v3) | npm registry |
| `pnpm-lock.yaml` (v5 to v9) | npm registry |
| `yarn.lock` (yarn 1 and yarn 2+) | npm registry |
| `bun.lock` | npm registry |
| `uv.lock` | its own upload times, else PyPI |
| `poetry.lock`, `Pipfile.lock` | PyPI |
| `requirements*.txt`, `constraints*.txt` (exact `==` pins only) | PyPI |
| `Cargo.lock` | crates.io sparse index |
| `go.mod` | Go module proxy (`GOPROXY` is honoured) |
| `composer.lock` | its own `time` field, else Packagist |
| `Gemfile.lock` | rubygems.org |

Git, path, workspace and file dependencies are skipped, since there is no
registry to ask about them. So are packages from another registry, such as a
private index in `uv.lock` or a second `GEM` remote. `bun.lockb` is binary
and is reported as unreadable: bun 1.2 and later write a text `bun.lock`.

`go.mod` is read rather than `go.sum`, because since Go 1.17 `go.mod` lists
every module in the build, while `go.sum` also keeps versions that are never
built.

## GitHub Action

```yaml
on: pull_request

permissions:
  contents: read

jobs:
  unripe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: TexWoW/unripe@v0.1.0  # better: pin the full commit sha
        with:
          min-age: 3d
```

On a pull request the action only checks versions the pull request adds (it
compares against the base commit, fetching it if the checkout is shallow), and
writes a report to the job summary. On a push it checks everything.

| Input | Default | |
|---|---|---|
| `min-age` | `3d` | how old every version must be: `90m`, `72h`, `3d`, `1w` |
| `since` | the PR base | a git ref; `none` checks everything |
| `paths` | whole repository | lockfiles or directories, separated by spaces or new lines |
| `exclude` | | one pattern per line, see [Excluding packages](#excluding-packages) |
| `unknown` | `fail` | `warn` lets versions whose age is unknown pass |
| `summary` | `true` | write the markdown report to the job summary |
| `sarif` | | also write SARIF to this path |
| `config` | | a config file other than `.unripe.toml` or `pyproject.toml` |

The action runs unripe straight from its own checkout, so the code that runs
is exactly the commit you pinned, and nothing is downloaded from PyPI. It uses
[astral-sh/setup-uv](https://github.com/astral-sh/setup-uv) (pinned by commit)
to get a Python.

To see problems as code scanning alerts on the lockfile lines:

```yaml
      - uses: TexWoW/unripe@v0.1.0
        with:
          sarif: unripe.sarif
      - uses: github/codeql-action/upload-sarif@v4
        if: always()
        with:
          sarif_file: unripe.sarif
```

(this needs `security-events: write` permission).

## pre-commit

```yaml
repos:
  - repo: https://github.com/TexWoW/unripe
    rev: v0.1.0
    hooks:
      - id: unripe
        # args: [--since, HEAD]   # only check versions this commit adds
```

The hook runs when a lockfile changes and checks that lockfile.

## Configuration

Settings go in `.unripe.toml`, or in `[tool.unripe]` in `pyproject.toml`.
Command line options win over the file, and excludes from both apply.

```toml
# .unripe.toml
min-age = "3d"          # 90m, 72h, 3d, 1w; a bare number means days
unknown = "fail"        # or "warn"
paths = ["web", "api"]  # where to look when no paths are given
exclude = [
  "npm:@myorg/*",       # our own packages, published by our own CI
  "left-pad@1.3.0",     # one version we checked by hand
]
```

### Excluding packages

A pattern is a package name or a glob, matched against the name as the
registry spells it.

- `left-pad`: this package in any ecosystem
- `npm:@types/*`: only in one ecosystem (`npm`, `pypi`, `cargo`, `go`, `composer`, `rubygems`)
- `left-pad@1.3.0`, `@types/node@20.*`: only matching versions
- `golang.org/x/*`: globs work for any name

PyPI names are compared the way PyPI compares them, so `Typing_Extensions`
matches `typing-extensions`.

## Command line

```
unripe check [paths...]   check lockfiles in these files and directories (default: .)
  --min-age AGE           how old a version must be (default 3d)
  --since REF             only check versions added since this git ref
  --exclude PATTERN       skip matching packages; repeatable
  --unknown fail|warn     what an unknown publish time does (default fail)
  --format FORMAT         text, json, sarif or markdown (default text)
  --output FILE           write the report to a file instead of stdout
  --summary FILE          also append a markdown report, like $GITHUB_STEP_SUMMARY
  --sarif FILE            also write a SARIF report
  --config FILE           read this config file
  --no-cache              ask the registries again
  --verbose               list the versions that passed too

unripe explain ECOSYSTEM NAME VERSION
                          say when one version was published and whether it passes
```

`unripe` on its own means `unripe check`.

Exit codes:

- `0`: every version is old enough
- `1`: a version is too new, or its age is unknown (unless `--unknown warn`)
- `2`: a lockfile could not be read, no lockfile was found, or the options were wrong

## How it decides

Each locked version ends up in one of four states:

- **ok**: published at least `min-age` ago
- **unripe**: published more recently
- **unknown**: the registry could not say, because it was down, rate limited, or
  does not have that version. Unknown fails by default. A check that passes
  when the registry is unreachable is a check an attacker can switch off.
- **excluded**: matched an exclude pattern, so it was not looked up

A version that is in two lockfiles is looked up once. Lookups run 16 at a
time. Publish times never change, so every answer is cached on disk for good
(`~/.cache/unripe`, `%LOCALAPPDATA%\unripe\cache` on Windows, or
`$UNRIPE_CACHE`), and a second run on the same lockfiles asks nothing.

To use a mirror, set `UNRIPE_NPM_REGISTRY` or `UNRIPE_PYPI_URL`. Go lookups
use the first proxy in `GOPROXY`.

`uv.lock` and `composer.lock` record publish times themselves, and unripe uses
them. Someone who can edit your lockfile can change those times, but someone
who can edit your lockfile can also point it at any package they like, so
unripe does not try to defend against that.

## How it compares to native settings

Several package managers now have a setting that refuses new versions at
install time. If your repository uses one package manager that has one, turn
it on: it stops a new version before it is installed, which unripe cannot do.

| | Setting |
|---|---|
| npm 11.10+ | `min-release-age` in `.npmrc` (days) |
| pnpm 10.16+ | `minimumReleaseAge` in `pnpm-workspace.yaml` (minutes) |
| yarn 4.10+ | `npmMinimalAgeGate` in `.yarnrc.yml` |
| bun 1.3+ | `minimumReleaseAge` in `bunfig.toml` (seconds) |
| uv | `exclude-newer` (a date, or a duration like `7d` in recent versions) |
| Cargo, Go, Composer, Bundler | none built in at the time of writing |

What unripe adds:

- the same policy, in the same words, across every ecosystem in the repository
- a check of what is already in the lockfile, whoever or whatever wrote it
  (a bot, an older tool, a hand edit)
- a pull request report limited to what the pull request adds, as text,
  markdown, JSON or SARIF
- coverage for Go, Composer and RubyGems

They work well together: the native setting stops the install, unripe checks
the result in CI.

## Library use

```python
import unripe

for finding in unripe.scan(["."], min_age="3d", exclude=["npm:@myorg/*"]):
    if finding.state != "ok":
        print(finding.package.ecosystem, finding.package, finding.state, finding.reason)
```

`scan()` raises `unripe.check.Problem` when a lockfile cannot be read or a
`since` ref does not exist.

## FAQ

**Our own packages are always new.** Exclude them: `npm:@myorg/*`.

**We use a private registry.** Packages unripe cannot find are unknown, which
fails. Exclude them, or run with `--unknown warn`. For private Go modules,
point `GOPROXY` at your proxy or exclude them.

**Is this a replacement for an audit tool?** No. Audit tools know about
published advisories. unripe knows nothing about any package; it only refuses
to trust a version before the world has had a few days to look at it. Use
both.

**How much does it ask the registries?** Once per package for npm, Cargo,
Composer and RubyGems (one answer covers every version), once per version for
PyPI and Go, and never again after that. crates.io lookups use the sparse
index on its CDN, as crates.io asks tools to.

**Does it need network access?** Yes, the first time it sees a version.

## License

MIT
