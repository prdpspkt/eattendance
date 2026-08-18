"""Gunicorn configuration for the 4-core / 500 MB ARM host.

Run it with:

    gunicorn -c deploy/gunicorn.conf.py ehajiri.wsgi:application

Every value can be overridden from the environment, so the systemd unit can
tune the box without editing this file.

The default gunicorn invocation - no config at all - runs **one** sync worker.
That single process handles exactly one request at a time on a 4-core machine,
which is what a benchmark showing tens of requests per second is usually
measuring. Nothing else in this file matters as much as the worker count.
"""
import multiprocessing
import os


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8001')

# --- workers ---------------------------------------------------------------
# One worker per core. The usual (2 x cores + 1) rule targets sync workers that
# block on a slow database or an upstream API; here the database is a local
# file and the work is CPU-bound Python, so more processes than cores only adds
# context switching and, on a 500 MB box, memory this host does not have.
#
# The classic rule is the right one if you move to a remote PostgreSQL, where
# workers really do sit waiting on the network.
workers = _int('WEB_CONCURRENCY', multiprocessing.cpu_count())

# Threads within each worker. Python's GIL means these add no CPU throughput,
# but they let a worker keep serving while another request waits on disk - a
# SQLite read that misses the page cache, or a static file. Four processes x
# four threads is 16 requests in flight, enough to keep all four cores busy
# without the memory cost of 16 processes.
#
# Set GUNICORN_THREADS=1 to fall back to plain sync workers: marginally faster
# per request, at the cost of head-of-line blocking behind any slow view
# (report generation and device polling are the ones here that can be slow).
threads = _int('GUNICORN_THREADS', 4)
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'gthread' if threads > 1 else 'sync')

# --- memory --------------------------------------------------------------
# preload_app imports Django once in the master and then forks. The
# interpreter, the loaded modules, the compiled templates and the URL resolver
# all start as copy-on-write pages shared between workers instead of being
# built independently in each - the difference between roughly 4 x 100 MB and
# roughly 100 MB shared plus a small private set per worker.
#
# The catch: nothing may hold a resource across the fork. Django opens database
# connections lazily per worker, so SQLite is fine here.
preload_app = True

# Recycle workers periodically. This is a safety net for slow growth - a cache
# that never evicts, a C extension that fragments its heap - rather than a
# known leak: on a 500 MB box a worker that grows unnoticed takes the whole
# machine down. The jitter stops all four restarting at the same moment.
max_requests = _int('GUNICORN_MAX_REQUESTS', 20000)
max_requests_jitter = _int('GUNICORN_MAX_REQUESTS_JITTER', 2000)

# Gunicorn's worker heartbeat file. It defaults to /tmp, which on a box with a
# disk-backed /tmp means every worker touches the disk on a timer; /dev/shm is
# a tmpfs, so the heartbeat stays in memory. If /dev/shm is missing (some
# containers), drop this line rather than pointing it back at a real disk.
worker_tmp_dir = '/dev/shm' if os.path.isdir('/dev/shm') else None

# --- connections -----------------------------------------------------------
# Pending connections the kernel will hold while all workers are busy. A burst
# larger than this is refused at the TCP level, which a load generator reports
# as a connection error rather than a slow response. 2048 absorbs a burst of
# device check-ins without pretending the app can serve them instantly.
backlog = _int('GUNICORN_BACKLOG', 2048)

# Hold idle connections open for nginx to reuse. nginx is configured with an
# upstream keepalive pool, and this must be longer than nginx's own idle time
# or gunicorn will close connections nginx still believes are usable, which
# surfaces as sporadic 502s under load.
keepalive = _int('GUNICORN_KEEPALIVE', 5)

# A request still running after this is killed and its worker replaced. Long
# enough for a monthly Excel report, short enough that a wedged worker does not
# stay wedged. Report generation that legitimately exceeds this belongs in
# Celery, not in a web request.
timeout = _int('GUNICORN_TIMEOUT', 60)
graceful_timeout = _int('GUNICORN_GRACEFUL_TIMEOUT', 30)

# Trust the forwarded headers only from the local nginx. X-Forwarded-For
# decides which IP is recorded for a pushing device, so it must not be
# accepted from arbitrary clients.
forwarded_allow_ips = os.environ.get('FORWARDED_ALLOW_IPS', '127.0.0.1')

# --- logging ---------------------------------------------------------------
# Access logging is off by default. At a few thousand requests per second it is
# a formatted write per request, and nginx already logs the same traffic one
# hop earlier. Set GUNICORN_ACCESS_LOG=- to send it to stdout while debugging.
accesslog = os.environ.get('GUNICORN_ACCESS_LOG') or None
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'warning')
