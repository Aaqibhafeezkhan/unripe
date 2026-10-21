# Dating Go module versions

Every other registry unripe talks to records when a version was published:
npm's `time` map, PyPI's upload times, crates.io's `pubtime`. Go doesn't
have a registry in that sense. A module version is a git tag, and the module
proxy reports the time of the commit the tag points at:

```
$ curl https://proxy.golang.org/github.com/colonelpanic8/gmcli/@v/v0.3.1.info
{"Version":"v0.3.1","Time":"2026-06-03T16:49:11Z", ...}
```

That version was tagged and first fetched on October 20, 2026. The commit is
from June, so by the proxy's account it was four and a half months old on
the day it came out. Nothing malicious about it, it's just how tags work. But
it means a commit time can't be trusted for this, and an attacker doesn't
even need an old commit: git lets you put any date on a new one.

## What can't be backdated

Go's checksum database, sum.golang.org, is an append-only log. Every module
version gets a record number the first time anyone fetches it through the
proxy, and the log is built so that nobody, including Google, can rewrite
it without clients noticing. Record numbers only go up:

```
$ curl https://sum.golang.org/lookup/github.com/colonelpanic8/gmcli@v0.3.1
69557040
github.com/colonelpanic8/gmcli v0.3.1 h1:...
```

The log stores no times, though. For that there's index.golang.org, which
lists module versions in the order the proxy first fetched them, with a
timestamp:

```
$ curl 'https://index.golang.org/index?since=2026-10-20T03:26:49Z&limit=1'
{"Path":"github.com/netdata/...","Version":"v0.0.0-...","Timestamp":"2026-10-20T03:26:49.88217Z"}
```

Look that version up in the checksum database and you have a record number
at a known time. Do it for a few moments and you have a timeline:

| When | Record number |
|---|---|
| now | 69,583,469 |
| 1 day ago | 69,482,739 |
| 3 days ago | 69,352,308 |
| 7 days ago | 68,910,823 |
| 30 days ago | 64,307,253 |

gmcli v0.3.1 is record 69,557,040, newer than the point a day ago. So it
first appeared less than a day ago, whatever its commit says.

## How unripe uses it

For each Go module version, unripe gets the commit time from the proxy and
the record number from the checksum database. It finds which two points of
the timeline the record falls between, by binary search over a fixed ladder
of moments, from an hour ago to a year ago.

- If the gap between the two points is narrow, it interpolates. For recent
  versions that comes out within minutes: gmcli's estimate is 23:48, and the
  index first listed it at 23:47.
- If the gap is wide, it takes the newer point, the youngest the version
  could be. unripe would rather call a version too new than too old.
- If the commit time falls inside the gap, that's the normal case of a
  version tagged soon after its commit, and the commit time is used.
- If the commit time is older than the gap, it isn't believed.

The ladder is dense in the first month, because that's where policies live
and because the log grows unevenly. Between October 9 and 12, 2026 it grew
three times faster than between October 1 and 6, so a straight line across
a wide gap can be off by weeks.

## What it costs

The record number at a past moment never changes, so ladder points are
cached like publish times, on whole hours (whole days further back) so that
runs close together share them. The binary search means a run only fetches
the points it needs. Checking the go.mod files of cli/cli and Hugo together,
332 module versions, took 25 index requests and one checksum lookup per
version, about 13 seconds on a cold cache.

## Limits

- Private modules aren't in the public checksum database. unripe honours
  `GOSUMDB`, `GONOSUMDB` and `GOPRIVATE` like the go command does, and uses
  the commit time for those.
- The record number is when anyone first fetched the version through the
  proxy, which is usually minutes after the tag is pushed, but it can be
  later for a module nobody uses.
- For versions more than about a month old the date is approximate. That
  doesn't change any decision unless your policy is longer than a month.

There's an open proposal for a cooldown in the go command itself,
[golang/go#76485](https://github.com/golang/go/issues/76485), on hold as of
June 2026. Until then this is the best signal I've found that the author of a
module can't control.
