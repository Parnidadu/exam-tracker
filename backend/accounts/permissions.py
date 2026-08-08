from typing import TYPE_CHECKING

from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Role

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView


class IsVerifierOrAdminOrReadOnly(BasePermission):
    """Reads are open to everyone; writes require an authenticated user
    with the verifier or admin role."""

    def has_permission(self, request: "Request", view: "APIView") -> bool:
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        return bool(
            user and user.is_authenticated and user.role in {Role.ADMIN, Role.VERIFIER}
        )
