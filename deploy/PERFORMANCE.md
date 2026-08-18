# Performance notes: 4 cores, 655 MB, ARM (HiSilicon Hi3798)

What was changed to make this deployment fast, what it can realistically serve,
and how to measure it without measuring the wrong thing.

## Read this first: what 2000 req/s means here

Throughput is not a property of a server, it is a property of a server *and an
endpoint*.

The target hardware is a HiSilicon Hi3798 TV box: four Cortex-A53 cores, with
frequency steps of 400 / 600 / 800 / 1200 / **1600** MHz. An A53 is a small
in-order core, roughly 8–12× slower than a current
laptop core on CPython. For reference, `/healthz/` through the full Django WSGI
stack measures **0.104 ms/request single-threaded on an x86 laptop**; scale that
by the core ratio, then subtract gunicorn and kernel networking overhead, to get:

| Endpoint | What it costs | Realistic on this box (all 4 cores, at full clock) |
|---|---|---|
| `/static/*` via nginx | a file read | 3000+ req/s |
| `/healthz/` | no DB, no template | **800–2000 req/s** — 2000 is the optimistic ceiling |
| `/iclock/*` heartbeats (`ping`, `getrequest`) | 0–1 cached reads | 400–1000 req/s |
| `/iclock/cdata` punch uploads | parse + batched writes | 100–300 uploads/s, each carrying many punches |
| `/dashboard/`, report pages | 5–15 queries + template render | **30–100 req/s** |

**2000 req/s is not achievable on this hardware for anything that touches the
database or renders a template.** It is borderline-plausible for `/healthz/`
with everything tuned. No configuration change moves the dashboard there: at
2000 req/s each core would have 2 ms per request, and the template render alone
costs far more than that on an A53. If a page genuinely needs that rate, the
answer is caching the rendered response so it is not rendered 2000 times a
second — see the last section — or different hardware.

Worth keeping in perspective: the real workload is a hundred terminals polling
every ten seconds, which is 10 req/s. 2000 is a burst-tolerance target, and
burst tolerance is what the backlog and worker settings buy.

## Why your benchmark showed 16 req/s

```
ab -n 5000 -c 1000 http://127.0.0.1:8001/
Non-2xx responses:  765
Requests per second: 16.57
```

Four separate problems, none of them the application's actual speed:

1. **One worker.** Bare `gunicorn ehajiri.wsgi` runs a *single sync worker* —
   one request at a time, on a 4-core box. This was the dominant factor. Fixed
   by `deploy/gunicorn.conf.py`, sized to 8 workers x 2 threads in the
   systemd unit.
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

## The two traps on this hardware

This deployment runs on an Android TV box: HiSilicon Hi3798, four Cortex-A53
cores. Both of the following were found in production and cost roughly 7× of
the machine's throughput between them. Check both after any reimage.

### 1. The CPU governor parks the cores at their floor

Android-derived images ship with `powersave`, and the cores sit at 400 MHz
against a ceiling of 1.2–1.6 GHz.

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor   # want: performance
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq   # want: near cpuinfo_max_freq
```

Fix, persistently:

```bash
sudo apt install -y cpufrequtils
echo 'GOVERNOR="performance"' | sudo tee /etc/default/cpufrequtils
```

These boxes have no airflow, so confirm the clock is actually being held under
sustained load rather than thermally throttled back:

```bash
cat /sys/class/thermal/thermal_zone*/temp   # divide by 1000 for degrees C
```

### 2. gunicorn started without this repo's config

The unit that was running had been launched as:

```
gunicorn --workers 2 --threads 2 --access-logfile - ehajiri.wsgi:application
```

Two workers on a four-core box, no `preload_app`, and an access-log write for
every single request - which on an A53 with the output going to journald is a
significant per-request cost, not a rounding error. Always launch via
`-c deploy/gunicorn.conf.py`, and verify:

```bash
pgrep -c -f gunicorn      # expect workers + 1 (master)
```

### 2b. ...and the old one does not go away when you kill it

This wasted two benchmark rounds, so it is worth stating plainly. The old
`attendance.service` had `Restart=always`. Killing the process with
`pkill -f gunicorn` made systemd start it straight back up, with the old
command line, still bound to port 8001. A hand-started gunicorn using the
correct config was running *at the same time*, and the benchmark was being
served by whichever held the port - so the numbers reflected the old
configuration while everything looked healthy.

Stop the **service**, not the process:

```bash
sudo systemctl stop attendance
pgrep -c -f gunicorn          # must be 0 before you start anything
```

`deploy/deploy.sh` checks for this automatically and refuses to report success
if a process with the old command line is still alive.

The check matches on this repo's path, **not** on "gunicorn", because the host
also runs `deepit.service` - a separate Django site from `/srv/deepit` on port
8000, with its own `--workers 2 --access-logfile -` command line. That is a
legitimate co-tenant, not a competing instance: it binds a different port and
must be left alone. An earlier version of the check counted its processes and
reported a false alarm.

## Measurements from this box

Each row changes exactly one thing from the row above it.

| Configuration | `ab -k -n 20000 -c 200 /healthz/` |
|---|---|
| 400 MHz (powersave), 2 workers, access log on | **149 req/s** |
| 1.6 GHz (performance), 2 workers, access log on | **589 req/s** |
| 1.6 GHz, 8 workers, no access log | *measure and record here* |

The first step is a 3.95x gain from one `echo performance` - almost exactly the
4x the clock ratio predicts, which is also how we knew the worker changes had
*not* taken effect yet.

For reference, `/healthz/` through the full Django WSGI stack costs
**0.104 ms/request** single-threaded on an x86 laptop (~9600 req/s). That is the
application's own cost; everything between it and the numbers above is
hardware, gunicorn and the benchmark client. Note that `ab` runs on the same
four cores it is measuring and consumes roughly one of them, so these figures
understate the server - benchmark from another machine over the LAN for a
truer number.

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

### Worker model (`deploy/gunicorn.conf.py`, sized in the systemd unit)
**8 workers × 2 threads** on this host — the classic 2×cores sizing. The extra
processes beyond four do not add CPU throughput, since four cores can only run
four things at once and the work is CPU-bound Python; what they buy is that a
worker blocked on a SQLite write or a disk read is not a quarter of the server
sitting idle.

Eight is only affordable because of `preload_app`, which imports Django once in
the master and forks, so the interpreter, modules and compiled templates start
as shared copy-on-write pages. The workers on this box measured 55 MB each
*without* preloading — eight of those would be ~440 MB on a 655 MB machine with
no swap.

`worker_tmp_dir=/dev/shm` keeps the heartbeat off disk; `backlog=2048` absorbs
bursts; workers recycle every ~20k requests as a guard against slow growth.

Access logging is **off**. At these rates it is a formatted write per request,
and nginx already logs the same traffic one hop earlier.

### `DEBUG=False` by default (`ehajiri/settings.py`)
With `DEBUG` on, Django appends every SQL query to `connection.queries_log` for
the process's lifetime — a per-query cost *and* unbounded memory growth, which
on a 500 MB box eventually takes the machine down. Set `DJANGO_DEBUG=1` locally.

### SQLite tuned rather than replaced
PostgreSQL would claim 150–250 MB of a 500 MB budget for itself. SQLite fits
this workload; what it needed was `journal_mode=WAL` (readers stop blocking the
writer — without it, concurrent workers mostly produce "database is locked"),
`synchronous=NORMAL` (no fsync per commit, which on SD/eMMC storage is the
slowest thing in the stack), an 8 MB page cache, `mmap_size`, and
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

The box has **655 MB total and no swap**, and **this app is not the only thing
on it**: `deepit.service` runs a second Django site (the thedeepit.com welcome
page, `/srv/deepit`, gunicorn on port 8000) using ~88 MB. Budget accordingly.

| | |
|---|---|
| gunicorn master + 8 preloaded workers | ~200–300 MB |
| `deepit.service` (separate site, port 8000) | ~90 MB |
| nginx (4 workers) | ~20 MB |
| redis (optional, small dataset) | ~15 MB |
| Celery worker (optional, 1 process) | ~60 MB |
| OS, sshd, journald | ~110 MB |
| **Total** | **~495–595 MB of 655 MB** |

Measured after deploying eight workers with both sites running: **300 MB used,
355 MB available**. Comfortable — but the slack disappears if you add Celery
and redis, so measure again if you do.

Eight workers is the setting most likely to need adjusting. `MemoryHigh=360M` /
`MemoryMax=430M` are set *under* the co-tenant's share, so gunicorn is
reclaimed and killed if something grows rather than the kernel OOM killer
choosing sshd or the other site. **Check this after deploying:**

```bash
systemctl status attendance | grep Memory
journalctl -u attendance | grep -i -E "memory|killed"
```

If workers are being killed, drop `WEB_CONCURRENCY` to 6, then 4. Since the
machine has no swap, an over-commitment here is not a slowdown — it is a
process disappearing.

`SQLITE_CACHE_KB` is charged **per connection**, and there is one connection per
worker thread - so the worst case is
`WEB_CONCURRENCY x GUNICORN_THREADS x SQLITE_CACHE_KB`, or 128 MB at the
defaults. That is why the default is 8 MB and not something more generous;
pages are only read in on demand, so the steady state is far below the ceiling,
but the ceiling is what has to fit.

If it is too tight, in order: drop `GUNICORN_THREADS` to 2 (which also halves
that SQLite ceiling), drop `SQLITE_CACHE_KB` to 4000, then `WEB_CONCURRENCY`
to 3. Dropping workers costs the most throughput, so it goes last.

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
