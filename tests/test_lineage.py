"""Tests for the lineage graph: traversal, column lineage, cycles, exports."""

import pytest

from data_governance_lineage.lineage import LineageCycleError
from data_governance_lineage.models import GovernanceError

A = "shop.raw.orders"
B = "shop.cleaned.orders"
C = "shop.mart.daily_revenue"
D = "shop.mart.fraud_review"


def test_upstream_traversal_with_hop_distances(platform):
    assert platform.lineage.upstream(C) == {B: 1, A: 2}


def test_downstream_traversal_with_hop_distances(platform):
    assert platform.lineage.downstream(A) == {B: 1, C: 2, D: 2}


def test_depth_limited_traversal(platform):
    assert platform.lineage.downstream(A, depth=1) == {B: 1}
    assert platform.lineage.upstream(C, depth=1) == {B: 1}
    assert platform.lineage.downstream("unknown.dataset") == {}


def test_column_lineage_upstream_multi_hop(platform):
    # mart.daily_revenue.revenue <- cleaned.orders.amount <- raw.orders.amount
    result = platform.lineage.upstream_columns(C, "revenue")
    assert result == {(B, "amount"): 1, (A, "amount"): 2}


def test_column_lineage_downstream(platform):
    # raw.orders.email flows only into cleaned.orders.email (marts don't map it)
    result = platform.lineage.downstream_columns(A, "email")
    assert result == {(B, "email"): 1}


def test_column_lineage_unmapped_column_stops(platform):
    # cleaned.orders has no declared mapping for a made-up column
    assert platform.lineage.upstream_columns(B, "nonexistent") == {}


def test_impact_report_paths(platform):
    report = platform.lineage.impact_report(A)
    by_ds = {r["dataset"]: r for r in report}
    assert set(by_ds) == {B, C, D}
    assert by_ds[B]["hops"] == 1
    assert by_ds[C]["path"][0] == A and by_ds[C]["path"][-1] == C


def test_cycle_detection(platform):
    assert platform.lineage.detect_cycles() == []
    platform.lineage.add_edge(C, A, "backfill loop (bad)")
    cycles = platform.lineage.detect_cycles()
    assert len(cycles) == 1
    assert set(cycles[0]) == {A, B, C}


def test_execution_order_respects_edges(platform):
    order = platform.lineage.execution_order()
    assert order.index(A) < order.index(B) < order.index(C)
    assert order.index(B) < order.index(D)


def test_execution_order_raises_on_cycle(platform):
    platform.lineage.add_edge(C, A, "backfill loop (bad)")
    with pytest.raises(LineageCycleError):
        platform.lineage.execution_order()


def test_remove_edge_breaks_cycle(platform):
    platform.lineage.add_edge(C, A, "backfill loop (bad)")
    assert platform.lineage.remove_edge(C, A) is True
    assert platform.lineage.detect_cycles() == []
    assert platform.lineage.remove_edge(C, A) is False


def test_self_loop_rejected(platform):
    with pytest.raises(GovernanceError):
        platform.lineage.add_edge(A, A, "self")


def test_edge_requires_transform_description(platform):
    with pytest.raises(GovernanceError):
        platform.lineage.add_edge(A, "shop.other", "   ")


def test_dot_export(platform):
    dot = platform.lineage.to_dot()
    assert dot.startswith("digraph lineage {")
    assert f'"{A}" -> "{B}"' in dot
    assert dot.rstrip().endswith("}")


def test_mermaid_export(platform):
    mermaid = platform.lineage.to_mermaid()
    assert mermaid.startswith("graph TD")
    assert "-->" in mermaid
    assert A in mermaid and C in mermaid
