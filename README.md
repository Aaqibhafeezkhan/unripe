# unripe

unripe checks your lockfiles and fails if any dependency version was
published too recently. The default is 3 days. It works across npm, pnpm,
yarn, bun, PyPI, Cargo, Go, Composer and RubyGems, so a repo with a Python
backend and a JavaScript frontend gets one check and one policy instead of
five different settings.

Here it is on Discourse's `Gemfile.lock`, the evening of October 20, 2026:

```
$ unripe check
unripe: checked 339 versions in 1 lockfile, minimum age 3d

unripe: 2 versions published less than 3d ago
  rubygems aws-sdk-core 3.259.0  7h old, published 2026-10-20 19:21 UTC, old enough at 2026-10-23 19:21 UTC
           Gemfile.lock:1014
  rubygems logster 3.0.0         35h old, published 2026-10-19 15:32 UTC, old enough at 2026-10-22 15:32 UTC
           Gemfile.lock:1126

fail: 2 unripe, 0 unknown, 337 ok, 0 excluded
```

Nothing is wrong with either of those gems. That's normal. Most new
versions are fine. The point is that when one isn't, you find out after
everyone else does, not before.

## Why bother

When a popular package gets hijacked, the bad version is usually spotted
and pulled within hours. In August 2026 a worm went through npm and
published malicious versions of
[444 packages](https://cybernews.com/security/npm-supply-chain-attack-shai-hulud-worm/)
in about four hours, stealing CI and cloud credentials from whoever
installed them. In March, two backdoored LiteLLM releases sat on PyPI for
somewhere between
[40 minutes and three hours](https://docs.litellm.ai/blog/security-update-march-2026).
Waiting a few days before taking a new version would have skipped both.

npm, pnpm, yarn, bun and uv have each added a setting for this in the last
year or so, and if you use one of them you should turn it on. unripe is for
what those settings don't do. It checks what is already in the lockfile,
including whatever a bot or an old tool put there. It reports on just the
versions a pull request adds. And it covers Go, Composer and RubyGems, which
have no setting of their own.

## Install

You need Python 3.11 or newer. unripe has no dependencies, on purpose: a
tool that checks your supply chain shouldn't add to it.

```
uvx unripe check
```

or `pipx install unripe`, or `pip install unripe`.

## Lockfiles it reads

| File | Publish times come from |
|---|---|
| `package-lock.json`, `npm-shrinkwrap.json` | npm registry |
| `pnpm-lock.yaml` | npm registry |
| `yarn.lock` (classic and berry) | npm registry |
| `bun.lock` | npm registry |
| `uv.lock` | the lockfile's own upload times |
| `poetry.lock`, `Pipfile.lock` | PyPI |
| `requirements*.txt`, `constraints*.txt` | PyPI (exact `==` pins only) |
| `Cargo.lock` | crates.io sparse index |
| `go.mod` | Go module proxy, honouring `GOPROXY` |
| `composer.lock` | the lockfile's own `time` field, else Packagist |
| `Gemfile.lock` | rubygems.org |

Dependencies from git, a local path, a workspace or another registry are
skipped, since there's nothing public to ask about them. The old binary
`bun.lockb` can't be read; bun 1.2 and later write a text `bun.lock`.

For Go it reads `go.mod`, not `go.sum`. Since Go 1.17, `go.mod` lists every
module in the build, while `go.sum` also remembers versions that aren't used.

## In GitHub Actions

```yaml
name: unripe
on: pull_request

permissions:
  contents: read

jobs:
  unripe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: TexWoW/unripe@v0.1.0
        with:
          min-age: 3d
```

On a pull request it only looks at versions the PR adds, compared with the
base commit. It fetches that commit if the checkout is shallow, so you don't
need `fetch-depth: 0`. On a push it checks everything. The report goes to the
job summary.

Pin the action to a full commit sha rather than a tag if you can. The action
runs unripe from its own checkout, so the code that runs is the commit you
pinned, and nothing gets downloaded from PyPI.

Inputs:

| Input | Default | |
|---|---|---|
| `min-age` | `3d` | `90m`, `72h`, `3d`, `1w` |
| `since` | PR base | a git ref, or `none` to check everything |
| `paths` | whole repo | files or directories, space or newline separated |
| `exclude` | | patterns, one per line |
| `unknown` | `fail` | set to `warn` to let unknown ages pass |
| `summary` | `true` | write the job summary |
| `sarif` | | also write SARIF to this path |
| `config` | | a config file other than the defaults |

If you want the results as code scanning alerts on the lockfile lines, write
SARIF and upload it (this needs `security-events: write`):

```yaml
      - uses: TexWoW/unripe@v0.1.0
        with:
          sarif: unripe.sarif
      - uses: github/codeql-action/upload-sarif@v4
        if: always()
        with:
          sarif_file: unripe.sarif
```

## As a pre-commit hook

```yaml
repos:
  - repo: https://github.com/TexWoW/unripe
    rev: v0.1.0
    hooks:
      - id: unripe
```

It runs when a lockfile changes. Add `args: [--since, HEAD]` if you only
want it to look at what the commit adds.

## Configuration

Put settings in `.unripe.toml`, or under `[tool.unripe]` in
`pyproject.toml`. Flags on the command line win. Excludes from both are
combined.

```toml
min-age = "3d"
unknown = "fail"
paths = ["web", "api"]
exclude = [
  "npm:@myorg/*",
  "left-pad@1.3.0",
]
```

An exclude is a package name or a glob. Prefix it with an ecosystem
(`npm:`, `pypi:`, `cargo:`, `go:`, `composer:`, `rubygems:`) to limit it to
one, and add `@version` to limit it to certain versions, like
`@types/node@20.*`. PyPI names are normalised the way PyPI does it, so
`Typing_Extensions` and `typing-extensions` are the same package.

## Command line

```
unripe check [paths...]
  --min-age AGE         default 3d
  --since REF           only versions added since this git ref
  --exclude PATTERN     can be repeated
  --unknown fail|warn   default fail
  --format FORMAT       text, json, sarif or markdown
  --output FILE         write the report here instead of stdout
  --summary FILE        also append a markdown report, e.g. $GITHUB_STEP_SUMMARY
  --sarif FILE          also write a SARIF report
  --config FILE
  --no-cache
  --verbose             list the versions that passed too

unripe explain ECOSYSTEM NAME VERSION
```

`unripe` with no command runs `unripe check`. It exits 0 when everything is
old enough, 1 when something is too new or has an unknown age, and 2 when a
lockfile can't be read, no lockfile was found, or an option is wrong.

`explain` is handy for a single question:

```
$ unripe explain npm left-pad 1.3.0
npm left-pad 1.3.0: published 2018-04-09 01:10 UTC, 8y ago
old enough: the minimum age is 3d
```

## Details worth knowing

If a registry can't tell unripe when a version was published, because it's
down or rate limiting or has never heard of the version, that version counts
as unknown, and unknown fails. A check that quietly passes when the registry
is unreachable is one an attacker can turn off. Use `--unknown warn` if you'd
rather not.

Publish times never change, so answers are cached on disk forever, in
`~/.cache/unripe` (`%LOCALAPPDATA%\unripe\cache` on Windows), or wherever
`UNRIPE_CACHE` points. A second run on the same lockfiles makes no requests
at all. Each version is looked up once even if several lockfiles have it,
and lookups run 16 at a time. For npm, Cargo, Composer and RubyGems one
request covers every version of a package.

Mirrors work through `UNRIPE_NPM_REGISTRY` and `UNRIPE_PYPI_URL`. For Go,
unripe uses the first proxy in `GOPROXY`.

The Go check is weaker than the rest. The module proxy reports a version's
commit time, not when it was first published, so a tag pushed today on an
older commit looks old, and git lets anyone set any commit time. I haven't
found a public record of when the proxy first saw a version that could be
used instead.

`uv.lock` and `composer.lock` carry publish times themselves and unripe
trusts them. Someone who can edit your lockfile can change those times, but
they could also point it at any package they like, so that isn't something
unripe tries to defend against.

## Next to the built-in settings

| | Setting |
|---|---|
| npm 11.10+ | `min-release-age` in `.npmrc`, in days |
| pnpm 10.16+ | `minimumReleaseAge` in `pnpm-workspace.yaml`, in minutes |
| yarn 4.10+ | `npmMinimalAgeGate` in `.yarnrc.yml` |
| bun 1.3+ | `minimumReleaseAge` in `bunfig.toml`, in seconds |
| uv | `exclude-newer`, a date or a duration like `7d` |
| Cargo, Go, Composer, Bundler | nothing built in as of October 2026 |

These stop a new version at install time, which unripe can't do, so use
them where you can. unripe is the check in CI afterwards: one policy for the
whole repo, run against what actually got locked.

## Using it from Python

```python
import unripe

for finding in unripe.scan(["."], min_age="3d", exclude=["npm:@myorg/*"]):
    if finding.state != "ok":
        print(finding.package, finding.state, finding.reason)
```

Each finding's state is `ok`, `unripe`, `unknown` or `excluded`. `scan()`
raises `unripe.check.Problem` if a lockfile can't be read or the `since` ref
doesn't exist.

## Questions

**Our own packages are always new.** Exclude them, e.g. `npm:@myorg/*`.

**We use a private registry.** unripe can't see into it, so those packages
come back unknown. Exclude them, or use `--unknown warn`. For private Go
modules, point `GOPROXY` at your own proxy.

**Does this replace `npm audit` or similar?** No. Audit tools know about
reported vulnerabilities. unripe doesn't know anything about any package. It
just won't trust a version until it has been around for a while. They catch
different things, so run both.

**Is it slow?** The first run on a big repo takes a while. Twenty real
lockfiles with about 7,800 versions between them took 82 seconds cold and
1 second after that.

## License

MIT
