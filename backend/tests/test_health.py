from django.test import Client


def test_health_endpoint_returns_ok():
    response = Client().get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
