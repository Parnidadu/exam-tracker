import pytest

from accounts.models import Role, User


@pytest.mark.django_db
def test_create_user_defaults_to_viewer_role():
    user = User.objects.create_user(email="verifier-candidate@example.com", password="pw")
    assert user.role == Role.VIEWER
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_create_user_normalizes_and_requires_email():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="pw")


@pytest.mark.django_db
def test_create_superuser_defaults_to_admin_role():
    user = User.objects.create_superuser(email="admin@example.com", password="pw")
    assert user.role == Role.ADMIN
    assert user.is_staff is True
    assert user.is_superuser is True


@pytest.mark.django_db
def test_user_authenticates_by_email_not_username():
    assert User.USERNAME_FIELD == "email"
    user = User.objects.create_user(email="someone@example.com", password="correct-password")
    assert user.check_password("correct-password")


def test_user_str_is_email():
    user = User(email="someone@example.com")
    assert str(user) == "someone@example.com"
