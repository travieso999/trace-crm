"""Role-based permissions with per-user adjustments.

Each role has a default permission set. Admins can switch individual *adjustable* permissions on or off for a single
user (``users.permission_overrides``); everything else follows the role. Admins always hold every permission, so an
override can never lock the workspace owner out.

To add a role later: add it to ``ROLES`` in ``app.models.core`` (and the check constraint, via a migration), give it
an entry in ``ROLE_DEFAULTS`` and a label in ``ROLE_INFO``. Routes check permissions, not role names, so nothing else
needs to change.
"""

from app.models.core import User

# permission -> (label, description). Order is the order shown in Team Management.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "opportunities.view": ("View opportunities", "See every opportunity in the workspace."),
    "opportunities.create": ("Create opportunities", "Add new opportunities."),
    "opportunities.edit_own": ("Edit own and unassigned opportunities",
                               "Change details, stage, status, notes and tasks on opportunities they own or nobody owns."),
    "opportunities.edit_others": ("Edit teammates' opportunities",
                                  "Change details, stage, status, notes and tasks on opportunities owned by someone else."),
    "opportunities.assign_self": ("Take unassigned opportunities", "Assign unassigned opportunities to themselves."),
    "opportunities.reassign": ("Assign to anyone", "Assign or reassign any opportunity to any teammate, including bulk."),
    "opportunities.delete_any": ("Remove any opportunity",
                                 "Remove opportunities owned by others from the board (their own can always be removed)."),
    "outreach.access": ("Outreach", "Campaigns, sequences and templates, imported lists, the Unified Inbox and email accounts: "
                                    "create, launch and view. When off, the Outreach section is hidden and refused."),
    "inbox.access": ("Unified Inbox", "Read and reply to campaign replies (also needs Outreach)."),
    "insights.access": ("Insights & tools", "Analytics, AI Personalization and Website Audits."),
    "prospects.export": ("Export prospects", "Download the prospect list as a CSV file."),
    "users.manage": ("Manage team", "Create, edit, deactivate and delete user accounts."),
    "settings.manage": ("Manage settings", "Change workspace settings, pipeline stages, mailboxes and suppressions."),
}

# Only these can be switched per user; the rest follow the role.
ADJUSTABLE = (
    "opportunities.edit_others",
    "opportunities.assign_self",
    "opportunities.reassign",
    "opportunities.delete_any",
    "outreach.access",
    "inbox.access",
    "insights.access",
    "prospects.export",
)

ROLE_DEFAULTS: dict[str, frozenset[str]] = {
    "admin": frozenset(PERMISSIONS),
    "member": frozenset({
        "opportunities.view", "opportunities.create", "opportunities.edit_own", "opportunities.edit_others",
        "opportunities.assign_self", "inbox.access", "prospects.export",
        # not "outreach.access" / "insights.access": off for team members unless an admin switches them on
    }),
    # read-only (writes still need a member role); keeps the read access viewers always had
    "viewer": frozenset({"opportunities.view", "inbox.access", "outreach.access", "insights.access"}),
}

ROLE_INFO: dict[str, tuple[str, str]] = {
    "admin": ("Administrator", "Full access: team, settings, mailboxes and every opportunity."),
    "member": ("Team member", "Works opportunities and prospects; Outreach and Insights when switched on. "
                          "Cannot manage the team or settings."),
    "viewer": ("Viewer", "Read-only access."),
}


def effective(role: str, overrides: dict | None) -> frozenset[str]:
    base = set(ROLE_DEFAULTS.get(role, frozenset()))
    if role == "admin":
        return frozenset(base)
    for perm, on in (overrides or {}).items():
        if perm not in ADJUSTABLE or role == "viewer":
            continue
        (base.add if on else base.discard)(perm)
    return frozenset(base)


def user_permissions(user: User) -> frozenset[str]:
    return effective(user.role, user.permission_overrides)


def has(user: User, perm: str) -> bool:
    return perm in user_permissions(user)


def clean_overrides(role: str, overrides: dict | None) -> dict:
    """Keep only adjustable permissions that actually differ from the role default."""
    defaults = ROLE_DEFAULTS.get(role, frozenset())
    if role in ("admin", "viewer"):
        return {}
    return {p: bool(v) for p, v in (overrides or {}).items() if p in ADJUSTABLE and bool(v) != (p in defaults)}


def catalog() -> dict:
    return {
        "permissions": [{"key": k, "label": label, "description": desc, "adjustable": k in ADJUSTABLE}
                        for k, (label, desc) in PERMISSIONS.items()],
        "roles": [{"key": r, "label": ROLE_INFO[r][0], "description": ROLE_INFO[r][1],
                   "permissions": sorted(ROLE_DEFAULTS[r])} for r in ROLE_DEFAULTS],
    }
