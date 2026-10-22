# Contributing

Thanks for helping. A few things that will make it easier.

## Running the tests

You need Python 3.11 or newer and pytest.

```
python -m pip install pytest
python -m pytest -q
```

The tests never touch the network. `tests/conftest.py` has a fake registry
that answers the way each real registry does, so the code that builds URLs
and reads answers is tested too. If a test needs a registry, use the `fake`
fixture and `fake.add(ecosystem, name, version, age)`.

## The rules unripe keeps

- No runtime dependencies. Only the standard library. A supply-chain tool
  shouldn't bring its own supply chain.
- Python 3.11 is the oldest version supported.
- Unknown is never ok. If a registry can't answer, the version is
  `unknown`, which fails by default. Don't add code paths that quietly pass.
- A broken lockfile is reported as unreadable, never treated as empty.
- Messages and docs are short and plain.

## Adding an ecosystem

This is the most useful contribution right now. The pieces are:

1. A parser in `src/unripe/lockfiles/`, a function that takes the lockfile's
   text and path and returns a list of `Package`. Skip anything that doesn't
   come from the public registry (git, path, workspace, other registries).
   Register the filename in `PARSERS` in `src/unripe/lockfiles/__init__.py`.
2. A lookup in `src/unripe/registries.py`, a method on `Registries` that
   returns when a version was published, added to `self.lookups`. If one
   request answers for every version of a package, write a method that
   returns every version's time, add it to `self.listings`, and have the
   lookup call `self.every(package)` so it is fetched once. Being in
   `self.listings` also lets unripe suggest an older version that passes.
3. The ecosystem's name in `ECOSYSTEMS` and `REGISTRY` in `src/unripe/model.py`.
4. A real lockfile in `tests/testdata/<name>/` with the awkward cases in it,
   tests in `tests/test_lockfiles.py`, and the registry's answer shape in
   the fake in `tests/conftest.py`.
5. A row in the README's table.

Real lockfiles are much better fixtures than made up ones. Generate one with
the package manager if you can, then add the odd entries by hand.

## Pull requests

Keep them focused on one thing, and say how you tested it. If you used an AI
tool, that's fine, just say so in the description.
