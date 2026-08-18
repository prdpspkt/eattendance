#!/usr/bin/env python3
"""Fire a burst of concurrent HTTP requests at a URL and report the results.

A crude load probe for this deployment. Standard library only, so it runs with
nothing installed:

    python scripts/loadtest.py http://127.0.0.1:8001/ -n 1000 -c 200

Arguments:
    url                 The target. Test the ORIGIN directly to measure your own
                        server (e.g. http://127.0.0.1:8001/, the gunicorn bind);
                        test the public https URL to measure the whole chain
                        including Cloudflare.
    -n, --requests      Total requests to send (default 1000).
    -c, --concurrency   How many in flight at once (default 100). This, not -n,
                        is what actually stresses the server: -n 1000 -c 1000 is
                        a genuine 1000-at-once burst; -n 1000 -c 50 is a steady
                        stream 50 wide.
    -m, --method        HTTP method (default GET).
    -t, --timeout       Per-request timeout in seconds (default 30).
    -H, --header        Extra header "Name: value"; repeatable.
    --body              Request body for POST/PUT (a string, or @path to read a
                        file).
    --insecure          Skip TLS certificate verification.

Read before you run:
  * Point this at infrastructure you own. It generates real load.
  * Against the public hostname you are testing Cloudflare, which may cache,
    challenge, or rate-limit the burst - and may read a large burst as an
    attack on your own zone. To measure the origin, hit 127.0.0.1 on the box.
  * /iclock/* is rate limited (10 r/s in nginx); a burst there will return 503
    on purpose. That is expected, not a failure of the server.
  * Concurrency is capped by this machine too: file descriptors, ephemeral
    ports, and the GIL. Very high -c on a small client box measures the client.
"""
import argparse
import ssl
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter


def make_request(url, method, headers, body, timeout, ctx):
    """Perform one request. Return (status_or_None, elapsed_seconds, error)."""
    req = urllib.request.Request(url, method=method, data=body)
    for name, value in headers.items():
        req.add_header(name, value)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            resp.read()  # drain the body so the timing includes transfer
            return resp.status, time.perf_counter() - start, None
    except urllib.error.HTTPError as exc:
        # A 4xx/5xx is a real response with a status, not a client failure.
        return exc.code, time.perf_counter() - start, None
    except Exception as exc:  # timeout, connection refused, reset, DNS, ...
        return None, time.perf_counter() - start, type(exc).__name__


def percentile(values, pct):
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Concurrent HTTP load probe (stdlib only).",
    )
    p.add_argument("url")
    p.add_argument("-n", "--requests", type=int, default=1000)
    p.add_argument("-c", "--concurrency", type=int, default=100)
    p.add_argument("-m", "--method", default="GET")
    p.add_argument("-t", "--timeout", type=float, default=30.0)
    p.add_argument("-H", "--header", action="append", default=[])
    p.add_argument("--body", default=None)
    p.add_argument("--insecure", action="store_true")
    return p.parse_args(argv)


def main(argv):
    args = parse_args(argv)

    if args.concurrency > args.requests:
        args.concurrency = args.requests

    headers = {}
    for h in args.header:
        if ":" not in h:
            print(f"Ignoring malformed header (no colon): {h!r}", file=sys.stderr)
            continue
        name, value = h.split(":", 1)
        headers[name.strip()] = value.strip()

    body = None
    if args.body is not None:
        if args.body.startswith("@"):
            with open(args.body[1:], "rb") as fh:
                body = fh.read()
        else:
            body = args.body.encode()

    ctx = None
    if args.insecure and args.url.lower().startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    print(
        f"Sending {args.requests} {args.method} requests to {args.url} "
        f"at concurrency {args.concurrency}\n"
    )

    statuses = Counter()
    errors = Counter()
    latencies = []  # only successful (got-a-status) requests

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                make_request, args.url, args.method, headers, body,
                args.timeout, ctx,
            )
            for _ in range(args.requests)
        ]
        done = 0
        for fut in as_completed(futures):
            status, elapsed, err = fut.result()
            if err is None:
                statuses[status] += 1
                latencies.append(elapsed)
            else:
                errors[err] += 1
            done += 1
            if done % max(1, args.requests // 20) == 0 or done == args.requests:
                print(f"\r  {done}/{args.requests} complete", end="", flush=True)
    wall = time.perf_counter() - wall_start
    print("\n")

    ok = sum(c for s, c in statuses.items() if 200 <= s < 400)
    total = args.requests

    print("Results")
    print("-------")
    print(f"  wall time        {wall:.2f}s")
    print(f"  throughput       {total / wall:.1f} req/s")
    print(f"  succeeded (2xx/3xx) {ok}/{total}")
    if statuses:
        print("  status codes:")
        for code in sorted(statuses):
            print(f"    {code}: {statuses[code]}")
    if errors:
        print("  connection errors:")
        for name in sorted(errors):
            print(f"    {name}: {errors[name]}")

    if latencies:
        print("  latency (responses only):")
        print(f"    min    {min(latencies) * 1000:8.1f} ms")
        print(f"    mean   {statistics.mean(latencies) * 1000:8.1f} ms")
        print(f"    p50    {percentile(latencies, 50) * 1000:8.1f} ms")
        print(f"    p95    {percentile(latencies, 95) * 1000:8.1f} ms")
        print(f"    p99    {percentile(latencies, 99) * 1000:8.1f} ms")
        print(f"    max    {max(latencies) * 1000:8.1f} ms")

    # Non-zero exit if anything failed, so CI / scripts can gate on it.
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
