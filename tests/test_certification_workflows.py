"""Tests for the certification lifecycle, schema-change queue, and stewards."""

import pytest

from data_governance_lineage.models import (
    CertificationError,
    CertificationStatus,
    Column,
    InvalidTransition,
    Role,
)

DS = "shop.cleaned.orders"


def _checklist_ready(platform):
    """Satisfy every certification checklist item for DS."""
    platform.stewards.assign(DS, "steward-shop", Role.ADMIN)
    platform.catalog.set_retention(DS, 365)
    platform.privacy.complete_pii_review(DS)
    platform.quality.register_check(DS, "schema_valid")
    platform.quality.record_result(DS, "schema_valid", True)


def test_certify_blocked_until_checklist_complete(platform):
    platform.certification.submit_for_review(DS, submitted_by="team-shop")
    with pytest.raises(CertificationError) as exc:
        platform.certification.certify(DS, approver="gov-lead", role=Role.STEWARD)
    message = str(exc.value)
    assert "steward assigned" in message
    assert "retention policy set" in message
    assert "PII review completed" in message
    assert "quality check" in message
    assert platform.catalog.get(DS).certification is CertificationStatus.UNDER_REVIEW


def test_certify_succeeds_when_checklist_complete(platform):
    _checklist_ready(platform)
    platform.certification.submit_for_review(DS, submitted_by="team-shop")
    status = platform.certification.certify(DS, approver="gov-lead", role=Role.STEWARD)
    assert status is CertificationStatus.CERTIFIED
    assert platform.catalog.get(DS).certification is CertificationStatus.CERTIFIED


def test_certify_requires_steward_or_admin_role(platform):
    _checklist_ready(platform)
    platform.certification.submit_for_review(DS, submitted_by="team-shop")
    with pytest.raises(CertificationError):
        platform.certification.certify(DS, approver="ana", role=Role.ANALYST)


def test_certify_requires_under_review(platform):
    _checklist_ready(platform)
    with pytest.raises(InvalidTransition):
        platform.certification.certify(DS, approver="gov-lead", role=Role.ADMIN)


def test_submit_for_review_twice_rejected(platform):
    platform.certification.submit_for_review(DS, submitted_by="team-shop")
    with pytest.raises(InvalidTransition):
        platform.certification.submit_for_review(DS, submitted_by="team-shop")


def test_deprecate_lifecycle(platform):
    _checklist_ready(platform)
    platform.certification.submit_for_review(DS, submitted_by="team-shop")
    platform.certification.certify(DS, approver="gov-lead", role=Role.ADMIN)
    status = platform.certification.deprecate(DS, reason="replaced by shop.cleaned.orders_v2")
    assert status is CertificationStatus.DEPRECATED
    with pytest.raises(InvalidTransition):
        platform.certification.deprecate(DS, reason="again")
    with pytest.raises(ValueError):
        platform.certification.deprecate("shop.mart.daily_revenue", reason="  ")


def test_schema_change_approve_applies_columns(platform):
    new_schema = [
        Column("order_id", "string"),
        Column("email", "string", pii=True),
        Column("amount", "int"),
        Column("discount_cents", "int"),
    ]
    req = platform.schema_reviews.submit(DS, new_schema, requested_by="eng")
    assert req.status.value == "open"
    assert len(platform.schema_reviews.open_requests()) == 1

    approved = platform.schema_reviews.approve(req.request_id, reviewer="steward-shop", role=Role.STEWARD)
    assert approved.status.value == "approved"
    assert platform.catalog.get(DS).column_names() == ["order_id", "email", "amount", "discount_cents"]
    history = platform.catalog.schema_history(DS)
    assert len(history) == 2  # initial registration + approved change
    assert history[-1].change_note.startswith("schema change schema-")


def test_schema_change_reject_keeps_old_schema(platform):
    before = platform.catalog.get(DS).column_names()
    req = platform.schema_reviews.submit(
        DS, [Column("order_id", "string")], requested_by="eng"
    )
    rejected = platform.schema_reviews.reject(req.request_id, reviewer="steward-shop", reason="drops PII needed downstream")
    assert rejected.status.value == "rejected"
    assert platform.catalog.get(DS).column_names() == before


def test_schema_change_guards(platform):
    req = platform.schema_reviews.submit(DS, [Column("order_id", "string")], requested_by="eng")
    with pytest.raises(CertificationError):
        platform.schema_reviews.approve(req.request_id, reviewer="ana", role=Role.ANALYST)
    with pytest.raises(ValueError):
        platform.schema_reviews.reject(req.request_id, reviewer="stw", reason=" ")
    platform.schema_reviews.approve(req.request_id, reviewer="stw", role=Role.STEWARD)
    with pytest.raises(InvalidTransition):
        platform.schema_reviews.approve(req.request_id, reviewer="stw", role=Role.STEWARD)
    with pytest.raises(KeyError):
        platform.schema_reviews.approve("schema-9999", reviewer="stw", role=Role.STEWARD)
    with pytest.raises(ValueError):
        platform.schema_reviews.submit(DS, [], requested_by="eng")


def test_steward_assignment_requires_admin(platform):
    with pytest.raises(CertificationError):
        platform.stewards.assign(DS, "someone", Role.STEWARD)
    platform.stewards.assign(DS, "steward-shop", Role.ADMIN)
    assert platform.catalog.get(DS).steward == "steward-shop"
    by_domain = platform.stewards.stewards_for_domain("shop")
    assert by_domain["steward-shop"] == sorted(by_domain["steward-shop"])
    assert DS in by_domain["steward-shop"]
