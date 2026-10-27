"""Action-level committee permissions; roles are editable presets, not security boundaries.

Every action has a stable key, group and subgroup. Group selection is UI-only;
no group/wildcard grants are stored. NULL admin permissions use the current role
preset; explicit lists are exact permissions. Pre-2026 broad keys in existing
explicit lists are expanded *only while loading* for backwards compatibility.
Never create new broad permission grants.
"""
from __future__ import annotations

# group -> subgroup -> permission-key suffix and human-readable label.
# Keep names stable: they are persisted by user ID and checked by API routes.
GROUPS = {
    'Access & security': {
        'Committee access': {
            'access.view': 'View committee access',
            'access.grant': 'Grant or change permissions',
            'access.revoke': 'Remove committee access',
            'access.impersonate': 'Sign in as another account',
        },
        'Recovery links': {
            'passkeys.view': 'View recovery links',
            'passkeys.create': 'Create recovery links',
            'passkeys.revoke': 'Revoke recovery links',
        },
    },
    'Events': {
        'Event visibility & scope': {
            'events.view': 'View the event administration calendar',
            'events.scope.all': 'All events (scope)',
            'events.scope.industry': 'Industry events only (scope)',
        },
        'Event management': {
            'events.create': 'Create events',
            'events.edit': 'Edit event details',
            'events.photo_short_name.edit': 'Edit an event photo short name',
            'events.availability_badge.edit': 'Set the public What’s On availability label',
            'events.waitlist.manage': 'Enable or disable joining event waitlists',
            'events.publish': 'Publish or unpublish events',
            'events.delete': 'Delete events',
        },
        'Ticket types & prices': {
            'events.ticket_types.create': 'Add ticket types',
            'events.ticket_types.edit': 'Edit ticket types and prices',
            'events.ticket_types.delete': 'Delete ticket types',
            'events.ticket_types.product': 'Link Union products to event tickets',
        },
        'Bookings': {
            'events.bookings.view': 'View attendee bookings',
            'events.bookings.export': 'Export attendee CSV',
            'events.bookings.add': 'Add complimentary or manual tickets',
            'events.bookings.confirm': 'Confirm a booking as paid',
            'events.bookings.order_number.edit': 'Edit reservation order numbers',
            'events.bookings.cancel': 'Cancel another person’s booking',
            'events.bookings.remind': 'Send purchase reminders',
            'events.bookings.change_ticket': 'Change an attendee’s ticket type',
            'events.bookings.resend': 'Resend ticket confirmation emails',
            'events.bookings.waitlist': 'View booking waitlists',
            'events.bookings.sale_alerts': 'View ticket sale-alert subscribers',
        },
        'Door check-in': {
            'events.checkin.view': 'View door attendee lists',
            'events.checkin.scan': 'Check in via QR scan',
            'events.checkin.manual': 'Manually check in attendees',
            'events.checkin.override': 'Override invalid or unpaid tickets',
            'events.checkin.undo': 'Undo a check-in',
        },
        'Union sync': {
            'events.sync.union': 'Synchronise Union sales and members',
            'events.sync.reconcile': 'Reconcile booking payments',
        },
    },
    'Sponsors': {
        'Directory': {
            'sponsors.view': 'View sponsor administration',
            'sponsors.create': 'Add sponsors',
            'sponsors.edit': 'Edit sponsor details',
            'sponsors.delete': 'Delete sponsors',
            'sponsors.reorder': 'Reorder sponsors',
            'sponsors.package': 'Edit default sponsor packages',
        },
        'People & correspondence': {
            'sponsors.users.view': 'View sponsor contacts',
            'sponsors.users.grant': 'Link sponsor portal users',
            'sponsors.users.revoke': 'Remove sponsor portal users',
            'sponsors.inbox': 'View sponsor inbox',
            'sponsors.notes.reply': 'Reply to sponsor messages',
            'sponsors.requests.view': 'View sponsor requests',
            'sponsors.requests.edit': 'Approve or edit sponsor requests',
        },
        'Agreements & events': {
            'sponsors.terms.edit': 'Edit sponsor agreements',
            'sponsors.usage.add': 'Record sponsor allowance usage',
            'sponsors.usage.delete': 'Undo sponsor allowance usage',
            'sponsors.events.link': 'Link sponsors to events',
            'sponsors.events.unlink': 'Remove sponsor-event links',
            'sponsors.events.decline': 'Decline sponsor event interest',
        },
        'Sponsor portal on behalf of a sponsor': {
            'sponsors.portal.view': 'View sponsor portal',
            'sponsors.portal.jobs.create': 'Post sponsor job listings',
            'sponsors.portal.jobs.edit': 'Edit sponsor job listings',
            'sponsors.portal.events.claim': 'Claim sponsor event opportunities',
            'sponsors.portal.events.withdraw': 'Withdraw sponsor event interest',
            'sponsors.portal.requests.create': 'Submit sponsor requests',
            'sponsors.portal.marketing.upload': 'Upload sponsor marketing media',
            'sponsors.portal.marketing.create': 'Submit sponsor marketing requests',
            'sponsors.portal.marketing.withdraw': 'Withdraw sponsor marketing requests',
            'sponsors.portal.notes.view': 'View sponsor-side conversations',
            'sponsors.portal.notes.send': 'Send sponsor-side messages',
            'sponsors.portal.contacts.edit': 'Update sponsor contacts',
            'sponsors.portal.merch.upload': 'Upload sponsor merchandise',
            'sponsors.portal.merch.delete': 'Remove sponsor merchandise',
            'sponsors.portal.profile.logo': 'Change sponsor logo',
            'sponsors.portal.profile.edit': 'Edit sponsor portal profile',
        },
        'Sponsor portal on behalf of a sponsor': {
            'sponsors.portal.view': 'View sponsor portal',
            'sponsors.portal.jobs.create': 'Post sponsor job listings',
            'sponsors.portal.jobs.edit': 'Edit sponsor job listings',
            'sponsors.portal.events.claim': 'Claim sponsor event opportunities',
            'sponsors.portal.events.withdraw': 'Withdraw sponsor event interest',
            'sponsors.portal.requests.create': 'Submit sponsor requests',
            'sponsors.portal.marketing.upload': 'Upload sponsor marketing media',
            'sponsors.portal.marketing.create': 'Submit sponsor marketing requests',
            'sponsors.portal.marketing.withdraw': 'Withdraw sponsor marketing requests',
            'sponsors.portal.notes.view': 'View sponsor-side conversations',
            'sponsors.portal.notes.send': 'Send sponsor-side messages',
            'sponsors.portal.contacts.edit': 'Update sponsor contacts',
            'sponsors.portal.merch.upload': 'Upload sponsor merchandise',
            'sponsors.portal.merch.delete': 'Remove sponsor merchandise',
            'sponsors.portal.profile.logo': 'Change sponsor logo',
            'sponsors.portal.profile.edit': 'Edit sponsor portal profile',
        },
        'Marketing & outreach': {
            'sponsors.marketing.edit': 'Edit marketing requests',
            'sponsors.marketing.preview': 'Preview sponsor messages',
            'sponsors.marketing.send': 'Send sponsor marketing',
            'sponsors.marketing.posted': 'Mark sponsor marketing as posted',
            'sponsors.marketing.complete': 'Mark sponsor marketing complete',
            'sponsors.marketing.decline': 'Decline sponsor marketing',
            'sponsors.digest.view': 'Preview roles digest',
            'sponsors.digest.send': 'Send roles digest',
        },
    },
    'Website': {
        'Resources': {
            'resources.drafts': 'View unpublished resources',
            'resources.pages.create': 'Create resource pages',
            'resources.pages.edit': 'Edit resource pages',
            'resources.pages.delete': 'Delete resource pages',
            'resources.entries.create': 'Add resource entries',
            'resources.entries.edit': 'Edit resource entries',
            'resources.entries.delete': 'Delete resource entries',
            'resources.entries.move': 'Reorder resource entries',
            'resources.import': 'Import resources',
        },
        'Photo print studio': {
            'photos.print': 'Record an event photo print',
            'photos.view': 'View photo print sets',
            'photos.upload': 'Upload photos for prints',
            'photos.create': 'Create photo print drafts',
            'photos.publish': 'Publish photo print sets and links',
            'photos.unpublish': 'Unpublish photo print sets',
            'photos.delete': 'Delete photo print sets and images',
        },
        'Shared uploads': {
            'files.view': 'Browse uploaded files',
            'files.upload': 'Upload files',
            'files.delete': 'Delete uploaded files',
        },
        'Committee page': {
            'committee.view': 'View committee administration',
            'committee.intro': 'Edit committee page introduction',
            'committee.profiles': 'Edit other committee cards',
            'committee.sync': 'Synchronise committee roster',
        },
        'Union products': {
            'products.view': 'Browse Union product mappings',
            'products.link': 'Create Union product links',
            'products.unlink': 'Delete Union product links',
        },
    },
    'Lockers': {
        'Inventory & holders': {
            'lockers.inventory.view': 'View locker inventory',
            'lockers.inventory.edit': 'Enable or disable lockers',
            'lockers.holders.view': 'View locker holders',
            'lockers.bookings.view': 'View locker bookings',
            'lockers.bookings.release': 'Release another person’s locker booking',
        },
        'Year-end management': {
            'lockers.clearout.notify': 'Send locker clear-out reminders',
            'lockers.year.release': 'Release lockers for a new year',
        },
        'Free allowances': {
            'lockers.allowances.view': 'View free locker allowances',
            'lockers.allowances.grant': 'Give or change free locker allowances',
            'lockers.allowances.revoke': 'Remove free locker allowances',
        },
    },
    'Communications': {
        'Email composition': {
            'email.audiences': 'View email audiences',
            'email.preview': 'Preview email',
            'email.send': 'Send member email',
            'email.announce_event': 'Announce an event',
            'email.announce_now': 'Trigger ticket sale announcements',
            'email.test_recipients.view': 'View test email recipients',
            'email.test_recipients.add': 'Add test email recipients',
            'email.test_recipients.remove': 'Remove test email recipients',
            'email.campaigns.view': 'View sent email campaigns',
            'email.optouts.view': 'View email opt-outs',
        },
        'Approval queue': {
            'email.queue.view': 'View pending email approvals',
            'email.queue.edit': 'Edit pending campaigns',
            'email.queue.test': 'Send test campaign emails',
            'email.queue.send': 'Approve and send queued emails',
            'email.queue.schedule': 'Approve and schedule queued emails',
            'email.queue.unschedule': 'Unschedule queued emails',
            'email.queue.dismiss': 'Reject queued emails',
        },
        'WhatsApp': {
            'whatsapp.view': 'View WhatsApp dashboard',
            'whatsapp.groups': 'View available WhatsApp groups',
            'whatsapp.link': 'Link society WhatsApp account',
            'whatsapp.unlink': 'Unlink society WhatsApp account',
            'whatsapp.post': 'Post messages to WhatsApp groups',
            'whatsapp.render': 'Preview WhatsApp messages',
        },
        'Opportunities': {
            'opportunities.view': 'View private opportunity submissions',
            'opportunities.approve': 'Approve opportunities',
            'opportunities.reject': 'Reject submissions or proposed edits',
            'opportunities.unpublish': 'Take down approved opportunities',
            'opportunities.return_for_changes': 'Take down and send approved opportunities back for editing',
        },
    },
}

PERMISSIONS: dict[str, dict[str, str]] = {
    name: {'group': group, 'subgroup': subgroup, 'label': label,
           'description': f'{label}.'}
    for group, children in GROUPS.items()
    for subgroup, actions in children.items()
    for name, label in actions.items()
}

EVENT_EDIT_ACTIONS = {p for p in PERMISSIONS if p.startswith('events.') and not
                      (p.startswith(('events.bookings.', 'events.checkin.', 'events.sync.')) or p.startswith('events.scope.'))}
BOOKING_ACTIONS = {p for p in PERMISSIONS if p.startswith('events.bookings.')}
CHECKIN_ACTIONS = {p for p in PERMISSIONS if p.startswith('events.checkin.')}
EVENT_ACTIONS = EVENT_EDIT_ACTIONS | BOOKING_ACTIONS | CHECKIN_ACTIONS

# Existing explicit overrides can contain these pre-upgrade keys. Translate
# every key into individual grants and the necessary event scope. This map is
# ONLY for decoding old saved overrides; it is never offered in the UI.
LEGACY_GRANTS = {
    'users': {p for p in PERMISSIONS if p.startswith('access.')},
    'passkey_links': {p for p in PERMISSIONS if p.startswith('passkeys.')},
    'events': EVENT_EDIT_ACTIONS | {'events.scope.all'},
    'industry': EVENT_EDIT_ACTIONS | {'events.scope.industry'},
    'bookings': BOOKING_ACTIONS | {'events.scope.all'},
    'industry_bookings': BOOKING_ACTIONS | {'events.scope.industry'},
    'sync': {p for p in PERMISSIONS if p.startswith('events.sync.')},
    'sponsors': {p for p in PERMISSIONS if p.startswith('sponsors.')},
    'resources': {p for p in PERMISSIONS if p.startswith('resources.')},
    'files': {p for p in PERMISSIONS if p.startswith('files.')},
    'committee': {p for p in PERMISSIONS if p.startswith('committee.')},
    'product_links': {p for p in PERMISSIONS if p.startswith('products.')},
    'lockers': {p for p in PERMISSIONS if p.startswith('lockers.') and not p.startswith('lockers.allowances.')},
    'locker_allowances': {p for p in PERMISSIONS if p.startswith('lockers.allowances.')},
    'email': {p for p in PERMISSIONS if p.startswith('email.')},
    'whatsapp': {p for p in PERMISSIONS if p.startswith('whatsapp.')},
    'opportunities': {p for p in PERMISSIONS if p.startswith('opportunities.')},
}
# Old committee-only check-in did not need a role. Existing roles receive door
# access by default; custom permission holders can explicitly revoke it.
_ADMIN = set(PERMISSIONS) - {'access.grant', 'access.revoke', 'access.impersonate'} - LEGACY_GRANTS['locker_allowances']
ROLES = {
    'superuser': {'label': 'Superuser', 'blurb': 'All actions selected by default.', 'capabilities': set(PERMISSIONS)},
    'admin': {'label': 'Admin', 'blurb': 'Read-only access list, with all other actions except access management and complimentary locker allowances.', 'capabilities': _ADMIN},
    'events': {'label': 'Events', 'blurb': 'All event and booking actions, including check-in and sales sync.', 'capabilities': EVENT_ACTIONS | {'events.scope.all'} | LEGACY_GRANTS['sync']},
    'industry': {'label': 'Industry', 'blurb': 'All sponsor actions and industry-scoped event, booking and check-in actions.', 'capabilities': LEGACY_GRANTS['sponsors'] | EVENT_ACTIONS | {'events.scope.industry'}},
}
ROLE_NAMES = tuple(ROLES)


def capabilities(role: str | None) -> frozenset[str]:
    return frozenset(ROLES.get(role, {}).get('capabilities', ()))


def effective_capabilities(role: str | None, permissions: list[str] | None = None) -> frozenset[str]:
    if not role:
        return frozenset()
    if permissions is None:
        return capabilities(role)
    result: set[str] = set()
    for p in permissions:
        if p in PERMISSIONS:
            result.add(p)
        elif p in LEGACY_GRANTS:
            result.update(LEGACY_GRANTS[p])
    return frozenset(result)


def validate_permissions(permissions: list[str] | None) -> list[str] | None:
    if permissions is None:
        return None
    if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
        raise ValueError('Permissions must be an array of permission names')
    invalid = set(permissions) - PERMISSIONS.keys()
    if invalid:
        raise ValueError(f"Unknown permissions: {', '.join(sorted(invalid))}")
    if len(permissions) != len(set(permissions)):
        raise ValueError('Duplicate permissions are not allowed')
    return sorted(permissions)


def describe() -> list[dict]:
    return [{'id': name, 'label': spec['label'], 'blurb': spec['blurb'],
             'capabilities': sorted(spec['capabilities'])} for name, spec in ROLES.items()]


def describe_permissions() -> list[dict]:
    return [{'id': name, **spec} for name, spec in PERMISSIONS.items()]


def matches_category(permissions: frozenset[str] | set[str], prefix: str) -> bool:
    return any(p.startswith(prefix + '.') for p in permissions)
