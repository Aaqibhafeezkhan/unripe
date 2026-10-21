# Changelog

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
