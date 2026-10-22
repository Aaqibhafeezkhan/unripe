# Past attacks, replayed

`--as-of` checks a lockfile as if it were an earlier moment, using the
publish times the registries still have. That makes it possible to check
whether a cooldown would have helped in a real incident, instead of taking
it on faith. Every output below is from a real run against the live
registries on October 22, 2026, with a lockfile holding the bad version and
one old package beside it.

You can repeat any of them: write the lockfile, run the command.

## nx, August 2025

Malicious versions of the `nx` build system went out on the evening of
August 26, 2025, and used AI command line tools on developers' machines to
look for secrets. Checked two hours after the first one:

```
$ unripe check --as-of 2025-08-27T00:30Z
unripe: checked 2 versions in 1 lockfile, minimum age 3d

unripe: 1 version published less than 3d ago
  npm      nx 21.5.0  1h old, published 2025-08-26 22:32 UTC, old enough at 2025-08-29 22:32 UTC
           package-lock.json:8
           21.4.1 is old enough (published 2025-08-22)

fail: 1 unripe, 0 unknown, 1 ok, 0 excluded
```

npm has since unpublished it, so the same lockfile today fails as unknown:

```
$ unripe check
unripe: checked 2 versions in 1 lockfile, minimum age 3d

unknown: 1 version could not be dated, which fails by default (--unknown warn to allow)
  npm      nx 21.5.0  unpublished from the npm registry, which is how npm takes down malicious versions
           package-lock.json:8

fail: 0 unripe, 1 unknown, 1 ok, 0 excluded
```

## debug and chalk, September 2025

A phished maintainer account published bad versions of `debug`, `chalk` and
other packages with billions of weekly downloads between them. The
[README](../README.md#why-bother) has the run, as of two hours after they
went out.

## @ctrl/tinycolor, September 2025

One of the first packages the Shai-Hulud worm republished, on September 15,
2025. As of that evening:

```
$ unripe check --as-of 2025-09-15T22:00Z
unripe: checked 2 versions in 1 lockfile, minimum age 3d

unripe: 1 version published less than 3d ago
  npm      @ctrl/tinycolor 4.1.1  2h old, published 2025-09-15 19:52 UTC, old enough at 2025-09-18 19:52 UTC
           package-lock.json:8
           4.1.0 is old enough (published 2024-04-13)

fail: 1 unripe, 0 unknown, 1 ok, 0 excluded
```

## LiteLLM, March 2026

Two backdoored LiteLLM releases, 1.82.7 and 1.82.8, were uploaded to PyPI
and deleted within hours. PyPI no longer has them at all, so their exact
upload times can't be replayed. A lockfile that still pins one fails today:

```
$ unripe check
unripe: checked 2 versions in 1 lockfile, minimum age 3d

unknown: 1 version could not be dated, which fails by default (--unknown warn to allow)
  pypi     litellm 1.82.7  not found on PyPI
           requirements.txt:1

fail: 0 unripe, 1 unknown, 1 ok, 0 excluded
```

## What these show, and what they don't

In each case the bad version was hours old when people installed it, and a
three day minimum would have refused it. That is the attack a cooldown is
for. It says nothing about attacks that stay hidden longer than your minimum
age; [threat-model.md](threat-model.md) covers what unripe doesn't do.

If you replay an incident that isn't here, a pull request adding it is
welcome. Include the command and the output as it ran.
