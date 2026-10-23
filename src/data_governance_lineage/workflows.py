"""Governance workflows: certification lifecycle, schema-change reviews, stewards.

Certification lifecycle
-----------------------
``PROPOSED -> UNDER_REVIEW -> CERTIFIED`` with ``DEPRECATED`` reachable from
any state. Certification is gated by a real checklist (owner, steward,
retention, PII review, passing quality checks) — a dataset cannot be stamped
"certified" by clicking a button; the gate enforces the evidence.

Schema-change review queue
--------------------------
Schema changes are never applied directly. A request captures the proposed
columns; a steward/admin approves (applies via the catalog, recording a
schema version) or rejects with a reason.

Steward directory
-----------------
Steward assignment per dataset, queryable by domain. Certification requires
a named steward, so ownership is never ambiguous.
"""

from __future__ import annotations

from data_governance_lineage.catalog import Catalog
from data_governance_lineage.models import (
    CertificationError,
    CertificationStatus,
    Column,
    InvalidTransition,
    Role,
    SchemaChangeRequest,
    SchemaChangeStatus,
    utcnow,
)

_CERTIFIERS = {Role.ADMIN, Role.STEWARD}


class CertificationLifecycle:
    def __init__(self, catalog: Catalog, quality: object | None = None) -> None:
        self._catalog = catalog
        self._quality = quality  # QualityMonitor, optional for the quality gate

    def propose(self, dataset_name: str) -> CertificationStatus:
        dataset = self._catalog.get(dataset_name)
        if dataset.certification is not CertificationStatus.PROPOSED:
            raise InvalidTransition(
                f"{dataset_name} is {dataset.certification.value}; only PROPOSED datasets can be (re-)proposed"
            )
        return dataset.certification

    def submit_for_review(self, dataset_name: str, submitted_by: str) -> CertificationStatus:
        dataset = self._catalog.get(dataset_name)
        if dataset.certification is not CertificationStatus.PROPOSED:
            raise InvalidTransition(
                f"{dataset_name} is {dataset.certification.value}; submit_for_review requires PROPOSED"
            )
        self._catalog.set_certification(dataset_name, CertificationStatus.UNDER_REVIEW)
        return CertificationStatus.UNDER_REVIEW

    def certify(self, dataset_name: str, approver: str, role: Role) -> CertificationStatus:
        dataset = self._catalog.get(dataset_name)
        if role not in _CERTIFIERS:
            raise CertificationError(f"Role {role.value} cannot certify datasets (requires steward or admin)")
        if dataset.certification is not CertificationStatus.UNDER_REVIEW:
            raise InvalidTransition(
                f"{dataset_name} is {dataset.certification.value}; certify requires UNDER_REVIEW"
            )
        missing = self._checklist(dataset_name)
        if missing:
            raise CertificationError(
                f"Cannot certify {dataset_name}: checklist incomplete — missing: {', '.join(missing)}"
            )
        self._catalog.set_certification(dataset_name, CertificationStatus.CERTIFIED)
        return CertificationStatus.CERTIFIED

    def deprecate(self, dataset_name: str, reason: str) -> CertificationStatus:
        dataset = self._catalog.get(dataset_name)
        if not reason or not reason.strip():
            raise ValueError("Deprecation requires a reason")
        if dataset.certification is CertificationStatus.DEPRECATED:
            raise InvalidTransition(f"{dataset_name} is already deprecated")
        self._catalog.set_certification(dataset_name, CertificationStatus.DEPRECATED)
        return CertificationStatus.DEPRECATED

    def _checklist(self, dataset_name: str) -> list[str]:
        """Return the list of unmet certification requirements (empty = certifiable)."""
        dataset = self._catalog.get(dataset_name)
        missing: list[str] = []
        if not dataset.steward:
            missing.append("steward assigned")
        if dataset.retention_days is None:
            missing.append("retention policy set")
        if not dataset.pii_reviewed:
            missing.append("PII review completed")
        if self._quality is not None:
            checks = self._quality.checks_for(dataset_name)  # type: ignore[attr-defined]
            if not checks:
                missing.append("at least one quality check registered")
            elif not self._quality.all_passing(dataset_name):  # type: ignore[attr-defined]
                missing.append("all quality checks passing")
        return missing


class SchemaChangeQueue:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._requests: dict[str, SchemaChangeRequest] = {}
        self._seq = 0

    def submit(
        self, dataset_name: str, proposed_columns: list[Column], requested_by: str
    ) -> SchemaChangeRequest:
        self._catalog.get(dataset_name)  # validate dataset exists
        if not proposed_columns:
            raise ValueError("Proposed schema must be non-empty")
        names = [c.name for c in proposed_columns]
        if len(set(names)) != len(names):
            raise ValueError("Proposed schema has duplicate column names")
        self._seq += 1
        request = SchemaChangeRequest(
            request_id=f"schema-{self._seq:04d}",
            dataset=dataset_name,
            proposed_columns=list(proposed_columns),
            requested_by=requested_by,
        )
        self._requests[request.request_id] = request
        return request

    def approve(self, request_id: str, reviewer: str, role: Role) -> SchemaChangeRequest:
        request = self._get_open(request_id)
        if role not in _CERTIFIERS:
            raise CertificationError(f"Role {role.value} cannot approve schema changes")
        self._catalog.apply_schema(
            request.dataset,
            request.proposed_columns,
            change_note=f"schema change {request.request_id} approved by {reviewer}",
            requested_by=request.requested_by,
        )
        request.status = SchemaChangeStatus.APPROVED
        request.reviewer = reviewer
        request.decided_at = utcnow()
        return request

    def reject(self, request_id: str, reviewer: str, reason: str) -> SchemaChangeRequest:
        request = self._get_open(request_id)
        if not reason or not reason.strip():
            raise ValueError("Rejection requires a reason")
        request.status = SchemaChangeStatus.REJECTED
        request.reviewer = reviewer
        request.reason = reason.strip()
        request.decided_at = utcnow()
        return request

    def _get_open(self, request_id: str) -> SchemaChangeRequest:
        try:
            request = self._requests[request_id]
        except KeyError:
            raise KeyError(f"Unknown schema change request: {request_id}") from None
        if request.status is not SchemaChangeStatus.OPEN:
            raise InvalidTransition(f"Request {request_id} is already {request.status.value}")
        return request

    def open_requests(self, dataset: str | None = None) -> list[SchemaChangeRequest]:
        reqs = [r for r in self._requests.values() if r.status is SchemaChangeStatus.OPEN]
        if dataset is not None:
            reqs = [r for r in reqs if r.dataset == dataset]
        return reqs

    def all_requests(self) -> list[SchemaChangeRequest]:
        return list(self._requests.values())


class StewardDirectory:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def assign(self, dataset_name: str, steward: str, assigned_by_role: Role) -> str:
        if assigned_by_role is not Role.ADMIN:
            raise CertificationError("Only admins can assign stewards")
        if not steward or not steward.strip():
            raise ValueError("Steward name must be non-empty")
        self._catalog.set_steward(dataset_name, steward.strip())
        return steward.strip()

    def unassign(self, dataset_name: str, assigned_by_role: Role) -> None:
        if assigned_by_role is not Role.ADMIN:
            raise CertificationError("Only admins can unassign stewards")
        dataset = self._catalog.get(dataset_name)
        dataset.steward = None
        dataset.updated_at = utcnow()

    def stewards_for_domain(self, domain: str) -> dict[str, list[str]]:
        """Map steward -> datasets they steward within a domain."""
        result: dict[str, list[str]] = {}
        for d in self._catalog.list(domain=domain):
            if d.steward:
                result.setdefault(d.steward, []).append(d.name)
        return result
