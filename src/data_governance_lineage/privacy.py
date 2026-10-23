"""Privacy: PII tagging, masking rules, subject access/erasure request handling.

Scope notes (deliberate):
- This implements the *governance control plane* for privacy: tagging,
  policy decisions, tombstone accounting. It is not a deletion engine for
  every downstream system — in production, erasure requests fan out to
  per-system executors (warehouse DELETEs, stream compactions, backup
  expiry) and this module tracks their completion.
- Masking is format-preserving-ish (email domain kept, phone tail kept) so
  masked data stays joinable/testable in lower environments — a common
  enterprise requirement for realistic dev data.
"""

from __future__ import annotations

import re

from data_governance_lineage.catalog import Catalog
from data_governance_lineage.models import (
    Classification,
    ErasureRequest,
    ErasureStatus,
    Role,
    SubjectRecord,
    Tombstone,
    utcnow,
)

# Roles that may see unmasked PII on CONFIDENTIAL/RESTRICTED datasets.
_UNMASKED_ROLES = {Role.ADMIN, Role.STEWARD}


def mask_email(value: str) -> str:
    if "@" not in value:
        return "***REDACTED***"
    local, _, domain = value.partition("@")
    head = local[:1] if local else ""
    return f"{head}***@{domain}"


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    tail = digits[-4:] if len(digits) >= 4 else "****"
    return f"***-***-{tail}"


def mask_generic(_value: object) -> str:
    return "***REDACTED***"


def mask_value(column_name: str, value: object) -> str:
    """Pick a masking rule from the column name; deterministic."""
    name = column_name.lower()
    text = "" if value is None else str(value)
    if "email" in name:
        return mask_email(text)
    if "phone" in name or "mobile" in name:
        return mask_phone(text)
    return mask_generic(text)


def apply_masking(record: dict, dataset_name: str, catalog: Catalog, role: Role) -> dict:
    """Return a copy of ``record`` with PII masked per classification policy.

    PUBLIC/INTERNAL datasets pass through. On CONFIDENTIAL/RESTRICTED datasets
    only ADMIN/STEWARD roles see raw PII; everyone else gets masked values.
    Unknown columns are left untouched (fail-open would be a leak; fail-closed
    would break analytics — masking only *tagged* PII columns is the middle
    path enterprises actually take, enforced by the pii_reviewed gate).
    """
    dataset = catalog.get(dataset_name)
    if dataset.classification in (Classification.PUBLIC, Classification.INTERNAL):
        return dict(record)
    if role in _UNMASKED_ROLES:
        return dict(record)
    masked = dict(record)
    for col in dataset.pii_columns():
        if col in masked:
            masked[col] = mask_value(col, masked[col])
    return masked


class PrivacyService:
    """PII tagging + subject-data store + SAR/erasure request lifecycle."""

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._records: dict[str, list[SubjectRecord]] = {}  # dataset -> records
        self._tombstones: list[Tombstone] = []
        self._erasure_requests: dict[str, ErasureRequest] = {}
        self._erasure_seq = 0

    # ------------------------------------------------------------------ #
    # PII tagging
    # ------------------------------------------------------------------ #
    def tag_pii_columns(self, dataset_name: str, columns: list[str]) -> list[str]:
        dataset = self._catalog.get(dataset_name)
        tagged = []
        for name in columns:
            col = dataset.get_column(name)
            if col is None:
                raise KeyError(f"Column {name!r} not found in dataset {dataset_name!r}")
            col.pii = True
            tagged.append(name)
        dataset.updated_at = utcnow()
        return tagged

    def untag_pii_columns(self, dataset_name: str, columns: list[str]) -> list[str]:
        dataset = self._catalog.get(dataset_name)
        untagged = []
        for name in columns:
            col = dataset.get_column(name)
            if col is None:
                raise KeyError(f"Column {name!r} not found in dataset {dataset_name!r}")
            col.pii = False
            untagged.append(name)
        dataset.updated_at = utcnow()
        return untagged

    def complete_pii_review(self, dataset_name: str) -> None:
        """Mark the PII review done — a certification checklist prerequisite."""
        self._catalog.mark_pii_reviewed(dataset_name)

    # ------------------------------------------------------------------ #
    # Subject data store (synthetic demo data lives here)
    # ------------------------------------------------------------------ #
    def add_subject_records(self, dataset_name: str, records: list[dict]) -> int:
        self._catalog.get(dataset_name)  # validate dataset exists
        stored = self._records.setdefault(dataset_name, [])
        for rec in records:
            if "subject_id" not in rec:
                raise ValueError("Subject records require a 'subject_id' field")
            stored.append(SubjectRecord(dataset=dataset_name, subject_id=str(rec["subject_id"]), fields=dict(rec)))
        return len(records)

    def subject_datasets(self, subject_id: str) -> list[str]:
        return sorted(
            ds for ds, recs in self._records.items() if any(r.subject_id == subject_id for r in recs)
        )

    # ------------------------------------------------------------------ #
    # Subject Access Request (SAR)
    # ------------------------------------------------------------------ #
    def access_request(self, subject_id: str, role: Role) -> dict:
        """Build the SAR manifest: what we hold, where, and under what policy.

        Values are included for PUBLIC/INTERNAL datasets; for higher
        classifications the manifest lists fields + classifications but
        withholds raw values (delivered through a separate secure channel in
        production).
        """
        manifest: list[dict] = []
        for dataset_name in self.subject_datasets(subject_id):
            dataset = self._catalog.get(dataset_name)
            recs = [r for r in self._records[dataset_name] if r.subject_id == subject_id]
            if dataset.classification in (Classification.PUBLIC, Classification.INTERNAL):
                values = [apply_masking(r.fields, dataset_name, self._catalog, role) for r in recs]
            else:
                values = [{"_withheld": f"values withheld ({dataset.classification.name}); request secure delivery"} for _ in recs]
            manifest.append(
                {
                    "dataset": dataset_name,
                    "classification": dataset.classification.name,
                    "record_count": len(recs),
                    "fields": dataset.column_names(),
                    "pii_fields": dataset.pii_columns(),
                    "values": values,
                }
            )
        return {"subject_id": subject_id, "datasets": manifest}

    # ------------------------------------------------------------------ #
    # Erasure request (right to be forgotten)
    # ------------------------------------------------------------------ #
    def request_erasure(self, subject_id: str, requested_by: str) -> ErasureRequest:
        self._erasure_seq += 1
        request = ErasureRequest(
            request_id=f"erasure-{self._erasure_seq:04d}",
            subject_id=subject_id,
            status=ErasureStatus.OPEN,
            requested_by=requested_by,
        )
        self._erasure_requests[request.request_id] = request
        return request

    def execute_erasure(self, request_id: str) -> ErasureRequest:
        """Erase the subject's records everywhere; write a tombstone per dataset.

        Tombstones are the audit proof of erasure — they record *that* records
        were removed (and how many) without retaining the removed values.
        """
        request = self._erasure_requests.get(request_id)
        if request is None:
            raise KeyError(f"Unknown erasure request: {request_id}")
        if request.status is ErasureStatus.COMPLETED:
            return request  # idempotent
        request.status = ErasureStatus.IN_PROGRESS
        try:
            for dataset_name in self.subject_datasets(request.subject_id):
                recs = self._records.get(dataset_name, [])
                doomed = [r for r in recs if r.subject_id == request.subject_id]
                self._records[dataset_name] = [r for r in recs if r.subject_id != request.subject_id]
                self._tombstones.append(
                    Tombstone(
                        subject_id=request.subject_id,
                        dataset=dataset_name,
                        erased_count=len(doomed),
                        erased_at=utcnow(),
                        requested_by=request.requested_by,
                    )
                )
                request.per_dataset_counts[dataset_name] = len(doomed)
            request.status = ErasureStatus.COMPLETED
            request.completed_at = utcnow()
        except Exception as exc:  # keep the request inspectable on partial failure
            request.status = ErasureStatus.FAILED
            request.error = str(exc)
            raise
        return request

    def get_erasure_request(self, request_id: str) -> ErasureRequest:
        try:
            return self._erasure_requests[request_id]
        except KeyError:
            raise KeyError(f"Unknown erasure request: {request_id}") from None

    def tombstones(self, subject_id: str | None = None) -> list[Tombstone]:
        if subject_id is None:
            return list(self._tombstones)
        return [t for t in self._tombstones if t.subject_id == subject_id]

    def remaining_records(self, subject_id: str) -> int:
        return sum(
            1
            for recs in self._records.values()
            for r in recs
            if r.subject_id == subject_id
        )
