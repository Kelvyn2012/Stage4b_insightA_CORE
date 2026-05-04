from rest_framework.permissions import BasePermission, IsAuthenticated


class IsActiveUser(IsAuthenticated):
    """Authenticated + active. Base permission for all /api/* endpoints."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if not getattr(request.user, "is_active", False):
            self.message = "Your account has been deactivated."
            return False
        return True


class IsAdminRole(IsActiveUser):
    """Active user with role=admin."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if getattr(request.user, "role", None) != "admin":
            self.message = "Admin access required."
            return False
        return True


class IsAnalystOrAdmin(IsActiveUser):
    """Active user with role=analyst or role=admin."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if getattr(request.user, "role", None) not in ("admin", "analyst"):
            self.message = "Insufficient role."
            return False
        return True
