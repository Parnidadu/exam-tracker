"""The Celery application.

Until now `worker.py` was a placeholder that only touched a heartbeat file
so Compose had something to health-check (EXT-002). This is the real thing.

Beat's schedule is *not* defined here. It lives in the database and is
derived from `Source` rows, so that enabling or disabling a source in
admin takes effect without restarting the beat process - see
`scraping.schedules` and `CELERY_BEAT_SCHEDULER` in settings.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("exam_tracker")

# Every Celery setting is a CELERY_-prefixed Django setting, which keeps
# the project's "configuration comes from the environment only" rule
# intact for the worker as much as for the web process.
app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()
