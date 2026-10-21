"""Turning a check's result into text for people, JSON for scripts, SARIF for code scanning, or markdown for PRs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from unripe import __version__
from unripe.check import Result
from unripe.model import Finding
from unripe.policy import spoken

HOME = "https://github.com/TexWoW/unripe"
FORMATS = ("text", "json", "sarif", "markdown")


def ago(seconds: float | None) -> str:
    """An age in the largest units that make sense: `40m`, `5h`, `2d 4h`, `3w`."""
    if seconds is None:
        return "?"
    if seconds < 0:
        return "in the future"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    days = hours // 24
    if days < 14:
        rest = hours % 24
        return f"{days}d {rest}h" if rest else f"{days}d"
    if days < 365:
        return f"{days // 7}w"
    return f"{days // 365}y"


def stamp(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else "?"


def ripe(finding: Finding, result: Result) -> datetime | None:
    """When an unripe version becomes old enough to pass."""
    return finding.published + result.policy.min_age if finding.published else None


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def where(finding: Finding) -> str:
    return ", ".join(f"{path}:{number}" if number else path for path, number in finding.locations)


def scope(result: Result) -> str:
    checked = plural(len(result.findings), "version")
    added = f" added since {result.since}" if result.since else ""
    return f"{checked}{added} in {plural(len(result.lockfiles), 'lockfile')}"


def verdict(result: Result) -> str:
    code = result.exitcode
    if code == 0:
        return "pass"
    if code == 2:
        return "error"
    return "fail"


def text(result: Result, verbose: bool = False) -> str:
    age = spoken(result.policy.min_age)
    out = [f"unripe: checked {scope(result)}, minimum age {age}"]
    if not result.lockfiles:
        out.append("")
        out.append("no lockfiles found. unripe reads package-lock.json, npm-shrinkwrap.json, pnpm-lock.yaml, yarn.lock,")
        out.append("bun.lock, uv.lock, poetry.lock, Pipfile.lock, requirements*.txt, Cargo.lock, go.mod, composer.lock,")
        out.append("Gemfile.lock, packages.lock.json and gradle.lockfile")

    groups = [
        ("unripe", f"published less than {age} ago"),
        ("unknown", "could not be dated, " + ("which fails by default (--unknown warn to allow)" if result.policy.unknown == "fail" else "allowed by --unknown warn")),
    ]
    if verbose:
        groups += [("ok", f"at least {age} old"), ("excluded", "skipped by an exclude rule")]
    for state, meaning in groups:
        chosen = [finding for finding in result.findings if finding.state == state]
        if not chosen:
            continue
        out.append("")
        out.append(f"{state}: {plural(len(chosen), 'version')} {meaning}")
        width = max(len(f"{f.package.name} {f.package.version}") for f in chosen)
        for finding in chosen:
            package = finding.package
            label = f"{package.name} {package.version}".ljust(width)
            if state == "unripe":
                detail = f"{ago(finding.age_seconds)} old, published {stamp(finding.published)}, old enough at {stamp(ripe(finding, result))}"
            elif state == "ok":
                detail = f"{ago(finding.age_seconds)} old"
            else:
                detail = finding.reason
            out.append(f"  {package.ecosystem:<8} {label}  {detail}")
            if state in ("unripe", "unknown"):
                out.append(f"  {'':<8} {where(finding)}")

    for _, message in result.unreadable:
        out.append("")
        out.append(f"could not read {message}")

    counts = [f"{result.count(state)} {state}" for state in ("unripe", "unknown", "ok", "excluded")]
    out.append("")
    out.append(f"{verdict(result)}: {', '.join(counts)}" + (f", {len(result.unreadable)} unreadable" if result.unreadable else ""))
    return "\n".join(out) + "\n"


def jsondoc(result: Result) -> str:
    def finding(f: Finding) -> dict:
        return {
            "ecosystem": f.package.ecosystem,
            "name": f.package.name,
            "version": f.package.version,
            "state": f.state,
            "published": f.published.isoformat() if f.published else None,
            "age_seconds": round(f.age_seconds) if f.age_seconds is not None else None,
            "old_enough_at": ripe(f, result).isoformat() if f.published else None,
            "reason": f.reason or None,
            "locations": [{"lockfile": path, "line": number} for path, number in f.locations],
        }

    document = {
        "unripe": __version__,
        "checked_at": result.checked_at.isoformat(),
        "result": verdict(result),
        "exit_code": result.exitcode,
        "policy": {
            "min_age": spoken(result.policy.min_age),
            "min_age_seconds": int(result.policy.min_age.total_seconds()),
            "unknown": result.policy.unknown,
            "exclude": result.policy.exclude,
        },
        "since": result.since,
        "lockfiles": result.lockfiles,
        "unreadable": [{"lockfile": path, "error": message} for path, message in result.unreadable],
        "summary": {state: result.count(state) for state in ("unripe", "unknown", "ok", "excluded")},
        "findings": [finding(f) for f in result.findings],
    }
    return json.dumps(document, indent=2) + "\n"


RULES = [
    {
        "id": "unripe/too-new",
        "name": "UnripeVersion",
        "shortDescription": {"text": "A locked version is younger than the minimum age"},
        "fullDescription": {
            "text": "This version was published more recently than the policy allows. Most malicious releases are found and pulled within days, so waiting before using a new version avoids them."
        },
        "help": {
            "text": "Wait until the version is old enough, lock an older version, or exclude the package if you trust it.",
            "markdown": f"Wait until the version is old enough, lock an older version, or exclude the package if you trust it. See [{HOME}]({HOME}).",
        },
        "defaultConfiguration": {"level": "error"},
        "properties": {"tags": ["security", "supply-chain"]},
    },
    {
        "id": "unripe/unknown-age",
        "name": "UnknownPublishTime",
        "shortDescription": {"text": "The registry could not say when a locked version was published"},
        "fullDescription": {
            "text": "unripe asked the package registry when this version was published and got no usable answer. An unknown age is not treated as old enough."
        },
        "help": {"text": "Check the package comes from the public registry. For private packages, add an exclude rule."},
        "defaultConfiguration": {"level": "error"},
        "properties": {"tags": ["security", "supply-chain"]},
    },
    {
        "id": "unripe/unreadable",
        "name": "UnreadableLockfile",
        "shortDescription": {"text": "A lockfile could not be read"},
        "fullDescription": {"text": "unripe found a lockfile it could not parse, so none of its versions were checked."},
        "help": {"text": "Check the file is a valid lockfile, or regenerate it with the package manager."},
        "defaultConfiguration": {"level": "error"},
        "properties": {"tags": ["supply-chain"]},
    },
]


def sarif(result: Result) -> str:
    """SARIF 2.1.0, the format GitHub code scanning reads. Only problems become results."""
    age = spoken(result.policy.min_age)

    def location(path: str, number: int | None) -> dict:
        return {
            "physicalLocation": {
                "artifactLocation": {"uri": path},
                "region": {"startLine": number or 1},
            }
        }

    results = []
    for f in result.findings:
        if f.state == "unripe":
            rule, level = "unripe/too-new", "error"
            message = (
                f"{f.package.ecosystem} {f.package.name} {f.package.version} was published {ago(f.age_seconds)} ago "
                f"({stamp(f.published)}), less than the {age} minimum. It is old enough at {stamp(ripe(f, result))}."
            )
        elif f.state == "unknown":
            rule = "unripe/unknown-age"
            level = "error" if result.policy.unknown == "fail" else "warning"
            message = f"{f.package.ecosystem} {f.package.name} {f.package.version}: publish time unknown, {f.reason}."
        else:
            continue
        results.append(
            {
                "ruleId": rule,
                "level": level,
                "message": {"text": message},
                "locations": [location(path, number) for path, number in f.locations] or [location("", None)],
                "partialFingerprints": {"unripe/v1": f"{f.package.ecosystem}:{f.package.name}@{f.package.version}"},
            }
        )
    for path, message in result.unreadable:
        results.append(
            {
                "ruleId": "unripe/unreadable",
                "level": "error",
                "message": {"text": message},
                "locations": [location(path, None)],
                "partialFingerprints": {"unripe/v1": f"unreadable:{path}"},
            }
        )
    document = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "unripe", "version": __version__, "informationUri": HOME, "rules": RULES}},
                "results": results,
                "invocations": [{"executionSuccessful": result.exitcode != 2}],
            }
        ],
    }
    return json.dumps(document, indent=2) + "\n"


def cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def markdown(result: Result) -> str:
    """A short report for a PR comment or the GitHub job summary."""
    age = spoken(result.policy.min_age)
    unripe, unknown = result.count("unripe"), result.count("unknown")
    if result.exitcode == 2:
        title = "could not check every lockfile" if result.lockfiles else "found no lockfiles"
    elif unripe or (unknown and result.policy.unknown == "fail"):
        problems = [plural(unripe, "version") + " too new"] if unripe else []
        if unknown:
            problems.append(f"{unknown} with an unknown age")
        title = ", ".join(problems)
    else:
        title = "every version is old enough"
    out = [f"### unripe: {title}", "", f"Checked {scope(result)}. Versions must be at least {age} old.", ""]

    flagged = [f for f in result.findings if f.state in ("unripe", "unknown")]
    if flagged:
        out.append("| | Package | Version | Age | Old enough at | Lockfile |")
        out.append("|---|---|---|---|---|---|")
        for f in flagged:
            if f.state == "unripe":
                status, when, enough = "too new", ago(f.age_seconds), stamp(ripe(f, result))
            else:
                status, when, enough = "unknown", "?", cell(f.reason)
            out.append(
                f"| {status} | {f.package.ecosystem} `{cell(f.package.name)}` | {cell(f.package.version)} | {when} | {enough} | {cell(where(f))} |"
            )
        out.append("")
    if result.unreadable:
        out.append("Could not read:")
        out.append("")
        out.extend(f"- {cell(message)}" for _, message in result.unreadable)
        out.append("")

    out.append(f"{result.count('ok')} ok, {result.count('excluded')} excluded. Checked by [unripe]({HOME}) {__version__}.")
    return "\n".join(out) + "\n"


def render(result: Result, form: str, verbose: bool = False) -> str:
    if form == "text":
        return text(result, verbose)
    if form == "json":
        return jsondoc(result)
    if form == "sarif":
        return sarif(result)
    if form == "markdown":
        return markdown(result)
    raise ValueError(f"unknown format {form!r}")


def explanation(finding: Finding, min_age: timedelta) -> str:
    """One package, said in full, for `unripe explain`."""
    package = finding.package
    head = f"{package.ecosystem} {package.name} {package.version}"
    if finding.state == "unknown":
        return f"{head}: publish time unknown, {finding.reason}\n"
    lines = [f"{head}: published {stamp(finding.published)}, {ago(finding.age_seconds)} ago"]
    if finding.state == "ok":
        lines.append(f"old enough: the minimum age is {spoken(min_age)}")
    elif finding.state == "unripe":
        lines.append(f"unripe: the minimum age is {spoken(min_age)}, old enough at {stamp(finding.published + min_age)}")
    return "\n".join(lines) + "\n"
