"""Tests for lineage-aware quality: taint propagation and impact reports."""

import pytest

from data_governance_lineage.models import DatasetNotFound

A = "shop.raw.orders"
B = "shop.cleaned.orders"
C = "shop.mart.daily_revenue"
D = "shop.mart.fraud_review"


def _all_green(platform):
    for ds in (A, B, C, D):
        platform.quality.register_check(ds, "freshness_sla")
        platform.quality.record_result(ds, "freshness_sla", True)


def test_no_taint_when_all_checks_pass(platform):
    _all_green(platform)
    assert platform.quality.tainted_datasets() == []
    assert platform.quality.is_tainted(C) is False


def test_upstream_failure_taints_downstream(platform):
    _all_green(platform)
    platform.quality.record_result(A, "freshness_sla", False, "source delayed 6h")
    assert platform.quality.tainted_datasets() == sorted([A, B, C, D])
    assert platform.quality.taint_causes(C) == ["freshness_sla"]


def test_failure_does_not_taint_upstream(platform):
    _all_green(platform)
    platform.quality.record_result(B, "freshness_sla", False)
    assert platform.quality.tainted_datasets() == [B, C, D]
    assert platform.quality.is_tainted(A) is False


def test_resolve_clears_taint(platform):
    _all_green(platform)
    platform.quality.record_result(A, "freshness_sla", False)
    assert platform.quality.is_tainted(C) is True
    platform.quality.resolve(A, "freshness_sla", "backfill complete")
    assert platform.quality.tainted_datasets() == []
    check = platform.quality.checks_for(A)[0]
    assert check.last_passed is True
    assert check.consecutive_failures == 0


def test_consecutive_failures_counted(platform):
    platform.quality.register_check(A, "freshness_sla")
    platform.quality.record_result(A, "freshness_sla", False)
    platform.quality.record_result(A, "freshness_sla", False)
    assert platform.quality.checks_for(A)[0].consecutive_failures == 2


def test_impact_report_lists_stakeholders(platform):
    _all_green(platform)
    platform.quality.record_result(A, "freshness_sla", False)
    platform.catalog.set_steward(C, "steward-shop")
    report = platform.quality.impact_report(A)
    assert report["failed_dataset"] == A
    assert report["failing_checks"] == ["freshness_sla"]
    assert report["impacted_count"] == 3
    by_ds = {i["dataset"]: i for i in report["impacted"]}
    assert by_ds[C]["hops"] == 2
    assert by_ds[C]["owner"] == "team-shop"
    assert by_ds[C]["steward"] == "steward-shop"
    assert by_ds[C]["classification"] == "INTERNAL"
    assert by_ds[C]["path"][0] == A


def test_impact_report_no_downstream(platform):
    _all_green(platform)
    report = platform.quality.impact_report(D)
    assert report["impacted_count"] == 0


def test_quality_summary(platform):
    _all_green(platform)
    platform.quality.record_result(A, "freshness_sla", False)
    summary = platform.quality.quality_summary()
    assert summary["checks"] == 4
    assert summary["passing"] == 3
    assert summary["failing"] == 1
    assert summary["never_run"] == 0
    assert summary["tainted_datasets"] == sorted([A, B, C, D])


def test_register_check_validates_dataset(platform):
    with pytest.raises(DatasetNotFound):
        platform.quality.register_check("nope.unknown", "freshness_sla")
    with pytest.raises(KeyError):
        platform.quality.record_result(A, "missing_check", True)


def test_duplicate_check_rejected(platform):
    platform.quality.register_check(A, "freshness_sla")
    with pytest.raises(ValueError):
        platform.quality.register_check(A, "freshness_sla")


def test_all_passing_requires_registered_checks(platform):
    assert platform.quality.all_passing(A) is False
    platform.quality.register_check(A, "freshness_sla")
    assert platform.quality.all_passing(A) is False  # never run
    platform.quality.record_result(A, "freshness_sla", True)
    assert platform.quality.all_passing(A) is True
