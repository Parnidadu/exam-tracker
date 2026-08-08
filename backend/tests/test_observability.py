import logging

import sentry_sdk
from django.test import Client

from config.observability import RequestIDFilter, init_sentry, request_id_var


def test_request_id_middleware_generates_id_when_absent():
    response = Client().get("/health/")
    assert response.headers["X-Request-Id"]


def test_request_id_middleware_echoes_incoming_id():
    response = Client().get("/health/", HTTP_X_REQUEST_ID="fixed-abc-123")
    assert response.headers["X-Request-Id"] == "fixed-abc-123"


def test_request_id_filter_stamps_log_record_from_contextvar():
    token = request_id_var.set("test-request-id")
    try:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)
        RequestIDFilter().filter(record)
        assert record.request_id == "test-request-id"
    finally:
        request_id_var.reset(token)


def test_init_sentry_has_no_transport_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    init_sentry()
    assert sentry_sdk.get_client().transport is None


def test_init_sentry_activates_transport_with_dsn(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
    init_sentry()
    assert sentry_sdk.get_client().transport is not None
