import pytest
from django.contrib import admin

from accounts.models import Role, User


def test_user_registered_in_admin():
    assert admin.site.is_registered(User)


@pytest.mark.django_db
def test_staff_can_create_user_via_admin(admin_client):
    response = admin_client.post(
        "/admin/accounts/user/add/",
        {
            "email": "new-verifier@example.com",
            "role": Role.VERIFIER,
            "password1": "a-strong-password-123",
            "password2": "a-strong-password-123",
        },
    )
    assert response.status_code == 302
    user = User.objects.get(email="new-verifier@example.com")
    assert user.role == Role.VERIFIER
