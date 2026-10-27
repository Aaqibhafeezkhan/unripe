"""
Turning a check's result into text for people, JSON for scripts, SARIF for
code scanning, markdown for PRs, and the formats other CI systems read:
GitHub annotations, GitLab code quality and JUnit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from xml.sax.saxutils import escape, quoteattr

from unripe import __version__
from unripe.check import Result
from unripe.model import Finding
from unripe.policy import spoken

HOME = "https://github.com/ashfall-sh/unripe"
FORMATS = ("text", "json", "sarif", "markdown", "github", "codequality", "junit")
# more rows than this make a pull request comment too long to read, and GitHub caps comments at 65536 characters
ROWS = 50
COLORS = {"unripe": "31", "unknown": "33", "allowed": "36", "ok": "32", "pass": "32", "fail": "31", "error": "31"}


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
    if finding.ripe:
        return finding.ripe
    return finding.published + result.policy.min_age if finding.published else None


def minimum(finding: Finding, result: Result) -> timedelta:
    return finding.minimum if finding.minimum is not None else result.policy.min_age


def paint(word: str, state: str, color: bool) -> str:
    return f"\033[{COLORS[state]}m{word}\033[0m" if color and state in COLORS else word


def instead(finding: Finding) -> str:
    """The suggestion for a version that is too new, said briefly."""
    if not finding.instead:
        return ""
    version, published = finding.instead
    return f"{version} is old enough (published {published.strftime('%Y-%m-%d')})"


def counts(result: Result) -> list[str]:
    shown = ["unripe", "unknown", "ok", "excluded"]
    if result.count("allowed"):
        shown.insert(2, "allowed")
    return [f"{result.count(state)} {state}" for state in shown]


def problem(finding: Finding, result: Result) -> bool:
    return finding.state == "unripe" or (finding.state == "unknown" and result.policy.unknown == "fail")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def where(finding: Finding) -> str:
    return ", ".join(f"{path}:{number}" if number else path for path, number in finding.locations)


def scope(result: Result) -> str:
    checked = plural(len(result.findings), "version")
    added = f" added since {result.since}" if result.since else ""
    return f"{checked}{added} in {plural(len(result.lockfiles), 'lockfile')}"


def policyline(result: Result) -> str:
    """The minimum age, and a word that some packages differ."""
    age = spoken(result.policy.min_age)
    return f"{age}, other ages set in min-age-for" if result.policy.varies() else age


def verdict(result: Result) -> str:
    code = result.exitcode
    if code == 0:
        return "pass"
    if code == 2:
        return "error"
    return "fail"


LOCKFILES = (
    "no lockfiles found. unripe reads package-lock.json, npm-shrinkwrap.json, pnpm-lock.yaml, yarn.lock,\n"
    "bun.lock, deno.lock, uv.lock, pylock.toml, poetry.lock, Pipfile.lock, requirements*.txt, Cargo.lock,\n"
    "go.mod, composer.lock, Gemfile.lock, packages.lock.json, gradle.lockfile, pubspec.lock, Podfile.lock,\n"
    "Package.resolved, .terraform.lock.hcl, conan.lock, renv.lock, mix.lock, flake.lock, MODULE.bazel.lock\n"
    "and vcpkg.json"
)


def text(result: Result, verbose: bool = False, color: bool = False) -> str:
    age = spoken(result.policy.min_age)
    out = [f"unripe: checked {scope(result)}, minimum age {policyline(result)}"]
    if not result.lockfiles:
        out.append("")
        out.append(LOCKFILES)

    young = f"published less than {age} ago" if not result.policy.varies() else "younger than their minimum age"
    groups = [
        ("unripe", young),
        ("unknown", "could not be dated, " + ("which fails by default (--unknown warn to allow)" if result.policy.unknown == "fail" else "allowed by --unknown warn")),
        ("allowed", "too new or unknown, but let through"),
    ]
    if verbose:
        groups += [("ok", "old enough"), ("excluded", "skipped by an exclude rule")]
    # the ecosystem column, as wide as the longest name shown (terraform, cocoapods)
    pad = max([8, *(len(f.package.ecosystem) for f in result.findings if f.state in dict(groups))])
    for state, meaning in groups:
        chosen = [finding for finding in result.findings if finding.state == state]
        if not chosen:
            continue
        out.append("")
        out.append(f"{paint(state, state, color)}: {plural(len(chosen), 'version')} {meaning}")
        width = max(len(f"{f.package.name} {f.package.version}") for f in chosen)
        for finding in chosen:
            package = finding.package
            label = f"{package.name} {package.version}".ljust(width)
            if state == "unripe":
                detail = f"{ago(finding.age_seconds)} old, published {stamp(finding.published)}, old enough at {stamp(ripe(finding, result))}"
                if minimum(finding, result) != result.policy.min_age:
                    detail += f" ({spoken(minimum(finding, result))} minimum)"
                if finding.basis:
                    detail += f", by its {finding.basis} time"
            elif state == "ok":
                detail = f"{ago(finding.age_seconds)} old"
                if finding.basis:
                    detail += f" by its {finding.basis} time, which the author sets"
            else:
                detail = finding.reason
            out.append(f"  {package.ecosystem:<{pad}} {label}  {detail}")
            if state in ("unripe", "unknown", "allowed"):
                out.append(f"  {'':<{pad}} {where(finding)}")
            if finding.instead:
                out.append(f"  {'':<{pad}} {instead(finding)}")

    for _, message in result.unreadable:
        out.append("")
        out.append(f"could not read {message}")
    if result.notes:
        out.append("")
        out.extend(f"note: {note}" for note in result.notes)

    out.append("")
    out.append(f"{paint(verdict(result), verdict(result), color)}: {', '.join(counts(result))}" + (f", {len(result.unreadable)} unreadable" if result.unreadable else ""))
    return "\n".join(out) + "\n"


def jsondoc(result: Result) -> str:
    def finding(f: Finding) -> dict:
        return {
            "ecosystem": f.package.ecosystem,
            "name": f.package.name,
            "version": f.package.version,
            "state": f.state,
            "published": f.published.isoformat() if f.published else None,
            "dated_by": (f.basis or "registry") if f.published else None,
            "age_seconds": round(f.age_seconds) if f.age_seconds is not None else None,
            "old_enough_at": ripe(f, result).isoformat() if f.published else None,
            "min_age_seconds": int(minimum(f, result).total_seconds()),
            "reason": f.reason or None,
            "instead": {"version": f.instead[0], "published": f.instead[1].isoformat()} if f.instead else None,
            "locations": [{"lockfile": path, "line": number} for path, number in f.locations],
        }

    document = {
        # bumped only when a field changes meaning or goes away; new fields can appear at any time
        "schema": 1,
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
        "notes": result.notes,
        "summary": {state: result.count(state) for state in ("unripe", "unknown", "allowed", "ok", "excluded")},
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
                f"({stamp(f.published)}), less than the {spoken(minimum(f, result))} minimum. It is old enough at {stamp(ripe(f, result))}."
            )
            if f.instead:
                message += f" {instead(f).capitalize()}."
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
    age = policyline(result)
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

    flagged = [f for f in result.findings if f.state in ("unripe", "unknown", "allowed")]
    if flagged:
        out.append("| | Package | Version | Age | Old enough at | Lockfile |")
        out.append("|---|---|---|---|---|---|")
        for f in flagged[:ROWS]:
            if f.state == "unripe":
                status, when, enough = "too new", ago(f.age_seconds), stamp(ripe(f, result))
                if f.instead:
                    enough += f", or use {cell(f.instead[0])}"
            elif f.state == "allowed":
                status, when, enough = "allowed", ago(f.age_seconds), cell(f.reason)
            else:
                status, when, enough = "unknown", "?", cell(f.reason)
            out.append(
                f"| {status} | {f.package.ecosystem} `{cell(f.package.name)}` | {cell(f.package.version)} | {when} | {enough} | {cell(where(f))} |"
            )
        if len(flagged) > ROWS:
            out.append("")
            out.append(f"And {len(flagged) - ROWS} more. The job log has the full list.")
        out.append("")
    if result.notes:
        out.extend(f"Note: {cell(note)}." for note in result.notes)
        out.append("")
    if result.unreadable:
        out.append("Could not read:")
        out.append("")
        out.extend(f"- {cell(message)}" for _, message in result.unreadable)
        out.append("")

    out.append(f"{result.count('ok')} ok, {result.count('excluded')} excluded. Checked by [unripe]({HOME}) {__version__}.")
    return "\n".join(out) + "\n"


def said(finding: Finding, result: Result) -> str:
    """One finding in one sentence, for annotations and CI reports."""
    package = finding.package
    head = f"{package.ecosystem} {package.name} {package.version}"
    if finding.state == "unripe":
        sentence = (
            f"{head} was published {ago(finding.age_seconds)} ago, less than the {spoken(minimum(finding, result))} minimum. "
            f"It is old enough at {stamp(ripe(finding, result))}."
        )
        return sentence + (f" {instead(finding).capitalize()}." if finding.instead else "")
    if finding.state == "unknown":
        return f"{head}: publish time unknown, {finding.reason}."
    return f"{head}: {finding.reason}."


def workflow(value: str, property: bool = False) -> str:
    """Escaped for a GitHub workflow command."""
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return value.replace(":", "%3A").replace(",", "%2C") if property else value


def github(result: Result) -> str:
    """GitHub Actions annotations, which show on the lockfile line in a pull request without code scanning."""
    out = annotations(result)
    out.append(f"{verdict(result)}: {', '.join(counts(result))}" + (f", {len(result.unreadable)} unreadable" if result.unreadable else ""))
    return "\n".join(out) + "\n"


def annotations(result: Result, soft: bool = False) -> list[str]:
    """One workflow command per problem and lockfile, and one per note. `soft` makes them all warnings."""
    out = []
    for f in result.findings:
        if f.state not in ("unripe", "unknown"):
            continue
        level = "error" if problem(f, result) and not soft else "warning"
        title = "unripe: too new" if f.state == "unripe" else "unripe: unknown age"
        for path, number in f.locations or (("", None),):
            spot = f"file={workflow(path, True)}," + (f"line={number}," if number else "") if path else ""
            out.append(f"::{level} {spot}title={workflow(title, True)}::{workflow(said(f, result))}")
    for path, message in result.unreadable:
        out.append(f"::error file={workflow(path, True)},title=unripe%3A unreadable::{workflow(message)}")
    for note in result.notes:
        out.append(f"::notice title=unripe::{workflow(note)}")
    return out


def codequality(result: Result) -> str:
    """GitLab's code quality report, which shows findings in the merge request widget."""
    issues = []
    for f in result.findings:
        if f.state not in ("unripe", "unknown"):
            continue
        for path, number in f.locations or (("", None),):
            key = f"{f.state}:{f.package.ecosystem}:{f.package.name}@{f.package.version}:{path}"
            issues.append(
                {
                    "description": said(f, result),
                    "check_name": "unripe/too-new" if f.state == "unripe" else "unripe/unknown-age",
                    "fingerprint": hashlib.sha256(key.encode("utf-8")).hexdigest(),
                    "severity": "critical" if problem(f, result) else "minor",
                    "location": {"path": path, "lines": {"begin": number or 1}},
                }
            )
    for path, message in result.unreadable:
        issues.append(
            {
                "description": message,
                "check_name": "unripe/unreadable",
                "fingerprint": hashlib.sha256(f"unreadable:{path}".encode("utf-8")).hexdigest(),
                "severity": "critical",
                "location": {"path": path, "lines": {"begin": 1}},
            }
        )
    return json.dumps(issues, indent=2) + "\n"


def junit(result: Result) -> str:
    """JUnit XML, which Jenkins, Azure Pipelines, CircleCI and GitLab all show as test results. One test per version."""
    cases = []
    for f in result.findings:
        name = quoteattr(f"{f.package.name}@{f.package.version}")
        head = f'    <testcase classname={quoteattr("unripe." + f.package.ecosystem)} name={name} file={quoteattr(f.locations[0][0] if f.locations else "")}'
        if f.state == "excluded":
            cases.append(f"{head}><skipped message={quoteattr(f.reason)}/></testcase>")
        elif problem(f, result):
            cases.append(f"{head}><failure message={quoteattr(said(f, result))} type={quoteattr(f.state)}>{escape(where(f))}</failure></testcase>")
        else:
            cases.append(f"{head}/>")
    for path, message in result.unreadable:
        cases.append(f'    <testcase classname="unripe.lockfile" name={quoteattr(path)}><error message={quoteattr(message)}/></testcase>')
    failures = sum(1 for f in result.findings if problem(f, result))
    skipped = result.count("excluded")
    head = (
        f'<testsuite name="unripe" tests="{len(cases)}" failures="{failures}" errors="{len(result.unreadable)}" '
        f'skipped="{skipped}" timestamp="{result.checked_at.strftime("%Y-%m-%dT%H:%M:%S")}">'
    )
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>', "<testsuites>", "  " + head, *cases, "  </testsuite>", "</testsuites>"]) + "\n"


def render(result: Result, form: str, verbose: bool = False, color: bool = False) -> str:
    if form == "text":
        return text(result, verbose, color)
    if form == "json":
        return jsondoc(result)
    if form == "sarif":
        return sarif(result)
    if form == "markdown":
        return markdown(result)
    if form == "github":
        return github(result)
    if form == "codequality":
        return codequality(result)
    if form == "junit":
        return junit(result)
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
