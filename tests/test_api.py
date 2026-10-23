"""API surface tests — auto-skip when FastAPI is not installed."""

import pytest

fastapi = pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("fastapi.testclient requires httpx", allow_module_level=True)

from data_governance_lineage.demo import build_demo_platform
from data_governance_lineage.service import create_app


@pytest.fixture
def client():
    return TestClient(create_app(build_demo_platform()))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["summary"]["catalog"]["datasets"] == 8


def test_list_and_get_datasets(client):
    r = client.get("/datasets")
    assert r.status_code == 200
    assert "marketing.raw.clickstream" in r.json()["datasets"]
    r = client.get("/datasets/orders.curated.fact_orders")
    assert r.status_code == 200
    body = r.json()
    assert body["classification"] == "CONFIDENTIAL"
    assert body["certification"] == "PROPOSED"
    assert "email" in [c["name"] for c in body["columns"]]
    assert client.get("/datasets/nope.unknown").status_code == 404


def test_register_dataset(client):
    r = client.post(
        "/datasets",
        json={
            "name": "shop.raw.returns",
            "kind": "table",
            "domain": "shop",
            "owner": "team-shop",
            "classification": "INTERNAL",
            "schema": [{"name": "return_id", "dtype": "string"}],
        },
    )
    assert r.status_code == 201
    assert r.json()["dataset"] == "shop.raw.returns"
    # duplicate rejected
    assert client.post(
        "/datasets",
        json={"name": "shop.raw.returns", "domain": "shop", "owner": "x"},
    ).status_code == 400


def test_lineage_endpoints(client):
    r = client.get("/lineage/downstream/orders.curated.fact_orders")
    assert r.status_code == 200
    assert "analytics.mart.customer_360" in r.json()["downstream"]
    r = client.get("/lineage/upstream/analytics.mart.customer_360?depth=1")
    assert r.status_code == 200
    assert set(r.json()["upstream"]) == {"orders.curated.fact_orders", "marketing.cleaned.sessions"}
    r = client.get("/lineage/export?format=dot")
    assert r.status_code == 200
    assert r.json()["graph"].startswith("digraph lineage {")
    assert client.get("/lineage/export?format=bogus").status_code == 400


def test_policy_evaluate_and_break_glass(client):
    denied = client.post(
        "/policies/evaluate",
        json={"actor": "ana", "role": "analyst", "dataset": "analytics.mart.customer_360", "action": "read"},
    )
    assert denied.json()["decision"] == "deny"
    grant = client.post(
        "/policies/break-glass",
        json={"actor": "ana", "dataset": "analytics.mart.customer_360",
              "justification": "INC-9: revenue anomaly", "ttl_seconds": 600},
    )
    assert grant.status_code == 201
    allowed = client.post(
        "/policies/evaluate",
        json={"actor": "ana", "role": "analyst", "dataset": "analytics.mart.customer_360", "action": "read"},
    )
    assert allowed.json()["decision"] == "allow"
    assert allowed.json()["break_glass"] is True
    audit = client.get("/policies/audit?limit=5")
    assert audit.status_code == 200
    assert len(audit.json()["events"]) >= 3


def test_erasure_and_access_endpoints(client):
    sar = client.get("/privacy/access/SUBJ-001?role=analyst")
    assert sar.status_code == 200
    assert len(sar.json()["datasets"]) == 2
    erasure = client.post(
        "/privacy/erasure", json={"subject_id": "SUBJ-002", "requested_by": "api-test"}
    )
    assert erasure.status_code == 201
    assert erasure.json()["status"] == "completed"
    assert sum(erasure.json()["per_dataset_counts"].values()) == 2


def test_quality_endpoints(client):
    r = client.post(
        "/quality/results",
        json={"dataset": "orders.raw.order_events", "check": "freshness_sla",
              "passed": False, "details": "source delayed"},
    )
    assert r.status_code == 200
    assert "analytics.mart.customer_360" in r.json()["tainted"]
    tainted = client.get("/quality/tainted")
    assert "orders.raw.order_events" in tainted.json()["tainted_datasets"]
    impact = client.get("/lineage/impact/orders.raw.order_events")
    # fact_orders (1 hop) -> customer_360 + marketing_attribution (2 hops)
    assert impact.json()["impacted_count"] == 3
