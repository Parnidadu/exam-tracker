import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from accounts.models import Role, User
from accounts.permissions import IsVerifierOrAdminOrReadOnly


@pytest.fixture
def factory():
    return RequestFactory()


def _request(factory, method, user):
    request = getattr(factory, method.lower())("/")
    request.user = user
    return request


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_are_always_allowed(factory, method):
    request = _request(factory, method, AnonymousUser())
    assert IsVerifierOrAdminOrReadOnly().has_permission(request, None) is True


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_denied_for_anonymous_user(factory, method):
    request = _request(factory, method, AnonymousUser())
    assert IsVerifierOrAdminOrReadOnly().has_permission(request, None) is False


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_denied_for_viewer_role(factory, method):
    user = User.objects.create_user(email="viewer@example.com", password="pw", role=Role.VIEWER)
    request = _request(factory, method, user)
    assert IsVerifierOrAdminOrReadOnly().has_permission(request, None) is False


@pytest.mark.django_db
@pytest.mark.parametrize("role", [Role.VERIFIER, Role.ADMIN])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_allowed_for_verifier_and_admin_roles(factory, method, role):
    user = User.objects.create_user(email=f"{role}@example.com", password="pw", role=role)
    request = _request(factory, method, user)
    assert IsVerifierOrAdminOrReadOnly().has_permission(request, None) is True
