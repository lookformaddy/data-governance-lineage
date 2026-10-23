"""Synthetic demo environment: a realistic multi-domain data estate.

All datasets, schemas, people, and records are invented for the demo —
generic e-commerce/marketing/supply-chain domains, no real company data.
The estate is small enough to read in one sitting but rich enough to show
every feature: a raw -> cleaned -> curated lineage chain per domain, a
cross-domain mart, PII columns, classifications, stewards, quality checks,
and an intentional lineage branch for impact analysis.
"""

from __future__ import annotations

from data_governance_lineage.models import (
    CertificationStatus,
    Classification,
    Column,
    Dataset,
    DatasetKind,
    Role,
)
from data_governance_lineage.platform import GovernancePlatform


def _col(name: str, dtype: str, pii: bool = False, desc: str = "") -> Column:
    return Column(name=name, dtype=dtype, pii=pii, description=desc)


def build_demo_platform() -> GovernancePlatform:
    p = GovernancePlatform()

    # ------------------------------------------------------------------ #
    # Catalog: marketing domain
    # ------------------------------------------------------------------ #
    p.catalog.register(
        Dataset(
            name="marketing.raw.clickstream",
            kind=DatasetKind.STREAM,
            domain="marketing",
            owner="mktg-data-eng",
            classification=Classification.INTERNAL,
            retention_days=90,
            tags=["events", "web"],
            schema=[
                _col("event_id", "string"),
                _col("event_ts", "timestamp"),
                _col("anonymous_id", "string"),
                _col("email", "string", pii=True, desc="only present when user identified"),
                _col("page_url", "string"),
                _col("campaign_id", "string", desc="nullable"),
            ],
        )
    )
    p.catalog.register(
        Dataset(
            name="marketing.cleaned.sessions",
            kind=DatasetKind.TABLE,
            domain="marketing",
            owner="mktg-data-eng",
            classification=Classification.INTERNAL,
            retention_days=365,
            tags=["sessions", "curated-input"],
            schema=[
                _col("session_id", "string"),
                _col("anonymous_id", "string"),
                _col("email", "string", pii=True),
                _col("session_start", "timestamp"),
                _col("session_end", "timestamp"),
                _col("page_views", "int"),
                _col("campaign_id", "string"),
            ],
        )
    )

    # ------------------------------------------------------------------ #
    # Catalog: orders domain
    # ------------------------------------------------------------------ #
    p.catalog.register(
        Dataset(
            name="orders.raw.order_events",
            kind=DatasetKind.STREAM,
            domain="orders",
            owner="orders-platform",
            classification=Classification.CONFIDENTIAL,
            retention_days=90,
            tags=["events", "transactions"],
            schema=[
                _col("order_id", "string"),
                _col("event_ts", "timestamp"),
                _col("customer_id", "string"),
                _col("email", "string", pii=True),
                _col("phone", "string", pii=True),
                _col("amount_cents", "int"),
                _col("currency", "string"),
            ],
        )
    )
    p.catalog.register(
        Dataset(
            name="orders.curated.fact_orders",
            kind=DatasetKind.TABLE,
            domain="orders",
            owner="orders-platform",
            classification=Classification.CONFIDENTIAL,
            retention_days=2555,  # 7 years: financial record-keeping
            tags=["facts", "finance"],
            schema=[
                _col("order_id", "string"),
                _col("order_date", "date"),
                _col("customer_id", "string"),
                _col("email", "string", pii=True),
                _col("phone", "string", pii=True),
                _col("amount_cents", "int"),
                _col("currency", "string"),
                _col("is_fraud_suspect", "boolean"),
            ],
        )
    )

    # ------------------------------------------------------------------ #
    # Catalog: supply chain domain
    # ------------------------------------------------------------------ #
    p.catalog.register(
        Dataset(
            name="supply.raw.inventory_snapshots",
            kind=DatasetKind.TABLE,
            domain="supply_chain",
            owner="supply-data-eng",
            classification=Classification.INTERNAL,
            retention_days=730,
            tags=["inventory"],
            schema=[
                _col("snapshot_ts", "timestamp"),
                _col("sku", "string"),
                _col("warehouse_id", "string"),
                _col("on_hand_qty", "int"),
            ],
        )
    )
    p.catalog.register(
        Dataset(
            name="supply.curated.inventory_daily",
            kind=DatasetKind.TABLE,
            domain="supply_chain",
            owner="supply-data-eng",
            classification=Classification.INTERNAL,
            retention_days=1095,
            tags=["inventory", "daily"],
            schema=[
                _col("snapshot_date", "date"),
                _col("sku", "string"),
                _col("warehouse_id", "string"),
                _col("on_hand_qty", "int"),
                _col("days_of_cover", "double"),
            ],
        )
    )

    # ------------------------------------------------------------------ #
    # Catalog: cross-domain marts (analyst-facing)
    # ------------------------------------------------------------------ #
    p.catalog.register(
        Dataset(
            name="analytics.mart.customer_360",
            kind=DatasetKind.TABLE,
            domain="analytics",
            owner="analytics-eng",
            classification=Classification.RESTRICTED,
            retention_days=1095,
            tags=["mart", "customer"],
            schema=[
                _col("customer_id", "string"),
                _col("email", "string", pii=True),
                _col("phone", "string", pii=True),
                _col("lifetime_orders", "int"),
                _col("lifetime_value_cents", "int"),
                _col("sessions_30d", "int"),
                _col("segment", "string"),
            ],
        )
    )
    p.catalog.register(
        Dataset(
            name="analytics.mart.marketing_attribution",
            kind=DatasetKind.TABLE,
            domain="analytics",
            owner="analytics-eng",
            classification=Classification.INTERNAL,
            retention_days=730,
            tags=["mart", "marketing"],
            schema=[
                _col("campaign_id", "string"),
                _col("attributed_orders", "int"),
                _col("attributed_revenue_cents", "int"),
                _col("spend_cents", "int"),
            ],
        )
    )

    # ------------------------------------------------------------------ #
    # Lineage edges (declared transforms with column mappings)
    # ------------------------------------------------------------------ #
    p.lineage.add_edge(
        "marketing.raw.clickstream",
        "marketing.cleaned.sessions",
        "sessionize clickstream (30-min inactivity gap)",
        column_map={
            "anonymous_id": "anonymous_id",
            "email": "email",
            "campaign_id": "campaign_id",
        },
    )
    p.lineage.add_edge(
        "orders.raw.order_events",
        "orders.curated.fact_orders",
        "dedupe + fraud scoring",
        column_map={
            "order_id": "order_id",
            "customer_id": "customer_id",
            "email": "email",
            "phone": "phone",
            "amount_cents": "amount_cents",
            "currency": "currency",
        },
    )
    p.lineage.add_edge(
        "supply.raw.inventory_snapshots",
        "supply.curated.inventory_daily",
        "daily rollup + days-of-cover calc",
        column_map={
            "sku": "sku",
            "warehouse_id": "warehouse_id",
            "on_hand_qty": "on_hand_qty",
        },
    )
    p.lineage.add_edge(
        "orders.curated.fact_orders",
        "analytics.mart.customer_360",
        "customer aggregation join",
        column_map={
            "customer_id": "customer_id",
            "email": "email",
            "phone": "phone",
        },
    )
    p.lineage.add_edge(
        "marketing.cleaned.sessions",
        "analytics.mart.customer_360",
        "session features join",
        column_map={"sessions_30d": "page_views"},
    )
    p.lineage.add_edge(
        "marketing.cleaned.sessions",
        "analytics.mart.marketing_attribution",
        "last-touch attribution",
        column_map={"campaign_id": "campaign_id"},
    )
    p.lineage.add_edge(
        "orders.curated.fact_orders",
        "analytics.mart.marketing_attribution",
        "revenue attribution join",
        column_map={"attributed_orders": "order_id", "attributed_revenue_cents": "amount_cents"},
    )

    # ------------------------------------------------------------------ #
    # Stewards, quality checks, PII reviews
    # ------------------------------------------------------------------ #
    for ds in p.catalog.list():
        p.catalog.set_steward(ds.name, "steward-" + ds.domain.replace("_", "-"))
    p.stewards.assign("analytics.mart.customer_360", "priya-steward", Role.ADMIN)

    for ds in p.catalog.list():
        p.quality.register_check(ds.name, "schema_valid", "schema matches registered contract")
        p.quality.register_check(ds.name, "freshness_sla", "data landed within SLA")
        p.quality.record_result(ds.name, "schema_valid", True)
        p.quality.record_result(ds.name, "freshness_sla", True)
        p.privacy.complete_pii_review(ds.name)

    # Certify the two marts end-to-end through the real lifecycle
    for mart in ("analytics.mart.customer_360", "analytics.mart.marketing_attribution"):
        p.certification.submit_for_review(mart, submitted_by=p.catalog.get(mart).owner)
        p.certification.certify(mart, approver="data-governance-lead", role=Role.ADMIN)

    # ------------------------------------------------------------------ #
    # Synthetic subject records (for SAR/erasure demos)
    # ------------------------------------------------------------------ #
    subjects = ["SUBJ-001", "SUBJ-002", "SUBJ-003"]
    for i, sid in enumerate(subjects, start=1):
        p.privacy.add_subject_records(
            "orders.curated.fact_orders",
            [
                {
                    "subject_id": sid,
                    "order_id": f"ORD-100{i}",
                    "customer_id": f"CUST-00{i}",
                    "email": f"customer{i}@example.com",
                    "phone": f"+1-415-555-010{i}",
                    "amount_cents": 1000 * i,
                }
            ],
        )
        p.privacy.add_subject_records(
            "analytics.mart.customer_360",
            [
                {
                    "subject_id": sid,
                    "customer_id": f"CUST-00{i}",
                    "email": f"customer{i}@example.com",
                    "phone": f"+1-415-555-010{i}",
                    "segment": "high_value" if i == 1 else "standard",
                }
            ],
        )

    return p


def run_demo() -> str:
    """Run the end-to-end demo narrative; returns the printed report."""
    from data_governance_lineage.models import Action, Decision, Role

    p = build_demo_platform()
    out: list[str] = []
    add = out.append

    add("== 1. Catalog ==")
    stats = p.catalog.stats()
    add(f"datasets: {stats['datasets']} | domains: {sorted(stats['by_domain'])}")
    add(f"classifications: {stats['by_classification']}")
    add(f"certifications: {stats['by_certification']}")

    add("\n== 2. Lineage: impact of orders.curated.fact_orders ==")
    for item in p.lineage.impact_report("orders.curated.fact_orders"):
        add(f"  {' -> '.join(item['path'])}")

    add("\n== 3. Column lineage: where does customer_360.email come from? ==")
    for (ds, col), hops in sorted(
        p.lineage.upstream_columns("analytics.mart.customer_360", "email").items()
    ):
        add(f"  {ds}.{col} ({hops} hop(s) upstream)")

    add("\n== 4. Access policy decisions ==")
    scenarios = [
        ("ana", Role.ANALYST, "analytics.mart.marketing_attribution", Action.READ),
        ("ana", Role.ANALYST, "analytics.mart.customer_360", Action.READ),
        ("eng", Role.DATA_ENGINEER, "orders.curated.fact_orders", Action.WRITE),
        ("ext", Role.EXTERNAL, "orders.curated.fact_orders", Action.READ),
    ]
    for actor, role, ds, action in scenarios:
        d = p.policies.evaluate(actor, role, p.catalog.get(ds), action)
        add(f"  {role.value:14s} {action.value:5s} {ds:42s} -> {d.decision.value.upper():5s} ({d.reason})")

    add("\n== 5. Break-glass: incident access to restricted mart ==")
    d0 = p.policies.evaluate("ana", Role.ANALYST, p.catalog.get("analytics.mart.customer_360"), Action.READ)
    add(f"  before: {d0.decision.value} ({d0.reason})")
    grant = p.policies.request_break_glass(
        "ana", "analytics.mart.customer_360", "INC-4242: investigate revenue anomaly", ttl_seconds=1800
    )
    d1 = p.policies.evaluate("ana", Role.ANALYST, p.catalog.get("analytics.mart.customer_360"), Action.READ)
    add(f"  grant {grant.grant_id} expires {grant.expires_at.isoformat()}")
    add(f"  during: {d1.decision.value} (break_glass={d1.break_glass})")

    add("\n== 6. Lineage-aware quality: upstream failure taints downstream ==")
    p.quality.record_result("orders.raw.order_events", "freshness_sla", False, "source delayed 6h")
    report = p.quality.impact_report("orders.raw.order_events")
    add(f"  failing: {report['failing_checks']} -> {report['impacted_count']} downstream dataset(s) tainted")
    for imp in report["impacted"]:
        add(f"    - {imp['dataset']} (owner={imp['owner']}, steward={imp['steward']}, {imp['classification']})")

    add("\n== 7. Privacy: subject access + erasure ==")
    sar = p.privacy.access_request("SUBJ-001", Role.ANALYST)
    add(f"  SAR for SUBJ-001: {len(sar['datasets'])} dataset(s) hold records")
    req = p.privacy.request_erasure("SUBJ-003", requested_by="privacy-officer")
    p.privacy.execute_erasure(req.request_id)
    add(f"  erasure {req.request_id}: {req.per_dataset_counts} (tombstones: {len(p.privacy.tombstones('SUBJ-003'))})")
    add(f"  records remaining for SUBJ-003: {p.privacy.remaining_records('SUBJ-003')}")

    add("\n== 8. Exports ==")
    add("  mermaid preview:")
    for line in p.lineage.to_mermaid().splitlines()[:4]:
        add(f"    {line}")
    add("    ...")

    add("\n== 9. Audit trail ==")
    summary = p.policies.audit_summary()
    add(f"  {summary['total_events']} events: {summary['allowed']} allowed, {summary['denied']} denied, "
        f"{summary['break_glass_events']} break-glass")

    return "\n".join(out)
