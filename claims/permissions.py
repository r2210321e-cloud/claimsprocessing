"""
Custom Permissions — Motor Insurance Claims System
"""
from rest_framework.permissions import BasePermission


class IsClientUser(BasePermission):
    """Only clients can access this endpoint."""
    message = 'Only client accounts can perform this action.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_client)


class IsAdjusterOrAdmin(BasePermission):
    """Only adjusters or admins can access this endpoint."""
    message = 'Only adjusters or administrators can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            (request.user.is_adjuster or request.user.is_admin_user or request.user.is_staff)
        )


class IsOwnerOrAdmin(BasePermission):
    """Object-level: only the owner or an admin can modify the object."""
    message = 'You do not have permission to access this resource.'

    def has_object_permission(self, request, view, obj):
        if request.user.is_admin_user or request.user.is_staff:
            return True
        # Check if object has an 'owner' or 'client' field
        if hasattr(obj, 'owner'):
            return obj.owner == request.user
        if hasattr(obj, 'client'):
            return obj.client == request.user
        return False
