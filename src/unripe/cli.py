"""The unripe command."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from unripe import __version__
from unripe.check import Problem, check, judge
from unripe.lockfiles.python import normal
from unripe.model import ECOSYSTEMS, Package
from unripe.policy import PolicyError, build, config
from unripe.registries import Cache, Registries, cachedir
from unripe.report import FORMATS, explanation, render

COMMANDS = ("check", "explain")


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="unripe",
        description="Fail when a locked dependency version was published more recently than you trust.",
    )
    top.add_argument("--version", action="version", version=f"unripe {__version__}")
    commands = top.add_subparsers(dest="command", metavar="command")

    run = commands.add_parser(
        "check",
        help="check every lockfile under the given paths (the default command)",
        description="Check every lockfile under the given files and directories. Exit 0 when every version "
        "is old enough, 1 when one is not (or its age is unknown), 2 when a lockfile could not be read.",
    )
    run.add_argument("paths", nargs="*", help="lockfiles or directories to search (default: the config's paths, or .)")
    run.add_argument("--min-age", metavar="AGE", help="how old a version must be, like 90m, 72h, 3d or 1w (default 3d)")
    run.add_argument("--since", metavar="REF", help="only check versions added since this git ref, like origin/main")
    run.add_argument(
        "--exclude",
        metavar="PATTERN",
        action="append",
        default=[],
        help="skip packages matching a name or glob, optionally with an ecosystem (npm:@types/*) or a version (name@1.2.3). repeatable",
    )
    run.add_argument("--unknown", choices=("fail", "warn"), help="what an unknown publish time does (default fail)")
    run.add_argument("--format", choices=FORMATS, default="text", help="report format (default text)")
    run.add_argument("--output", metavar="FILE", help="write the report to a file instead of stdout")
    run.add_argument("--summary", metavar="FILE", help="also append a markdown report to this file, like $GITHUB_STEP_SUMMARY")
    run.add_argument("--sarif", metavar="FILE", help="also write a SARIF report to this file, for code scanning")
    run.add_argument("--config", metavar="FILE", help="read settings from this file instead of .unripe.toml or pyproject.toml")
    run.add_argument("--no-cache", action="store_true", help="ask the registries again instead of using cached publish times")
    run.add_argument("--verbose", "-v", action="store_true", help="list the versions that passed too")

    one = commands.add_parser(
        "explain",
        help="say when one version was published and whether it passes",
        description="Look up one version and say when it was published and whether it is old enough.",
    )
    one.add_argument("ecosystem", choices=ECOSYSTEMS, help="which registry to ask")
    one.add_argument("name", help="the package, crate, module or gem name")
    one.add_argument("version", help="the exact version")
    one.add_argument("--min-age", metavar="AGE", default=None, help="how old a version must be (default 3d)")
    one.add_argument("--no-cache", action="store_true", help="ask the registry again instead of using the cache")
    return top


def registries(nocache: bool, mavenrepos: list[str] | None = None) -> Registries:
    return Registries(Cache(None if nocache else cachedir()), mavenrepos=mavenrepos or ())


def progress(done: int, total: int) -> None:
    sys.stderr.write(f"\rchecking {total} versions: {done}/{total}")
    if done == total:
        sys.stderr.write("\r" + " " * 40 + "\r")
    sys.stderr.flush()


def write(path: str, content: str, append: bool = False) -> None:
    with open(path, "a" if append else "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def runcheck(args: argparse.Namespace) -> int:
    settings, source = config(".", args.config)
    policy = build(settings, args.min_age, args.exclude, args.unknown, source)
    paths = args.paths or policy.paths or ["."]
    missing = [path for path in paths if not Path(path).exists()]
    if missing:
        raise PolicyError(f"no such file or directory: {', '.join(missing)}")
    show = progress if sys.stderr.isatty() else None
    result = check(paths, policy, registries(args.no_cache, policy.maven_repositories), since=args.since, progress=show)

    report = render(result, args.format, args.verbose)
    if args.output:
        write(args.output, report)
    else:
        sys.stdout.write(report)
    if args.summary:
        write(args.summary, render(result, "markdown"), append=True)
    if args.sarif:
        write(args.sarif, render(result, "sarif"))
    return result.exitcode


def runexplain(args: argparse.Namespace) -> int:
    settings, source = config(".")
    policy = build(settings, args.min_age, (), None, source)
    name = normal(args.name) if args.ecosystem == "pypi" else args.name
    version = args.version.lstrip("v") if args.ecosystem == "composer" else args.version
    package = Package(args.ecosystem, name, version)
    found = registries(args.no_cache, policy.maven_repositories)
    finding = judge(package, policy, found, datetime.now(timezone.utc))
    found.cache.save()
    sys.stdout.write(explanation(finding, policy.min_age))
    if finding.state == "excluded":
        sys.stdout.write(f"excluded by the config: {finding.reason}\n")
    return {"ok": 0, "excluded": 0, "unripe": 1}.get(finding.state, 1)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `unripe`, `unripe .` and `unripe --min-age 7d` all mean `unripe check ...`
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "check")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parser().parse_args(argv)
    try:
        if args.command == "explain":
            return runexplain(args)
        return runcheck(args)
    except (PolicyError, Problem) as error:
        sys.stderr.write(f"unripe: {error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("unripe: stopped\n")
        return 130
    except OSError as error:
        sys.stderr.write(f"unripe: {error}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())

