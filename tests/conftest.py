"""Shared fixtures: a small deterministic estate plus the full demo estate."""

from __future__ import annotations

import pytest

from data_governance_lineage.demo import build_demo_platform
from data_governance_lineage.models import (
    Classification,
    Column,
    Dataset,
    DatasetKind,
)
from data_governance_lineage.platform import GovernancePlatform


def make_small_platform() -> GovernancePlatform:
    """A -> B -> {C, D} lineage chain with column mappings, all synthetic."""
    p = GovernancePlatform()

    def register(name: str, classification: Classification) -> None:
        p.catalog.register(
            Dataset(
                name=name,
                kind=DatasetKind.TABLE,
                domain="shop",
                owner="team-shop",
                classification=classification,
                schema=[
                    Column("order_id", "string"),
                    Column("email", "string", pii=True),
                    Column("amount", "int"),
                ],
            )
        )

    register("shop.raw.orders", Classification.CONFIDENTIAL)
    register("shop.cleaned.orders", Classification.CONFIDENTIAL)
    register("shop.mart.daily_revenue", Classification.INTERNAL)
    register("shop.mart.fraud_review", Classification.RESTRICTED)

    p.lineage.add_edge(
        "shop.raw.orders",
        "shop.cleaned.orders",
        "clean + dedupe",
        {"order_id": "order_id", "email": "email", "amount": "amount"},
    )
    p.lineage.add_edge(
        "shop.cleaned.orders",
        "shop.mart.daily_revenue",
        "daily aggregation",
        {"revenue": "amount"},
    )
    p.lineage.add_edge(
        "shop.cleaned.orders",
        "shop.mart.fraud_review",
        "fraud filter",
        {"order_id": "order_id"},
    )
    return p


@pytest.fixture
def platform() -> GovernancePlatform:
    return make_small_platform()


@pytest.fixture
def demo_platform() -> GovernancePlatform:
    return build_demo_platform()
