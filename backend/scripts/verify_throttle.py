"""Load-test the public API's rate limit and check the 429 behaviour.

Evidence for EXT-062's "verified 429 behaviour under load test". Narrow on
purpose: it answers "does the limit hold, and does it answer correctly
when it bites", not "how fast is the dashboard" - capacity testing at
expected peak is EXT-065.

Run it against a server that is actually running:

    python scripts/verify_throttle.py --url http://localhost:8000/api/boards/

It sends more concurrent requests than the configured rate allows and
reports what came back. A pass needs four things:

* some requests succeed - the limit is not simply breaking the endpoint;
* the rest are 429, not 500 - the limit is refusing, not failing;
* every 429 carries Retry-After, so a client knows when to come back;
* a forged X-Forwarded-For does not buy a fresh budget, because a limit
  a header can bypass is decoration.
"""

from __future__ import annotations

import argparse
import collections
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def one_request(url: str, forwarded_for: str | None = None) -> tuple[int, str | None]:
    request = urllib.request.Request(url)
    if forwarded_for:
        request.add_header("X-Forwarded-For", forwarded_for)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("Retry-After")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Retry-After")
    except OSError as error:  # connection refused, timeout, reset
        print(f"  ! request failed: {error}")
        return 0, None


def blast(url: str, count: int, workers: int, spoof: bool = False):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(
                lambda i: one_request(url, f"10.0.0.{i % 250}" if spoof else None),
                range(count),
            )
        )


def summarise(label: str, results) -> collections.Counter:
    codes = collections.Counter(code for code, _ in results)
    print(f"\n{label}")
    for code, count in sorted(codes.items()):
        name = {200: "OK", 429: "Too Many Requests", 0: "connection error"}.get(code, "")
        print(f"    {code} {name:20} x{count}")
    return codes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/api/boards/")
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()

    print(f"Target: {args.url}")
    print(f"Load:   {args.requests} requests, {args.workers} concurrent")

    started = time.monotonic()
    results = blast(args.url, args.requests, args.workers)
    elapsed = time.monotonic() - started
    codes = summarise(f"Under load ({elapsed:.1f}s):", results)

    retry_after = [value for code, value in results if code == 429]
    with_header = [value for value in retry_after if value]

    spoofed = blast(args.url, 40, args.workers, spoof=True)
    spoofed_codes = summarise("With a forged X-Forwarded-For on every request:", spoofed)

    checks = [
        ("some requests are served", codes[200] > 0),
        ("the rest are refused with 429", codes[429] > 0),
        ("nothing failed with a 5xx", not any(c >= 500 for c in codes)),
        (
            "every 429 carries Retry-After",
            bool(retry_after) and len(with_header) == len(retry_after),
        ),
        ("a forged X-Forwarded-For is still throttled", spoofed_codes[429] > 0),
    ]

    print("\nChecks:")
    for description, passed in checks:
        print(f"    [{'PASS' if passed else 'FAIL'}] {description}")

    if with_header:
        print(f"\n    Retry-After seen: {sorted(set(with_header))[:5]}")

    return 0 if all(passed for _, passed in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
