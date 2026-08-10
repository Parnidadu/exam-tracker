"""Load-test the public dashboard and report latency percentiles.

Evidence for EXT-065's "p95 latency under 500ms at target concurrency".

The workload is the dashboard's own request mix, not a single URL hammered
in a loop. Opening the home page fires three calls; browsing to an exam
fires a fourth; the calendar is a fifth. Measuring only the cheapest of
those would produce a number that means nothing about what a visitor
experiences.

Query strings vary per request on purpose. The public reads are cached
for a minute (EXT-034), and a fixed URL would measure the cache rather
than the application - a real crowd on results day is filtering and
paging, and those are misses.

    python scripts/load_test.py --url https://localhost --concurrency 10 25 50

Note the rate limit: the anonymous throttle is 60/min per IP (EXT-062),
and every request here comes from one address. Raise THROTTLE_ANON_RATE
on the target before running, or the test measures how fast the server
can say 429.
"""

from __future__ import annotations

import argparse
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

#: (label, path template, weight). Weights approximate a visitor session:
#: the home page's three calls dominate, detail views are common, the
#: calendar is occasional.
WORKLOAD = [
    ("exam list", "/api/exams/?page={page}", 4),
    # Page 1 only. A filtered search has few pages, and asking for page 7
    # of two is a 404 - which a real visitor cannot click and which would
    # otherwise be excluded from the percentiles as a non-200, quietly
    # dropping the most expensive queries from the measurement.
    ("exam list filtered", "/api/exams/?search={term}", 2),
    ("boards", "/api/boards/", 3),
    ("discrepancy feed", "/api/discrepancy-feed/", 3),
    ("exam detail", "/api/exams/{slug}/", 3),
    ("calendar", "/api/calendar/?month={month}", 1),
]

TERMS = ["civil", "clerk", "engineer", "constable", "defence", "graduate", "officer"]
MONTHS = [f"2026-{month:02d}" for month in range(1, 13)]


@dataclass
class Sample:
    label: str
    milliseconds: float
    status: int


@dataclass
class Report:
    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)

    @property
    def ok(self) -> list[Sample]:
        return [s for s in self.samples if s.status == 200]

    def percentile(self, share: float, samples: list[Sample] | None = None) -> float:
        values = sorted(s.milliseconds for s in (samples if samples is not None else self.ok))
        if not values:
            return float("nan")
        # Nearest-rank: with a few hundred samples, interpolating invents
        # precision the measurement does not have.
        index = min(len(values) - 1, max(0, round(share * len(values)) - 1))
        return values[index]


def fetch(base: str, path: str, context: ssl.SSLContext) -> tuple[float, int]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(base + path, timeout=30, context=context) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        error.read()
        status = error.code
    except OSError:
        status = 0
    return (time.perf_counter() - started) * 1000, status


def build_path(template: str, slugs: list[str]) -> str:
    return template.format(
        page=random.randrange(1, 8),
        term=random.choice(TERMS),
        slug=random.choice(slugs) if slugs else "none",
        month=random.choice(MONTHS),
    )


def run(base: str, concurrency: int, requests: int, slugs: list[str], context) -> Report:
    weighted = [entry for entry in WORKLOAD for _ in range(entry[2])]
    report = Report()

    def one(_: int) -> Sample:
        label, template, _weight = random.choice(weighted)
        milliseconds, status = fetch(base, build_path(template, slugs), context)
        return Sample(label=label, milliseconds=milliseconds, status=status)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for sample in pool.map(one, range(requests)):
            report.add(sample)
    return report


def discover_slugs(base: str, context, wanted: int = 60) -> list[str]:
    slugs: list[str] = []
    page = 1
    while len(slugs) < wanted and page <= 5:
        try:
            with urllib.request.urlopen(
                f"{base}/api/exams/?page={page}", timeout=30, context=context
            ) as response:
                payload = json.load(response)
        except Exception:
            break
        slugs += [row["slug"] for row in payload.get("results", [])]
        if not payload.get("next"):
            break
        page += 1
    return slugs


def summarise(report: Report, concurrency: int, elapsed: float, threshold: float) -> bool:
    ok = report.ok
    failures = [s for s in report.samples if s.status != 200]
    throttled = [s for s in failures if s.status == 429]

    p50 = report.percentile(0.50)
    p95 = report.percentile(0.95)
    p99 = report.percentile(0.99)
    passed = bool(ok) and p95 < threshold and not throttled

    print(f"\n  concurrency {concurrency}")
    print(f"    requests      {len(report.samples)} in {elapsed:.1f}s "
          f"({len(report.samples) / elapsed:.0f}/s)")
    print(f"    p50 / p95 / p99   {p50:.0f} / {p95:.0f} / {p99:.0f} ms")
    if failures:
        codes: dict[int, int] = defaultdict(int)
        for sample in failures:
            codes[sample.status] += 1
        print(f"    non-200       {dict(codes)}")
        if throttled:
            print("    ! throttled - raise THROTTLE_ANON_RATE or this measures the rate limit")
    print(f"    p95 < {threshold:.0f}ms   {'PASS' if passed else 'FAIL'}")

    by_label: dict[str, list[Sample]] = defaultdict(list)
    for sample in ok:
        by_label[sample.label].append(sample)
    for label in sorted(by_label):
        samples = by_label[label]
        print(f"      {label:22} n={len(samples):<5} "
              f"p50 {report.percentile(0.50, samples):>6.0f}  "
              f"p95 {report.percentile(0.95, samples):>6.0f} ms")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://localhost")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 25, 50])
    parser.add_argument("--requests", type=int, default=400, help="Per concurrency level.")
    parser.add_argument("--threshold-ms", type=float, default=500.0)
    parser.add_argument("--insecure", action="store_true", help="Accept a self-signed cert.")
    args = parser.parse_args()

    context = ssl.create_default_context()
    if args.insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    base = args.url.rstrip("/")
    print(f"Target: {base}")

    slugs = discover_slugs(base, context)
    print(f"Dataset: {len(slugs)} exam slugs discovered for detail views")
    if not slugs:
        print("  ! no exams found - seed the target first (manage.py seed_load_data)")

    # Warm-up, excluded from the numbers. The first request through a cold
    # connection pool and an empty response cache is not what a visitor on
    # results day experiences, and including it drags the tail.
    run(base, 5, 40, slugs, context)

    results = []
    for concurrency in args.concurrency:
        started = time.monotonic()
        report = run(base, concurrency, args.requests, slugs, context)
        elapsed = time.monotonic() - started
        results.append(summarise(report, concurrency, elapsed, args.threshold_ms))

    print()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
