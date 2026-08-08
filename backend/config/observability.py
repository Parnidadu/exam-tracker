import contextvars
import logging
import os
import uuid

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIDMiddleware:
    """Stamps every request/response with an X-Request-Id, generating one
    when the caller didn't send it, and exposes it to the logging filter
    below via a contextvar for the life of the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-Id"] = request_id
        return response


class RequestIDFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def init_sentry() -> None:
    """No-op until SENTRY_DSN is set - safe to call in dev/CI where no
    Sentry project exists yet."""
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN", ""),
        integrations=[DjangoIntegration()],
        traces_sample_rate=0,
        send_default_pii=False,
    )
