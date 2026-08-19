"""
Django settings for ehajiri project.

Tuned to run on a small ARM box: 4 cores, 500 MB of RAM. Everything that costs
memory or per-request CPU is switched by an environment variable so the same
file serves a laptop and the server; see deploy/PERFORMANCE.md for the
reasoning and the measurements behind the defaults.
"""

import os
from pathlib import Path

from django.contrib.messages import constants as message_constants

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default):
    """Read a boolean from the environment. '0', 'false', 'no', '' are False."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ('0', 'false', 'no', '')


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# Set DJANGO_SECRET_KEY in the service environment; the literal below is a
# development fallback only.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-twm2ajx1fo7tgub!4p=dvy^j=r6*!5o&j#3c-*-3uhf-py%2!-',
)

# SECURITY WARNING: don't run with debug turned on in production!
#
# DEBUG is also the most expensive setting in this file: with it on, Django
# appends every SQL query it runs to connection.queries_log for the life of the
# process. Under sustained load that is both a per-query cost and an unbounded
# memory growth a 500 MB box cannot absorb. Default off; opt in locally with
# DJANGO_DEBUG=1.
DEBUG = env_bool('DJANGO_DEBUG', False)

# The public hostname this deployment serves. Everything host-dependent below
# is derived from it, so a new domain is a one-line change.
SITE_DOMAIN = os.environ.get('SITE_DOMAIN', 'attendance.thedeepit.com')

# Extra hostnames or IPs this deployment answers to, comma separated. Not
# needed for the biometric terminals, which reach the site over the internet on
# SITE_DOMAIN and so are already covered below; it exists for the case where
# something addresses this server by another name or by IP, which Django would
# otherwise reject with 400 DisallowedHost:
#     EXTRA_ALLOWED_HOSTS=attendance.lan,192.168.1.50
EXTRA_ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get('EXTRA_ALLOWED_HOSTS', '').split(',')
    if host.strip()
]

# Hosts this site will answer to. Note that once this list is non-empty,
# Django enforces it even with DEBUG on, so the loopback IP is listed
# alongside 'localhost' to keep the dev server reachable either way.
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    SITE_DOMAIN,
    *EXTRA_ALLOWED_HOSTS,
]

# Origins allowed to submit forms. Django 4+ checks the Origin header on
# unsafe requests, and when nginx terminates TLS the browser sends
# https://... while Django sees a plain http request, so the https origin
# must be listed explicitly or every POST fails CSRF verification.
#
# The extra hostnames are included for the same reason they are in
# ALLOWED_HOSTS. Listing a name there only gets a page to render on it; every
# form on that page still fails to submit unless the name is also trusted
# here, which made an alias domain look like it "half worked" - pages load,
# logging in does not.
CSRF_TRUSTED_ORIGINS = [
    f'https://{SITE_DOMAIN}',
    f'http://{SITE_DOMAIN}',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    *(f'{scheme}://{host}'
      for host in EXTRA_ALLOWED_HOSTS
      for scheme in ('https', 'http')),
]

# TLS terminates at Cloudflare and nginx forwards the original scheme, so
# request.is_secure() has to come from the header rather than from this
# origin's plain-HTTP leg. nginx overwrites X-Forwarded-Proto on every proxied
# request, so a client cannot forge it.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_celery_beat',
    'core',
    'devices',
    'attendance',
    'leaves',
    'travel_orders',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Must sit directly after SecurityMiddleware. Serves everything under
    # STATIC_ROOT with correct MIME types and cache headers, so static files
    # work whether traffic arrives via nginx, a tunnel, or gunicorn directly.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'ehajiri.urls'

# Template loading. With DEBUG off, the cached loader parses each template once
# per worker process and reuses the compiled nodelist, removing a stat() and a
# parse from every render. It must not be used with DEBUG on, or template edits
# would not appear until restart - hence the explicit loaders list rather than
# APP_DIRS, which cannot be combined with 'loaders'.
_TEMPLATE_LOADERS = [
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
]
if not DEBUG:
    _TEMPLATE_LOADERS = [('django.template.loaders.cached.Loader', _TEMPLATE_LOADERS)]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'OPTIONS': {
            'loaders': _TEMPLATE_LOADERS,
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.navigation',
            ],
        },
    },
]

WSGI_APPLICATION = 'ehajiri.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
#
# SQLite is kept deliberately. On a 500 MB / 4-core box, PostgreSQL would want
# 150-250 MB of that budget for itself plus a backend process per connection,
# and this workload - a few thousand employees, punches arriving in small
# batches - fits SQLite comfortably. What SQLite needs in order to survive
# concurrency is the pragmas below:
#
#   journal_mode=WAL   readers stop blocking the writer and vice versa. Without
#                      it, every read serialises behind any write and the
#                      workers spend their time on 'database is locked'. This
#                      is persistent state on the file, so it is set once, but
#                      re-issuing it per connection is harmless.
#   synchronous=NORMAL in WAL mode this is durable across a process crash and
#                      risks only the last transactions on a power cut, in
#                      exchange for not fsync'ing every commit. FULL costs an
#                      fsync per write, which on the SD/eMMC storage these ARM
#                      boards use is the slowest thing in the whole stack.
#   cache_size=-8000   8 MB of page cache PER CONNECTION (negative = KiB).
#                      Read that again: per connection, and there is one per
#                      worker thread, so the worst case is
#                      WEB_CONCURRENCY x GUNICORN_THREADS x this. At the
#                      default 4 x 4 that is 128 MB on a box with 655 MB total,
#                      which is the most that can be justified. Raise it only
#                      if you also reduce threads. Pages are read in on demand,
#                      so the steady-state cost is far lower - today's database
#                      is under a megabyte and will never approach the ceiling.
#   temp_store=MEMORY  sorts and temporary B-trees stay off disk.
#   mmap_size          reads served from the page cache with no read() syscall
#                      and no extra copy.
#   busy_timeout       a writer that finds the lock held waits for it instead
#                      of failing immediately with OperationalError.
#
# transaction_mode='IMMEDIATE' takes the write lock when the transaction opens
# rather than on its first write, which removes the deferred-to-write lock
# upgrade that is the usual source of SQLITE_BUSY under concurrent writers.
SQLITE_CACHE_KB = env_int('SQLITE_CACHE_KB', 8000)
SQLITE_MMAP_BYTES = env_int('SQLITE_MMAP_BYTES', 134217728)  # 128 MB
SQLITE_BUSY_TIMEOUT_MS = env_int('SQLITE_BUSY_TIMEOUT_MS', 10000)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('SQLITE_PATH', BASE_DIR / 'db.sqlite3'),
        # Keep the connection, and its warmed page cache, across requests
        # instead of reopening the file each time. Each worker thread holds its
        # own, so this scales with workers x threads, not with request rate.
        'CONN_MAX_AGE': env_int('CONN_MAX_AGE', 600),
        'CONN_HEALTH_CHECKS': False,
        'OPTIONS': {
            'timeout': SQLITE_BUSY_TIMEOUT_MS / 1000,
            'transaction_mode': 'IMMEDIATE',
            'init_command': (
                'PRAGMA journal_mode=WAL;'
                'PRAGMA synchronous=NORMAL;'
                f'PRAGMA cache_size=-{SQLITE_CACHE_KB};'
                'PRAGMA temp_store=MEMORY;'
                f'PRAGMA mmap_size={SQLITE_MMAP_BYTES};'
                f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};'
            ),
        },
    }
}


# Cache
# -----
# Two backends, selected by whether REDIS_URL is set:
#
#   * LocMemCache (default): per-process, no extra daemon, no extra RAM beyond
#     the entries themselves. Sufficient for what this project caches - device
#     records and session data - because those are read far more often than
#     written, and one worker holding a copy a few seconds stale costs nothing.
#   * Redis, when a broker is already running for Celery. Shared across
#     workers, so a device record written by one worker is seen by all.
#
# MAX_ENTRIES is capped deliberately: an unbounded per-process cache is how a
# 500 MB box runs out of memory at 3am.
REDIS_URL = os.environ.get('REDIS_URL', '')

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'KEY_PREFIX': 'eh',
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'eattendance',
            'OPTIONS': {
                'MAX_ENTRIES': env_int('CACHE_MAX_ENTRIES', 2000),
                'CULL_FREQUENCY': 4,
            },
        }
    }

# The session is read on every authenticated request. 'cached_db' answers that
# read from the cache and touches the database only on a miss or a write,
# removing one query from every page an employee loads.
SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'
SESSION_COOKIE_AGE = env_int('SESSION_COOKIE_AGE', 60 * 60 * 12)
# Do not rewrite the session row on every request.
SESSION_SAVE_EVERY_REQUEST = False


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

# Custom User Model
AUTH_USER_MODEL = 'core.User'

TIME_ZONE = 'Asia/Dhaka'

# This project's own templates carry no {% trans %} tags and it ships in one
# language, so the translation machinery - a locale lookup behind every lazy
# string rendered - is overhead on every page. The Django admin's own strings
# fall back to the untranslated English source, which is what this deployment
# displays anyway. Set USE_I18N=1 if a translation is ever added.
USE_I18N = env_bool('USE_I18N', False)

USE_TZ = True


# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Celery Configuration
CELERY_BROKER_URL = os.environ.get(
    'CELERY_BROKER_URL', REDIS_URL or 'redis://localhost:6379/0'
)
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
# Nothing inspects the result of the sync and rollup tasks, and storing them
# costs a Redis write plus a key with a TTL for every task run.
CELERY_TASK_IGNORE_RESULT = True

# Static and Media
# https://docs.djangoproject.com/en/5.2/howto/static-files/
STATIC_URL = 'static/'

# Source directory for this project's own assets (app.css, app.js).
STATICFILES_DIRS = [BASE_DIR / 'static']

# Destination for `manage.py collectstatic`: the directory the web server
# serves /static/ from. Override with the STATIC_ROOT environment variable on
# machines where that path does not exist, e.g. a Windows dev box.
STATIC_ROOT = Path(os.environ.get('STATIC_ROOT', f'/var/www/{SITE_DOMAIN}'))

# WhiteNoise storage: hashes filenames on collectstatic (app.4f2a1c9d.css) and
# pre-compresses them, which makes a one-year immutable cache safe because a
# changed file gets a new URL.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'core.storage.ResilientManifestStaticFilesStorage',
    },
}

# WhiteNoise serves static files only. User uploads (MEDIA_ROOT) still need
# nginx or a permission-checking Django view.
MEDIA_URL = 'media/'
MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', BASE_DIR / 'media'))

# WhiteNoise tuning. With autorefresh off, the file list is built once at
# startup and requests are answered from an in-process map with no stat() per
# request. It must stay off in production, where collectstatic has already run;
# with it on, WhiteNoise re-checks the filesystem on every single request.
# The one-year max-age is safe because collectstatic hashes filenames.
WHITENOISE_AUTOREFRESH = DEBUG
WHITENOISE_MAX_AGE = 0 if DEBUG else 31536000
# Consult the staticfiles finders only in development. In production everything
# has been collected into STATIC_ROOT already.
WHITENOISE_USE_FINDERS = DEBUG

# Employees created automatically from a device enrolment have no real email
# address. The placeholder uses our own domain so nothing is ever addressed to
# a third party's mail server; set it to a reserved domain such as
# 'invalid' if you would rather these never resemble deliverable addresses.
PLACEHOLDER_EMAIL_DOMAIN = os.environ.get('PLACEHOLDER_EMAIL_DOMAIN', SITE_DOMAIN)

# Login URLs
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

# Request body limits. These bound how much memory one request can claim, which
# matters more here than usual: /iclock/ is public, and the body of a POST is
# read into RAM up to this ceiling before any view sees it.
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int('DATA_UPLOAD_MAX_MEMORY_SIZE', 2621440)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int('FILE_UPLOAD_MAX_MEMORY_SIZE', 1048576)
# A device push is one body, not a form; the largest real form here is a shift
# or travel-order submission.
DATA_UPLOAD_MAX_NUMBER_FIELDS = env_int('DATA_UPLOAD_MAX_NUMBER_FIELDS', 200)

# Map Django message levels onto Bootstrap alert classes. Without this, an
# error message renders as "alert-error", which Bootstrap does not define, so
# error messages appear unstyled.
MESSAGE_TAGS = {
    message_constants.DEBUG: 'secondary',
    message_constants.ERROR: 'danger',
}

# Attendance rules
# ----------------
# Weekly off days, using Python's weekday() numbering (Monday=0 ... Sunday=6).
# Saturday and Sunday.
#
# NOTE: the punches already in this database show a Sunday-Friday working week
# (Saturday: 4 punches on 4 dates; Sunday: 217 punches on 43 dates). If that is
# this office's actual pattern, set WEEKEND_DAYS = [5] for Saturday only,
# otherwise every Sunday worked is booked entirely as overtime.
WEEKEND_DAYS = [5, 6]

# Two scans closer together than this are the same person re-presenting a
# finger, not a check-in followed by a check-out.
MINIMUM_PUNCH_GAP_MINUTES = 5

# Overtime policy
OVERTIME_MINIMUM_MINUTES = 30   # ignore anything below this; avoids OT noise
OVERTIME_ROUNDING_MINUTES = 15  # round OT down to this increment
# Count overtime only for time worked after the shift ends. When False,
# overtime is any net worked time beyond the shift's scheduled hours.
OVERTIME_AFTER_SHIFT_END_ONLY = True
# Minimum net worked hours before a day counts as a full day rather than half.
HALF_DAY_MAX_HOURS = 4

# ADMS / WDMS push protocol
# -------------------------
# Devices post to /iclock/ instead of being polled over TCP. Configure the
# terminal with: Comm. -> Ethernet -> Cloud Server / ADMS -> SITE_DOMAIN, port
# 80. The terminals are at remote sites and reach us over the internet, so
# /iclock/ is publicly exposed; see the deploy notes for what guards it.
# Auto-register unknown serial numbers as inactive devices awaiting approval.
# With this off, unknown devices are rejected outright - which is what a
# public /iclock/ wants once the real terminals are enrolled, since a serial
# number is the only credential the protocol has. Turn it off by setting
# ADMS_AUTO_REGISTER_DEVICES=0 in the service environment; no code change and
# no redeploy, so it can be flipped the moment enrolment finishes.
ADMS_AUTO_REGISTER_DEVICES = env_bool('ADMS_AUTO_REGISTER_DEVICES', True)
# Roll pushed punches into daily summaries immediately, so the app works
# without a Celery worker running. Turn this off (ADMS_PROCESS_ON_PUSH=0) once
# a worker is running: the rollup is by far the most expensive part of handling
# a push, and it does not have to happen inside the device's request.
ADMS_PROCESS_ON_PUSH = env_bool('ADMS_PROCESS_ON_PUSH', True)
# Maximum queued commands handed to a device in a single poll.
ADMS_MAX_COMMANDS_PER_POLL = env_int('ADMS_MAX_COMMANDS_PER_POLL', 10)
# A device is shown as offline if it has not contacted us within this window.
ADMS_OFFLINE_AFTER_SECONDS = env_int('ADMS_OFFLINE_AFTER_SECONDS', 180)

# How long a resolved device record is reused from the cache instead of being
# re-read from the database. Every ADMS request has to identify its device
# first, so this turns the one guaranteed query per push into one query per
# device per interval. Keep it well below ADMS_OFFLINE_AFTER_SECONDS, and note
# that with the local-memory cache a device deactivated in the admin keeps
# being served for up to this long by workers that already have it cached.
ADMS_DEVICE_CACHE_SECONDS = env_int('ADMS_DEVICE_CACHE_SECONDS', 60)

# Devices heartbeat every few seconds. Writing last_seen on each contact turns
# a read-only poll into a database write, and SQLite has exactly one writer -
# so a rack of chatty terminals can saturate it with nothing but heartbeats.
# Persist last_seen at most this often per device; the online/offline display
# tolerates that, since it already allows ADMS_OFFLINE_AFTER_SECONDS of slack.
ADMS_LAST_SEEN_WRITE_SECONDS = env_int('ADMS_LAST_SEEN_WRITE_SECONDS', 30)

# Logging
# -------
# At high request rates the logging call itself is measurable: the message is
# formatted, then written to stdout, which under systemd means journald has to
# absorb it. INFO in this project logs a line per device push. WARNING is the
# production default; export LOG_LEVEL=INFO while diagnosing a device.
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG' if DEBUG else 'WARNING').upper()

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'},
    },
    'loggers': {
        'devices': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
        'attendance': {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False},
    },
}
