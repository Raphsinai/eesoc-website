"""Committee role presets and the fine-grained permission catalogue.

A role is a convenient starting selection, *not* an authorization boundary.
`admins.permissions IS NULL` follows its role's preset (backwards compatible).
A saved array, including [], is the user's exact effective permission set.
Authorization always uses the effective set resolved from the database on each
request, not a cached browser selection.
"""

from __future__ import annotations

# Single source of truth for validation, backend guards, and checkbox labels.
# Grouping is presentation only; permissions are independent of one another.
PERMISSIONS: dict[str, dict[str, str]] = {
    "users": {"group": "Security", "label": "Manage access & impersonate", "description": "Grant and revoke committee access and sign in as other users."},
    "passkey_links": {"group": "Security", "label": "Recovery passkey links", "description": "Issue and revoke one-time account access links. Committee accounts also require Manage access."},
    "events": {"group": "Events", "label": "Edit all events", "description": "Create, publish and edit any event and its ticket types and product mappings."},
    "industry": {"group": "Events", "label": "Edit industry events", "description": "Create and edit industry-marked events and their ticket types only."},
    "bookings": {"group": "Events", "label": "Manage all bookings", "description": "See attendee details, confirm/cancel bookings, issue tickets and handle waitlists for any event."},
    "industry_bookings": {"group": "Events", "label": "Manage industry bookings", "description": "Manage bookings and waitlists for industry-marked events only."},
    "sync": {"group": "Events", "label": "Sync sales & members", "description": "Trigger upstream sales/member sync and reconcile booking holds."},
    "sponsors": {"group": "Sponsors", "label": "Manage sponsors", "description": "Edit sponsors, agreements, requests, portal members and company listings."},
    "resources": {"group": "Website", "label": "Edit resources", "description": "Edit, publish and import teaching resources and feedback."},
    "files": {"group": "Website", "label": "Manage files", "description": "Upload, browse and delete shared files."},
    "committee": {"group": "Website", "label": "Manage committee page", "description": "Change committee page content, ordering and other members' cards."},
    "product_links": {"group": "Website", "label": "Manage Union product links", "description": "Link eActivities products to lockers, membership and other purposes."},
    "lockers": {"group": "Website", "label": "Manage lockers", "description": "See and change locker assignments, releases and reminders."},
    "locker_allowances": {"group": "Website", "label": "Give free locker allowances", "description": "Grant or remove paid-equivalent locker allowances."},
    "email": {"group": "Communications", "label": "Email & approval queue", "description": "Manage recipients, review campaigns and send mail to members."},
    "whatsapp": {"group": "Communications", "label": "WhatsApp administration", "description": "Link the account, manage groups and post messages to the community."},
    "opportunities": {"group": "Communications", "label": "Review opportunities", "description": "Access private submissions and approve or reject opportunities."},
}

_ADMIN = set(PERMISSIONS) - {"users", "locker_allowances"}
ROLES: dict[str, dict] = {
    "superuser": {
        "label": "Superuser", "blurb": "All permissions selected by default.",
        "capabilities": set(PERMISSIONS),
    },
    "admin": {
        "label": "Admin", "blurb": "All except access management and free locker grants.",
        "capabilities": _ADMIN,
    },
    "events": {
        "label": "Events", "blurb": "Manage events, bookings and sales sync.",
        "capabilities": {"events", "bookings", "sync"},
    },
    "industry": {
        "label": "Industry", "blurb": "Manage sponsors and industry events and bookings.",
        "capabilities": {"sponsors", "industry", "industry_bookings"},
    },
}
ROLE_NAMES = tuple(ROLES)


def capabilities(role: str | None) -> frozenset[str]:
    """Default selection for a role; never infer custom grants from the label."""
    return frozenset(ROLES.get(role, {}).get("capabilities", ()))


def effective_capabilities(role: str | None, permissions: list[str] | None = None) -> frozenset[str]:
    if not role:
        return frozenset()
    if permissions is None:
        return capabilities(role)
    # Fail closed for entries no longer recognised by the permission catalogue.
    return frozenset(p for p in permissions if p in PERMISSIONS)


def validate_permissions(permissions: list[str] | None) -> list[str] | None:
    """Validate *before* any account is created or changed."""
    if permissions is None:
        return None
    if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
        raise ValueError("Permissions must be an array of permission names")
    invalid = set(permissions) - PERMISSIONS.keys()
    if invalid:
        raise ValueError(f"Unknown permissions: {', '.join(sorted(invalid))}")
    if len(permissions) != len(set(permissions)):
        raise ValueError("Duplicate permissions are not allowed")
    return sorted(permissions)


def describe() -> list[dict]:
    return [
        {"id": name, "label": spec["label"], "blurb": spec["blurb"],
         "capabilities": sorted(spec["capabilities"])}
        for name, spec in ROLES.items()
    ]


def describe_permissions() -> list[dict]:
    return [{"id": name, **spec} for name, spec in PERMISSIONS.items()]
