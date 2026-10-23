"""Tests for PII tagging/masking and subject access/erasure handling."""

import pytest

from data_governance_lineage.models import Classification, ErasureStatus, Role

CONF_DS = "shop.cleaned.orders"          # CONFIDENTIAL
RESTRICTED_DS = "shop.mart.fraud_review"  # RESTRICTED
PUBLIC_DS = "shop.mart.daily_revenue"      # INTERNAL


def _seed_subjects(platform):
    platform.privacy.add_subject_records(
        CONF_DS,
        [
            {"subject_id": "SUBJ-A", "order_id": "O-1", "email": "alice@example.com", "phone": "+1-415-555-0101", "amount": 500},
            {"subject_id": "SUBJ-B", "order_id": "O-2", "email": "bob@example.com", "phone": "+1-415-555-0102", "amount": 700},
        ],
    )
    platform.privacy.add_subject_records(
        RESTRICTED_DS,
        [
            {"subject_id": "SUBJ-A", "order_id": "O-1", "email": "alice@example.com", "phone": "+1-415-555-0101", "amount": 500},
        ],
    )


def test_tag_and_untag_pii(platform):
    assert platform.catalog.get(CONF_DS).pii_columns() == ["email"]
    tagged = platform.privacy.tag_pii_columns(CONF_DS, ["amount"])
    assert tagged == ["amount"]
    assert set(platform.catalog.get(CONF_DS).pii_columns()) == {"email", "amount"}
    platform.privacy.untag_pii_columns(CONF_DS, ["amount"])
    assert platform.catalog.get(CONF_DS).pii_columns() == ["email"]
    with pytest.raises(KeyError):
        platform.privacy.tag_pii_columns(CONF_DS, ["nope"])


def test_masking_rules():
    from data_governance_lineage.privacy import mask_email, mask_phone, mask_generic

    masked_email = mask_email("alice@example.com")
    assert masked_email.endswith("@example.com") and "alice" not in masked_email
    assert mask_phone("+1-415-555-0101").endswith("0101")
    assert "***" in mask_phone("+1-415-555-0101")
    assert mask_generic("anything") == "***REDACTED***"
    assert mask_email("not-an-email") == "***REDACTED***"


def test_apply_masking_by_role(platform):
    from data_governance_lineage.privacy import apply_masking

    record = {"order_id": "O-1", "email": "alice@example.com", "phone": "+1-415-555-0101", "amount": 500}
    analyst_view = apply_masking(record, CONF_DS, platform.catalog, Role.ANALYST)
    assert analyst_view["order_id"] == "O-1"  # non-PII untouched
    assert analyst_view["amount"] == 500
    assert "alice" not in analyst_view["email"]
    assert analyst_view["phone"].endswith("0101")

    steward_view = apply_masking(record, CONF_DS, platform.catalog, Role.STEWARD)
    assert steward_view == record  # stewards see raw values

    internal_view = apply_masking(record, PUBLIC_DS, platform.catalog, Role.EXTERNAL)
    assert internal_view == record  # INTERNAL passes through


def test_access_request_manifest(platform):
    _seed_subjects(platform)
    manifest = platform.privacy.access_request("SUBJ-A", Role.ANALYST)
    assert manifest["subject_id"] == "SUBJ-A"
    datasets = {d["dataset"]: d for d in manifest["datasets"]}
    assert set(datasets) == {CONF_DS, RESTRICTED_DS}
    # CONFIDENTIAL values are visible to an analyst in this demo store? No:
    # CONFIDENTIAL withholds raw values regardless of role in the manifest.
    assert datasets[CONF_DS]["record_count"] == 1
    assert datasets[RESTRICTED_DS]["values"][0]["_withheld"].startswith("values withheld")
    # another subject's records never leak into this manifest
    assert platform.privacy.access_request("SUBJ-B", Role.ANALYST)["datasets"][0]["record_count"] == 1


def test_erasure_removes_records_and_writes_tombstones(platform):
    _seed_subjects(platform)
    req = platform.privacy.request_erasure("SUBJ-A", requested_by="privacy-officer")
    assert req.status is ErasureStatus.OPEN
    done = platform.privacy.execute_erasure(req.request_id)
    assert done.status is ErasureStatus.COMPLETED
    assert done.per_dataset_counts == {CONF_DS: 1, RESTRICTED_DS: 1}
    assert platform.privacy.remaining_records("SUBJ-A") == 0

    tombstones = platform.privacy.tombstones("SUBJ-A")
    assert len(tombstones) == 2
    assert {t.dataset for t in tombstones} == {CONF_DS, RESTRICTED_DS}
    # tombstones prove erasure (counts + timestamp) without retaining any values
    assert all(t.erased_count == 1 for t in tombstones)
    assert not any(hasattr(t, "fields") for t in tombstones)

    # other subject untouched
    assert platform.privacy.remaining_records("SUBJ-B") == 1
    # idempotent re-execution
    again = platform.privacy.execute_erasure(req.request_id)
    assert again.status is ErasureStatus.COMPLETED


def test_erasure_unknown_request(platform):
    with pytest.raises(KeyError):
        platform.privacy.execute_erasure("erasure-9999")
    with pytest.raises(KeyError):
        platform.privacy.get_erasure_request("erasure-9999")


def test_add_records_requires_subject_id(platform):
    with pytest.raises(ValueError):
        platform.privacy.add_subject_records(CONF_DS, [{"order_id": "O-9"}])


def test_demo_subjects_have_tombstone_accounting(demo_platform):
    req = demo_platform.privacy.request_erasure("SUBJ-001", requested_by="privacy-officer")
    done = demo_platform.privacy.execute_erasure(req.request_id)
    assert done.status is ErasureStatus.COMPLETED
    assert sum(done.per_dataset_counts.values()) == 2
    assert demo_platform.privacy.remaining_records("SUBJ-001") == 0
