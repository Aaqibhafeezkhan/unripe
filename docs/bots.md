# unripe with Renovate and Dependabot

Update bots open the pull requests that bring new versions in, so they are
where a cooldown matters most. Both have one now. Set it, and set it to at
least unripe's minimum age, so the bots stop opening pull requests that
unripe would fail.

`unripe config` reads the settings below if they are there, says whether
they are shorter than unripe's, and prints the line to add if they are
missing.

## Renovate

```json
{
  "extends": ["config:recommended"],
  "minimumReleaseAge": "3 days",
  "internalChecksFilter": "strict"
}
```

`minimumReleaseAge` holds an update back until the version is that old.
`internalChecksFilter: "strict"` makes Renovate wait instead of opening the
pull request with a pending check. Renovate needs a release timestamp from
the registry to do this, so check how it treats datasources that don't
have one.

## Dependabot

```yaml
version: 2
updates:
  - package-ecosystem: npm
    directory: /
    schedule:
      interval: weekly
    cooldown:
      default-days: 3
```

`cooldown` can also set different days for major, minor and patch updates
(`semver-major-days` and so on) and include or exclude packages. Security
updates skip the cooldown, which is what you want; turn on
`allow-security-fixes = true` in `.unripe.toml` so unripe lets those
through too when an advisory names the version as the fix.

## When the two disagree

If the bot's cooldown is shorter than unripe's minimum age, its pull
requests fail until the version is old enough. That's safe but noisy. Either
raise the bot's cooldown, or re-run the check later: the report says when
each version will be old enough.

If a bot pull request has to go in sooner, add an `[[allow]]` entry for that
exact version with a reason and an `until` date. It shows up in every report
as allowed, and stops applying on its own.

## What unripe adds on top

The bot's cooldown only covers the pull requests the bot opens. unripe
checks everything in the lockfile, however it got there: a person running
`npm install`, an agent adding a package, a transitive dependency that
moved, a lockfile from before the cooldown was turned on. It also covers
ecosystems where the bot has no publish time to wait on.
