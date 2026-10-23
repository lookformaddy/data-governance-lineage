"""Access policies: role x classification enforcement, audit trail, break-glass.

Design
------
- Every evaluation goes through ``PolicyEngine.evaluate`` and is appended to an
  immutable audit log — there is no silent access path.
- The matrix combines two dimensions:
  1. *Clearance*: each role has a maximum classification it may touch.
  2. *Action scoping*: analysts are read-only, only admins delete, only
     stewards/admins certify, etc.
- Break-glass grants temporary, justified, expiring access for incidents.
  They are evaluated first, always logged with ``break_glass=True``, and a
  missing/expired/revoked grant is a hard deny — no fallthrough.

Trade-off note: this is policy *evaluation*, not enforcement at the storage
layer. In production the same decision function would back a policy sidecar /
query-rewrite layer (e.g. view-based masking), because application-level
checks alone cannot stop direct warehouse access.
"""

from __future__ import annotations

from dataclasses import dataclass

from data_governance_lineage.models import (
    Action,
    AuditEvent,
    BreakGlassGrant,
    Classification,
    Dataset,
    Decision,
    Role,
    utcnow,
)

# Maximum classification each role may access (clearance ceiling).
_ROLE_CLEARANCE: dict[Role, Classification] = {
    Role.ADMIN: Classification.RESTRICTED,
    Role.STEWARD: Classification.RESTRICTED,
    Role.DATA_ENGINEER: Classification.CONFIDENTIAL,
    Role.SERVICE_ACCOUNT: Classification.CONFIDENTIAL,
    Role.ANALYST: Classification.INTERNAL,
    Role.EXTERNAL: Classification.PUBLIC,
}

# Roles permitted to perform each action (subject to clearance as well).
_ACTION_ROLES: dict[Action, set[Role]] = {
    Action.READ: set(Role),
    Action.WRITE: {Role.ADMIN, Role.STEWARD, Role.DATA_ENGINEER, Role.SERVICE_ACCOUNT},
    Action.DELETE: {Role.ADMIN},
    Action.CERTIFY: {Role.ADMIN, Role.STEWARD},
    Action.MANAGE_POLICY: {Role.ADMIN},
    Action.ASSIGN_STEWARD: {Role.ADMIN},
}

# Break-glass may only widen READ/WRITE; destructive or governance actions
# can never be break-glassed (prevents privilege-escalation-by-incident).
_BREAK_GLASS_ACTIONS = {Action.READ, Action.WRITE}

MAX_BREAK_GLASS_TTL_SECONDS = 4 * 3600  # 4 hours


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str
    break_glass: bool = False
    grant_id: str | None = None


class PolicyEngine:
    def __init__(self) -> None:
        self._audit: list[AuditEvent] = []
        self._grants: dict[str, BreakGlassGrant] = {}
        self._event_seq = 0
        self._grant_seq = 0

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        actor: str,
        role: Role,
        dataset: Dataset,
        action: Action,
        purpose: str = "",
    ) -> PolicyDecision:
        # 1. Break-glass first: valid grant for this actor+dataset+action wins.
        grant = self._valid_grant(actor, dataset.name, action)
        if grant is not None:
            return self._log(
                actor, action, dataset.name,
                PolicyDecision(
                    Decision.ALLOW,
                    f"break-glass grant {grant.grant_id} (justification: {grant.justification})",
                    break_glass=True,
                    grant_id=grant.grant_id,
                ),
                purpose,
            )

        # 2. Action scoping.
        if role not in _ACTION_ROLES[action]:
            return self._log(
                actor, action, dataset.name,
                PolicyDecision(Decision.DENY, f"role {role.value} may not perform {action.value}"),
                purpose,
            )

        # 3. Clearance check.
        clearance = _ROLE_CLEARANCE[role]
        if dataset.classification.value > clearance.value:
            return self._log(
                actor, action, dataset.name,
                PolicyDecision(
                    Decision.DENY,
                    f"classification {dataset.classification.name} exceeds "
                    f"{role.value} clearance ({clearance.name})",
                ),
                purpose,
            )

        return self._log(
            actor, action, dataset.name,
            PolicyDecision(Decision.ALLOW, f"{role.value} cleared for {action.value} on {dataset.classification.name}"),
            purpose,
        )

    def _valid_grant(self, actor: str, dataset: str, action: Action) -> BreakGlassGrant | None:
        if action not in _BREAK_GLASS_ACTIONS:
            return None
        for grant in self._grants.values():
            if grant.actor == actor and grant.dataset == dataset and grant.is_valid():
                return grant
        return None

    def _log(
        self, actor: str, action: Action, dataset: str, decision: PolicyDecision, purpose: str
    ) -> PolicyDecision:
        self._event_seq += 1
        self._audit.append(
            AuditEvent(
                event_id=self._event_seq,
                timestamp=utcnow(),
                actor=actor,
                action=action.value,
                dataset=dataset,
                decision=decision.decision,
                reason=decision.reason,
                break_glass=decision.break_glass,
                metadata={"purpose": purpose} if purpose else {},
            )
        )
        return decision

    # ------------------------------------------------------------------ #
    # Break-glass
    # ------------------------------------------------------------------ #
    def request_break_glass(
        self,
        actor: str,
        dataset: str,
        justification: str,
        ttl_seconds: int = 3600,
    ) -> BreakGlassGrant:
        if not justification or not justification.strip():
            raise ValueError("Break-glass requires a written justification")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if ttl_seconds > MAX_BREAK_GLASS_TTL_SECONDS:
            raise ValueError(
                f"ttl_seconds capped at {MAX_BREAK_GLASS_TTL_SECONDS} (4h) — request a shorter window"
            )
        self._grant_seq += 1
        now = utcnow()
        grant = BreakGlassGrant(
            grant_id=f"bg-{self._grant_seq:04d}",
            actor=actor,
            dataset=dataset,
            justification=justification.strip(),
            granted_at=now,
            expires_at=now + _timedelta_seconds(ttl_seconds),
        )
        self._grants[grant.grant_id] = grant
        self._event_seq += 1
        self._audit.append(
            AuditEvent(
                event_id=self._event_seq,
                timestamp=now,
                actor=actor,
                action="break_glass_grant",
                dataset=dataset,
                decision=Decision.ALLOW,
                reason=f"break-glass granted: {grant.justification}",
                break_glass=True,
                metadata={"grant_id": grant.grant_id, "ttl_seconds": ttl_seconds},
            )
        )
        return grant

    def revoke_break_glass(self, grant_id: str, revoked_by: str) -> BreakGlassGrant:
        grant = self._grants.get(grant_id)
        if grant is None:
            raise KeyError(f"Unknown break-glass grant: {grant_id}")
        grant.revoked = True
        self._event_seq += 1
        self._audit.append(
            AuditEvent(
                event_id=self._event_seq,
                timestamp=utcnow(),
                actor=revoked_by,
                action="break_glass_revoke",
                dataset=grant.dataset,
                decision=Decision.ALLOW,
                reason=f"break-glass grant {grant_id} revoked",
                metadata={"grant_id": grant_id, "grantee": grant.actor},
            )
        )
        return grant

    def active_grants(self) -> list[BreakGlassGrant]:
        return [g for g in self._grants.values() if g.is_valid()]

    # ------------------------------------------------------------------ #
    # Audit trail (append-only reads)
    # ------------------------------------------------------------------ #
    def audit_log(
        self,
        *,
        actor: str | None = None,
        dataset: str | None = None,
        decision: Decision | None = None,
        break_glass_only: bool = False,
        limit: int | None = None,
    ) -> list[AuditEvent]:
        events = self._audit
        if actor is not None:
            events = [e for e in events if e.actor == actor]
        if dataset is not None:
            events = [e for e in events if e.dataset == dataset]
        if decision is not None:
            events = [e for e in events if e.decision is decision]
        if break_glass_only:
            events = [e for e in events if e.break_glass]
        events = list(reversed(events))  # newest first
        return events[:limit] if limit is not None else events

    def audit_summary(self) -> dict:
        allowed = sum(1 for e in self._audit if e.decision is Decision.ALLOW)
        denied = sum(1 for e in self._audit if e.decision is Decision.DENY)
        return {
            "total_events": len(self._audit),
            "allowed": allowed,
            "denied": denied,
            "break_glass_events": sum(1 for e in self._audit if e.break_glass),
            "active_grants": len(self.active_grants()),
        }


def _timedelta_seconds(seconds: int):  # small helper to keep the stdlib import local
    from datetime import timedelta

    return timedelta(seconds=seconds)
