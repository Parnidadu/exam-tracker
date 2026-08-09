import os
from pathlib import Path

from config.observability import init_sentry

BASE_DIR = Path(__file__).resolve().parent.parent.parent

init_sentry()

# Secrets and environment-specific values come from the environment only -
# never hardcode a default here. See .env.example for the full variable list.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "simple_history",
    "django_celery_beat",
    "accounts",
    "exams",
    "scraping",
    "verification",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.observability.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must come after AuthenticationMiddleware: it reads request.user to
    # stamp history_user on each record. Without it, "who" is always null
    # for changes made over HTTP.
    "simple_history.middleware.HistoryRequestMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = {
    "default": {
        # Redis is already provisioned in docker-compose (EXT-002). Falls back
        # to a local-memory cache when REDIS_URL is unset, so the test suite and
        # a bare `manage.py runserver` work without a running Redis.
        "BACKEND": (
            "django.core.cache.backends.redis.RedisCache"
            if os.environ.get("REDIS_URL")
            else "django.core.cache.backends.locmem.LocMemCache"
        ),
        "LOCATION": os.environ.get("REDIS_URL", "exam-tracker-locmem"),
    }
}

#: How long a cached public read may be served. Short on purpose: a
#: verification write busts the cache immediately, so this only bounds
#: staleness from writes that do not (scraper runs, admin edits).
PUBLIC_CACHE_TTL = int(os.environ.get("PUBLIC_CACHE_TTL", "60"))

# --- Scraper politeness (EXT-041) -------------------------------------
#: Sent on every scrape request so operators can identify and contact us.
SCRAPER_USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "ExamTrackerBot/1.0 (+https://github.com/parnidadu/exam-tracker)",
)
#: Minimum gap between two requests to the same domain.
SCRAPER_RATE_LIMIT_SECONDS = float(os.environ.get("SCRAPER_RATE_LIMIT_SECONDS", "2"))
#: Give up waiting for a domain's slot after this long.
SCRAPER_RATE_LIMIT_MAX_WAIT = float(os.environ.get("SCRAPER_RATE_LIMIT_MAX_WAIT", "30"))
#: Total attempts per URL, including the first.
SCRAPER_MAX_ATTEMPTS = int(os.environ.get("SCRAPER_MAX_ATTEMPTS", "3"))
#: First backoff pause; doubles each retry.
SCRAPER_BACKOFF_BASE = float(os.environ.get("SCRAPER_BACKOFF_BASE", "1"))
SCRAPER_TIMEOUT = float(os.environ.get("SCRAPER_TIMEOUT", "10"))
#: How long a fetched robots.txt stays cached.
SCRAPER_ROBOTS_TTL = int(os.environ.get("SCRAPER_ROBOTS_TTL", "3600"))

# --- Source health (EXT-047) ------------------------------------------
#: How many of a source's own scheduled runs may be missed before it is
#: called stale. Counted against that source's cron rather than a fixed
#: duration, so it means the same thing for an hourly and a weekly board.
#: Three allows for a board being briefly down without crying wolf.
SOURCE_STALE_AFTER_MISSED_RUNS = int(os.environ.get("SOURCE_STALE_AFTER_MISSED_RUNS", "3"))

# --- Celery (EXT-046) -------------------------------------------------
# Falls back to the Redis already provisioned in Compose, then to a local
# default, so `manage.py` and the test suite work without a broker.
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL", os.environ.get("REDIS_URL", "redis://redis:6379/0")
)
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)

#: The whole point of the ticket. The default scheduler reads its schedule
#: once at startup from a Python dict, so a source enabled in admin would
#: not run until beat was restarted. The database scheduler re-reads the
#: schedule when it changes, which is what makes an admin toggle take
#: effect on a running process.
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

#: A scrape that hangs must not hold a worker slot forever, or a source
#: with a broken endpoint slowly starves every other source.
CELERY_TASK_SOFT_TIME_LIMIT = int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "300"))
CELERY_TASK_TIME_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", "360"))

#: Redelivery on worker loss is wrong for scraping: the run is periodic, so
#: a lost task is picked up by the next tick rather than re-fetching a
#: board we may already have hit.
CELERY_TASK_ACKS_LATE = False

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PERMISSION_CLASSES": ["accounts.permissions.IsVerifierOrAdminOrReadOnly"],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Exam Tracker API",
    "DESCRIPTION": "Read API for tracked exams and their stages.",
    "VERSION": "1.0.0",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "config.observability.RequestIDFilter"},
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
