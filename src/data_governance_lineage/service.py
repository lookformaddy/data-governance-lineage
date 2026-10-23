"""Optional FastAPI HTTP surface for the governance platform.

FastAPI is an *optional* dependency: this module imports cleanly without it
(``create_app`` raises a clear error), and the API tests auto-skip when it
is not installed. The core engine stays stdlib-only.
"""

from __future__ import annotations

try:
    from fastapi import FastAPI, HTTPException

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when FastAPI is missing
    FastAPI = None  # type: ignore[assignment,misc]
    HTTPException = Exception  # type: ignore[assignment,misc]
    FASTAPI_AVAILABLE = False

from data_governance_lineage.demo import build_demo_platform
from data_governance_lineage.models import Action, Classification, Dataset, DatasetKind, Role


def _require_fastapi() -> None:
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI is not installed — install it with: pip install 'data-governance-lineage[api]'")


def _parse_dataset(body: dict) -> Dataset:
    try:
        return Dataset(
            name=body["name"],
            kind=DatasetKind(body.get("kind", "table")),
            domain=body["domain"],
            owner=body["owner"],
            classification=Classification[body.get("classification", "INTERNAL")],
            schema=[],
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing field: {exc}") from exc


def create_app(platform=None):
    """Build the FastAPI app (demo platform when none is supplied)."""
    _require_fastapi()
    p = platform or build_demo_platform()
    app = FastAPI(title="data-governance-lineage", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "summary": p.summary()}

    # -- catalog ------------------------------------------------------ #
    @app.post("/datasets", status_code=201)
    def register_dataset(body: dict):
        from data_governance_lineage.models import Column

        dataset = _parse_dataset(body)
        dataset.schema = [
            Column(name=c["name"], dtype=c.get("dtype", "string"), pii=c.get("pii", False))
            for c in body.get("schema", [])
        ]
        try:
            p.catalog.register(dataset)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"dataset": dataset.name}

    @app.get("/datasets")
    def list_datasets(domain: str | None = None):
        return {"datasets": [d.name for d in p.catalog.list(domain=domain)]}

    @app.get("/datasets/{name:path}")
    def get_dataset(name: str):
        try:
            d = p.catalog.get(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "name": d.name,
            "domain": d.domain,
            "owner": d.owner,
            "steward": d.steward,
            "classification": d.classification.name,
            "certification": d.certification.name,
            "columns": [{"name": c.name, "dtype": c.dtype, "pii": c.pii} for c in d.schema],
        }

    # -- lineage ------------------------------------------------------ #
    @app.get("/lineage/upstream/{name:path}")
    def lineage_upstream(name: str, depth: int | None = None):
        return {"dataset": name, "upstream": p.lineage.upstream(name, depth)}

    @app.get("/lineage/downstream/{name:path}")
    def lineage_downstream(name: str, depth: int | None = None):
        return {"dataset": name, "downstream": p.lineage.downstream(name, depth)}

    @app.get("/lineage/impact/{name:path}")
    def lineage_impact(name: str):
        return p.quality.impact_report(name)

    @app.get("/lineage/export")
    def lineage_export(format: str = "mermaid"):
        if format == "dot":
            return {"format": "dot", "graph": p.lineage.to_dot()}
        if format == "mermaid":
            return {"format": "mermaid", "graph": p.lineage.to_mermaid()}
        raise HTTPException(status_code=400, detail="format must be 'dot' or 'mermaid'")

    # -- policies ----------------------------------------------------- #
    @app.post("/policies/evaluate")
    def evaluate_policy(body: dict):
        try:
            dataset = p.catalog.get(body["dataset"])
            decision = p.policies.evaluate(
                body["actor"], Role(body["role"]), dataset, Action(body["action"])
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "decision": decision.decision.value,
            "reason": decision.reason,
            "break_glass": decision.break_glass,
        }

    @app.post("/policies/break-glass", status_code=201)
    def break_glass(body: dict):
        try:
            grant = p.policies.request_break_glass(
                body["actor"], body["dataset"], body["justification"],
                ttl_seconds=int(body.get("ttl_seconds", 3600)),
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"grant_id": grant.grant_id, "expires_at": grant.expires_at.isoformat()}

    @app.get("/policies/audit")
    def audit(limit: int = 50):
        return {
            "events": [
                {
                    "event_id": e.event_id,
                    "timestamp": e.timestamp.isoformat(),
                    "actor": e.actor,
                    "action": e.action,
                    "dataset": e.dataset,
                    "decision": e.decision.value,
                    "reason": e.reason,
                    "break_glass": e.break_glass,
                }
                for e in p.policies.audit_log(limit=limit)
            ]
        }

    # -- privacy ------------------------------------------------------ #
    @app.post("/privacy/erasure", status_code=201)
    def request_erasure(body: dict):
        req = p.privacy.request_erasure(body["subject_id"], requested_by=body.get("requested_by", "api"))
        done = p.privacy.execute_erasure(req.request_id)
        return {
            "request_id": done.request_id,
            "status": done.status.value,
            "per_dataset_counts": done.per_dataset_counts,
        }

    @app.get("/privacy/access/{subject_id}")
    def access_request(subject_id: str, role: str = "analyst"):
        try:
            parsed_role = Role(role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return p.privacy.access_request(subject_id, parsed_role)

    # -- quality ------------------------------------------------------ #
    @app.post("/quality/results")
    def record_quality(body: dict):
        try:
            check = p.quality.record_result(
                body["dataset"], body["check"], bool(body["passed"]), body.get("details", "")
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "dataset": check.dataset,
            "check": check.name,
            "passed": check.last_passed,
            "tainted": p.quality.tainted_datasets(),
        }

    @app.get("/quality/tainted")
    def tainted():
        return {"tainted_datasets": p.quality.tainted_datasets()}

    # -- workflows ---------------------------------------------------- #
    @app.post("/workflows/certify")
    def certify(body: dict):
        from data_governance_lineage.models import GovernanceError

        try:
            status = p.certification.certify(body["dataset"], body["approver"], Role(body["role"]))
        except (GovernanceError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"dataset": body["dataset"], "certification": status.value}

    return app


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104 - demo service
    _require_fastapi()
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
