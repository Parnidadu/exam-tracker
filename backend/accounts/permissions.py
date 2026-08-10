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


class IsVerifierOrAdmin(BasePermission):
    """Verifier or admin for *every* method, reads included.

    Unlike IsVerifierOrAdminOrReadOnly above, this does not open reads to
    the world - and that is the point. A discrepancy starts life as
    `reported`: an unverified claim that an exam's paper leaked, naming a
    real board and a real exam. Publishing those before anyone has checked
    them would turn this app into a rumour mill, which is the opposite of
    what it is for.

    The public discrepancy feed (EXT-055) decides separately which subset
    is safe to show; this endpoint is the verifier's working surface.
    """

    def has_permission(self, request: "Request", view: "APIView") -> bool:
        user = request.user
        return bool(
            user and user.is_authenticated and user.role in {Role.ADMIN, Role.VERIFIER}
        )
