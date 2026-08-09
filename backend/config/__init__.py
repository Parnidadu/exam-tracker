"""Importing the Celery app here is what makes `@shared_task` work.

`shared_task` binds to whichever Celery app is current at import time, so
the app has to exist before any app module is imported. Django imports
this package on startup, which makes it the one reliable hook.
"""

from config.celery import app as celery_app

__all__ = ("celery_app",)
