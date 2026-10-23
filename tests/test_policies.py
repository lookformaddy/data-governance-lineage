"""Tests for access-policy decisions, the audit trail, and break-glass."""

from datetime import timedelta

import pytest

from data_governance_lineage.models import Action, Decision, Role, utcnow

PUBLIC_DS = "shop.mart.daily_revenue"      # INTERNAL
CONF_DS = "shop.cleaned.orders"            # CONFIDENTIAL
RESTRICTED_DS = "shop.mart.fraud_review"   # RESTRICTED


def _eval(platform, actor, role, dataset_name, action):
    return platform.policies.evaluate(
        actor, role, platform.catalog.get(dataset_name), action
    )


def test_analyst_read_clearance_boundary(platform):
    assert _eval(platform, "ana", Role.ANALYST, PUBLIC_DS, Action.READ).decision is Decision.ALLOW
    denied = _eval(platform, "ana", Role.ANALYST, CONF_DS, Action.READ)
    assert denied.decision is Decision.DENY
    assert "CONFIDENTIAL" in denied.reason


def test_analyst_is_read_only(platform):
    denied = _eval(platform, "ana", Role.ANALYST, PUBLIC_DS, Action.WRITE)
    assert denied.decision is Decision.DENY
    assert "may not perform" in denied.reason


def test_engineer_write_confidential_allowed(platform):
    assert _eval(platform, "eng", Role.DATA_ENGINEER, CONF_DS, Action.WRITE).decision is Decision.ALLOW
    denied = _eval(platform, "eng", Role.DATA_ENGINEER, RESTRICTED_DS, Action.READ)
    assert denied.decision is Decision.DENY


def test_admin_can_delete_restricted(platform):
    assert _eval(platform, "root", Role.ADMIN, RESTRICTED_DS, Action.DELETE).decision is Decision.ALLOW


def test_engineer_cannot_delete(platform):
    denied = _eval(platform, "eng", Role.DATA_ENGINEER, CONF_DS, Action.DELETE)
    assert denied.decision is Decision.DENY


def test_steward_can_certify(platform):
    assert _eval(platform, "stw", Role.STEWARD, PUBLIC_DS, Action.CERTIFY).decision is Decision.ALLOW


def test_external_public_only(platform):
    denied = _eval(platform, "ext", Role.EXTERNAL, PUBLIC_DS, Action.READ)
    assert denied.decision is Decision.DENY  # PUBLIC_DS is INTERNAL, above EXTERNAL clearance


def test_every_evaluation_is_audited(platform):
    _eval(platform, "ana", Role.ANALYST, PUBLIC_DS, Action.READ)
    _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    events = platform.policies.audit_log()
    assert len(events) == 2
    decisions = {e.decision for e in events}
    assert decisions == {Decision.ALLOW, Decision.DENY}


def test_audit_log_filters(platform):
    _eval(platform, "ana", Role.ANALYST, PUBLIC_DS, Action.READ)
    _eval(platform, "bob", Role.ANALYST, RESTRICTED_DS, Action.READ)
    assert len(platform.policies.audit_log(actor="ana")) == 1
    assert len(platform.policies.audit_log(decision=Decision.DENY)) == 1
    assert len(platform.policies.audit_log(limit=1)) == 1
    # newest first
    assert platform.policies.audit_log(limit=1)[0].actor == "bob"


def test_break_glass_allows_otherwise_denied_read(platform):
    denied = _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    assert denied.decision is Decision.DENY
    grant = platform.policies.request_break_glass(
        "ana", RESTRICTED_DS, "INC-1: investigate fraud spike", ttl_seconds=1800
    )
    assert grant.grant_id.startswith("bg-")
    allowed = _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    assert allowed.decision is Decision.ALLOW
    assert allowed.break_glass is True
    assert allowed.grant_id == grant.grant_id


def test_break_glass_cannot_widen_delete(platform):
    platform.policies.request_break_glass("ana", RESTRICTED_DS, "INC-2: urgent", ttl_seconds=600)
    denied = _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.DELETE)
    assert denied.decision is Decision.DENY


def test_break_glass_requires_justification(platform):
    with pytest.raises(ValueError):
        platform.policies.request_break_glass("ana", RESTRICTED_DS, "   ")


def test_break_glass_ttl_capped(platform):
    with pytest.raises(ValueError):
        platform.policies.request_break_glass("ana", RESTRICTED_DS, "INC-3", ttl_seconds=24 * 3600)


def test_expired_grant_denies(platform):
    grant = platform.policies.request_break_glass("ana", RESTRICTED_DS, "INC-4", ttl_seconds=60)
    grant.expires_at = utcnow() - timedelta(seconds=1)  # simulate expiry
    denied = _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    assert denied.decision is Decision.DENY
    assert platform.policies.active_grants() == []


def test_revoked_grant_denies(platform):
    grant = platform.policies.request_break_glass("ana", RESTRICTED_DS, "INC-5", ttl_seconds=3600)
    platform.policies.revoke_break_glass(grant.grant_id, revoked_by="secops")
    denied = _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    assert denied.decision is Decision.DENY
    with pytest.raises(KeyError):
        platform.policies.revoke_break_glass("bg-9999", revoked_by="secops")


def test_break_glass_events_flagged_in_audit(platform):
    platform.policies.request_break_glass("ana", RESTRICTED_DS, "INC-6", ttl_seconds=600)
    _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    flagged = platform.policies.audit_log(break_glass_only=True)
    assert len(flagged) == 2  # the grant event + the granted evaluation
    assert all(e.break_glass for e in flagged)


def test_audit_summary_counts(platform):
    _eval(platform, "ana", Role.ANALYST, PUBLIC_DS, Action.READ)
    _eval(platform, "ana", Role.ANALYST, RESTRICTED_DS, Action.READ)
    summary = platform.policies.audit_summary()
    assert summary["total_events"] == 2
    assert summary["allowed"] == 1
    assert summary["denied"] == 1
