# Changelog

## 0.5.1 (2026-10-22)

- Much faster with nothing cached. Each lookup thread keeps its connection
  to a registry open instead of starting a new one for every request:
  2,048 versions across three big lockfiles took 6 to 7 seconds, against
  11 to 33 seconds before, depending on the network.
  Through a proxy, requests go the way they did before.
- An npm version that was unpublished is now reported even when another
  lockfile in the same run had already fetched that package.

## 0.5.0 (2026-10-22)

New things to check:

- Deno: reads `deno.lock` (versions 3 to 5), with npm packages dated by the
  npm registry and JSR packages by jsr.io.
- `pylock.toml`, the standard Python lockfile from PEP 751, using its own
  upload times when it has them.
- GitHub Actions, with `--workflows` or `workflows = true`. Each `uses:` ref
  is dated by the GitHub release that points at the same commit.
- A version npm has unpublished now counts as unknown, since that is how npm
  takes malicious versions down.

Policy:

- `[min-age-for]` sets a different minimum age for an ecosystem
  (`npm = "7d"`) or a package pattern (`"npm:@types/*" = "1d"`).
- `[[allow]]` lets one version through while it is too new, with a reason
  and an `until` date. Allowed versions are reported as `allowed`. unripe says
  when an entry has ended, is no longer needed, or matches nothing.
- `allow-security-fixes = true` lets a too new version through when an
  advisory on osv.dev names that exact version as the fix.
- The config is found in parent directories up to the top of the git
  repository, or wherever `UNRIPE_CONFIG` points.

Reports:

- A version that is too new comes with the newest version in the same line
  that is old enough, for npm, PyPI, Cargo, Composer, RubyGems, NuGet and JSR.
- New formats: `github` (annotations on the lockfile line), `codequality`
  (GitLab) and `junit`. `--report FORMAT:FILE` writes any of them next to the
  normal output, and `--json FILE` writes JSON.
- Colour on a terminal, off with `NO_COLOR`.
- The markdown report lists at most 50 versions, so a pull request comment
  stays readable.

Commands and options:

- `unripe explain ECOSYSTEM NAME` without a version lists the recent
  releases and the newest one that is old enough.
- `unripe config` shows the settings in use and the cooldown settings your
  package managers and bots have, and suggests the ones that are missing.
- `unripe init` writes a starter `.unripe.toml`, and with `--github` a
  workflow.
- `--as-of TIME` checks as if it were then.
- `--exit-zero` reports without failing. `--offline` uses only the cache.
  `--no-suggest` skips the suggestions. `--verbose` also prints how long the
  check took and how many requests it made.
- Company CA bundles from `REQUESTS_CA_BUNDLE` or `UNRIPE_CA_BUNDLE`.

CI:

- The Action marks problems on the lockfile lines (`annotations`), can report
  without failing (`fail: false`), can check workflows (`workflows: true`),
  and has outputs: `result`, `unripe`, `unknown`, `report` and `json`.
- The GitLab template feeds the merge request's code quality widget and test
  report.
- A second pre-commit hook, `unripe-changed`, checks only what a commit adds.
- Tested on Python 3.14.

## 0.4.0 (2026-10-21)

- The GitHub Action can comment on pull requests (`comment: true`). It posts
  one comment when something is too new or unknown and keeps it up to date on
  later runs. If it can't comment, it warns and the result is unchanged.
- A GitLab CI template, `ci/gitlab.yml`, that checks only what a merge request
  adds.

## 0.3.0 (2026-10-21)

- NuGet: reads `packages.lock.json` and asks nuget.org. Unlisted versions,
  which nuget.org dates to 1900, get their real time from the NuGet catalog.
- Gradle: reads `gradle.lockfile` and the older
  `gradle/dependency-locks/*.lockfile`, and asks Maven Central, Google's Maven
  repository and the Gradle plugin portal. Other repositories can be added
  with `maven-repositories` in the config or `UNRIPE_MAVEN_REPOS`.
- Registry answers are fetched gzipped, which makes big npm documents much
  quicker to download.

## 0.2.0 (2026-10-21)

- Go ages can no longer be faked with an old commit. The module proxy only
  knows commit times, which the author controls, so unripe now also asks
  Go's checksum database when a version first appeared and goes by that when
  the commit claims to be older. Honours `GOSUMDB`, `GONOSUMDB` and
  `GOPRIVATE`.
- The README's table of built-in cooldown settings is up to date with pip,
  Bundler, Hex, Cargo and Composer.
- Contributing guide, security policy and issue templates.

## 0.1.0 (2026-10-21)

First release. Reads package-lock.json, npm-shrinkwrap.json, pnpm-lock.yaml,
yarn.lock, bun.lock, uv.lock, poetry.lock, Pipfile.lock, pinned requirements
files, Cargo.lock, go.mod, composer.lock and Gemfile.lock. Reports as text,
JSON, SARIF or markdown. Ships as a command, a GitHub Action and a pre-commit
hook.
