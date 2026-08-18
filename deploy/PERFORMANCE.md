# Performance notes: 4 cores, 500 MB, ARM

What was changed to make this deployment fast, what it can realistically serve,
and how to measure it without measuring the wrong thing.

## Read this first: what 2000 req/s means here

Throughput is not a property of a server, it is a property of a server *and an
endpoint*. On this hardware:

| Endpoint | What it costs | Realistic on 4 ARM cores |
|---|---|---|
| `/healthz/` | no DB, no template | **2000+ req/s** — reachable |
| `/static/*` via nginx | a file read | **5000+ req/s** — reachable |
| `/iclock/*` heartbeats (`ping`, `getrequest`) | 0–1 cached reads | **1500–3000 req/s** — reachable |
| `/iclock/cdata` punch uploads | parse + batched writes | **300–800 uploads/s**, each carrying many punches |
| `/dashboard/`, report pages | 5–15 queries + template render | **200–600 req/s** — *not* 2000 |

The first three targets are met by the changes below. **The dashboard is not
going to serve 2000 req/s on four ARM cores**, and no amount of tuning gets it
there — at 2000 req/s each core has ~2 ms per request, which is less than the
template render alone. If you need that number on a page, the answer is caching
the rendered response, not a faster stack.

The good news is that the real workload is nowhere near it. A hundred terminals
polling every 10 seconds is 10 req/s. 2000 req/s is a burst-tolerance target,
and burst tolerance is what the backlog and worker settings below buy you.

## Why your benchmark showed 16 req/s

```
ab -n 5000 -c 1000 http://127.0.0.1:8001/
Non-2xx responses:  765
Requests per second: 16.57
```

Four separate problems, none of them the application's actual speed:

1. **One worker.** Bare `gunicorn ehajiri.wsgi` runs a *single sync worker* —
   one request at a time, on a 4-core box. This was the dominant factor. Fixed
   by `deploy/gunicorn.conf.py` (4 workers × 4 threads).
2. **Every response was an error.** `Non-2xx responses: 765` — `/` was not a
   routed URL, so `ab` was timing a 404. With `DEBUG=True` that 404 is the
   6.8 KB debug page (note `Document Length: 6810`), which is far more
   expensive to render than any real view. `/` now redirects to the dashboard,
   and `DEBUG` defaults off.
3. **`-c 1000` against 1 worker.** 999 connections sit in the backlog while one
   is served, so you measure queueing, not service time — hence the 23-second
   median. Concurrency above ~4× your worker count only measures the queue.
4. **`socket: Too many open files (24)`** on the `-c 10000` run is your *client*
   hitting its own descriptor limit. `ab` also caps at 20000 connections and is
   single-threaded — at these rates it becomes the bottleneck itself.

## How to measure it properly

```bash
ulimit -n 65535
```

Baseline the stack against the endpoint that does nothing, so you know the
ceiling before application work:

```bash
wrk -t4 -c200 -d30s --latency http://127.0.0.1:8001/healthz/
```

Concurrency of 100–500 is the useful range here; keep `-t` at or below your
core count. If you only have `ab`, use `-k` so it reuses connections:

```bash
ab -k -n 20000 -c 200 http://127.0.0.1:8001/healthz/
```

Run the load generator on a **different machine** if you can. On loopback it
competes with the server for the same four cores, and a single-threaded
generator will usually saturate itself first.

Then measure the endpoints that matter — a device push, and an authenticated
page with a real session cookie. `scripts/loadtest.py` takes `-H` for that.

## What changed

### Worker model (`deploy/gunicorn.conf.py`)
4 workers, one per core, × 4 threads. `preload_app` forks after loading Django
so the interpreter and compiled templates are shared copy-on-write pages rather
than being built four times — roughly 100 MB shared plus a small private set
per worker, instead of 4 × 100 MB. `worker_tmp_dir=/dev/shm` keeps the
heartbeat off disk; `backlog=2048` absorbs bursts; workers recycle every ~20k
requests as a guard against slow growth.

### `DEBUG=False` by default (`ehajiri/settings.py`)
With `DEBUG` on, Django appends every SQL query to `connection.queries_log` for
the process's lifetime — a per-query cost *and* unbounded memory growth, which
on a 500 MB box eventually takes the machine down. Set `DJANGO_DEBUG=1` locally.

### SQLite tuned rather than replaced
PostgreSQL would claim 150–250 MB of a 500 MB budget for itself. SQLite fits
this workload; what it needed was `journal_mode=WAL` (readers stop blocking the
writer — without it, concurrent workers mostly produce "database is locked"),
`synchronous=NORMAL` (no fsync per commit, which on SD/eMMC storage is the
slowest thing in the stack), a 16 MB page cache, `mmap_size`, and
`transaction_mode=IMMEDIATE` to avoid deferred lock upgrades under concurrent
writers. Connections persist across requests (`CONN_MAX_AGE`), keeping the page
cache warm.

Remember SQLite has **one writer at a time**. Everything below is about not
wasting it.

### Device heartbeats no longer write to the database
`get_device()` ran on every `/iclock/` request and wrote `last_seen` every
time — turning read-only polls into writes and letting a rack of idle terminals
monopolise the single write lock. `last_seen` is now persisted at most every
`ADMS_LAST_SEEN_WRITE_SECONDS` (30s) per device; the online/offline display
already tolerates 180s of slack. The device lookup itself is cached for
`ADMS_DEVICE_CACHE_SECONDS`, so the one query every push had to make becomes
one query per device per minute.

*Trade-off:* with the default per-process cache, a device deactivated in the
admin keeps being accepted by other workers for up to 60 s. Set `REDIS_URL` to
make the cache shared and invalidation immediate.

### Punch ingestion is batched (`devices/ingest.py`)
Was 3 queries per punch (resolve enrolment, check duplicate, insert) plus a
fourth to re-resolve the enrolment for the touched day, all with the single
write lock held. Now a fixed handful per batch: one to resolve every enrolment,
one to find existing duplicates, one `bulk_create`. Measured:

| Punches in one upload | Queries before | Queries now |
|---|---|---|
| 1 | ~4 | 5 |
| 10 | ~31 | 5 |
| 50 | ~151 | 5 |
| 200 | ~601 | 6 |

Flat, as intended — a single punch costs one query more than it used to, which
is a fine trade for 200 costing a hundredth. `devices/tests_performance.py`
asserts this stays true. `ignore_conflicts` also handles a concurrent upload of
the same punches, which previously would have aborted the entire batch.

### Per-request work removed
Cached template loader (parse once per worker, not per render); `cached_db`
sessions (no query per authenticated request); `USE_I18N=False` (no translation
lookup per lazy string); WhiteNoise autorefresh off (no `stat()` per static
request); logging at `WARNING` (INFO logged a line per device push).

### nginx
Upstream keepalive — without it every request opens a TCP connection, and a
sustained load test exhausts the ephemeral port range and reports connection
errors that look like the app failing. Static files served straight from disk
instead of occupying a gunicorn worker. `gzip_static` reuses the `.gz` files
`collectstatic` already wrote. See the comment block at the end of the site
config for the host-wide `nginx.conf` settings it assumes.

## Memory budget

| | |
|---|---|
| gunicorn master + 4 preloaded workers | ~200–240 MB |
| nginx (4 workers) | ~20 MB |
| redis (optional, small dataset) | ~15 MB |
| Celery worker (optional, 1 process) | ~60 MB |
| OS, sshd, journald | ~80 MB |
| **Total** | **~380–420 MB** |

That is snug in 500 MB. `MemoryHigh=320M` / `MemoryMax=400M` in the systemd
unit make gunicorn the thing that gets reclaimed and killed if something grows,
rather than the kernel OOM killer choosing sshd or redis.

If it is too tight, in order: drop `GUNICORN_THREADS` to 2, drop
`SQLITE_CACHE_KB` to 8000, then `WEB_CONCURRENCY` to 3. Dropping workers costs
the most throughput, so it goes last.

## If you genuinely need 2000 req/s on the dashboard

The stack cannot render it 2000 times a second on this hardware; do not render
it 2000 times a second.

1. **Cache the expensive fragments.** The dashboard's monthly aggregates change
   when a punch arrives, not per request — `{% cache %}` them for 60 s,
   keyed by employee.
2. **Cache whole responses** for anonymous pages with Django's cache
   middleware, or in nginx with `proxy_cache`, which never wakes a worker at
   all.
3. **Fix the queries before adding hardware.** Run the dashboard under
   `django-debug-toolbar` locally; unbounded page sizes and missing
   `select_related` cost more than any setting in this document.
4. **Then add cores.** This app is CPU-bound on template rendering, so
   throughput scales close to linearly with core count.
