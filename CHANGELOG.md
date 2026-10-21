# Changelog

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
