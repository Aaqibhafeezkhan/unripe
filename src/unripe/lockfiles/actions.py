"""GitHub Actions workflows: the actions they use, and the ref each is pinned to."""

from __future__ import annotations

import re

from unripe.model import Package

# `uses: owner/repo@ref` or `uses: owner/repo/path@ref`, in a step or a reusable workflow call
USES = re.compile(r"""^\s*(?:-\s+)?uses\s*:\s*["']?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/[^@\s"']*)?@([^\s"'#]+)""")


def workflow(text: str, lockfile: str) -> list[Package]:
    """
    Every action a workflow (or a composite action) uses. Local actions
    (`./path`) and docker images are not on GitHub's release timeline.
    """
    found = []
    for row in text.splitlines():
        match = USES.match(row)
        if match and not match.group(1).startswith("."):
            found.append(Package("actions", match.group(1), match.group(2), lockfile))
    return found
