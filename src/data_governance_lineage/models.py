"""Shared domain types for the governance platform.

Everything is plain dataclasses + enums on the standard library so the core
engine has zero third-party dependencies (FastAPI is an optional extra for
the HTTP surface only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Classification(Enum):
    """Data sensitivity tiers. Higher value = more sensitive."""

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3


class DatasetKind(Enum):
    TABLE = "table"
    STREAM = "stream"
    FILE = "file"


class CertificationStatus(Enum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    CERTIFIED = "certified"
    DEPRECATED = "deprecated"


class Role(Enum):
    ADMIN = "admin"
    STEWARD = "steward"
    DATA_ENGINEER = "data_engineer"
    ANALYST = "analyst"
    SERVICE_ACCOUNT = "service_account"
    EXTERNAL = "external"


class Action(Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    CERTIFY = "certify"
    MANAGE_POLICY = "manage_policy"
    ASSIGN_STEWARD = "assign_steward"


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"


class SchemaChangeStatus(Enum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"


class ErasureStatus(Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Column:
    name: str
    dtype: str
    description: str = ""
    pii: bool = False
    nullable: bool = True


@dataclass
class Dataset:
    """A registered data asset: table, stream, or file."""

    name: str  # qualified, e.g. "orders.curated.fact_orders"
    kind: DatasetKind
    domain: str
    owner: str
    classification: Classification
    schema: list[Column] = field(default_factory=list)
    steward: str | None = None
    certification: CertificationStatus = CertificationStatus.PROPOSED
    retention_days: int | None = None
    tags: list[str] = field(default_factory=list)
    pii_reviewed: bool = False
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def column_names(self) -> list[str]:
        return [c.name for c in self.schema]

    def get_column(self, name: str) -> Column | None:
        for col in self.schema:
            if col.name == name:
                return col
        return None

    def pii_columns(self) -> list[str]:
        return [c.name for c in self.schema if c.pii]


@dataclass
class AuditEvent:
    event_id: int
    timestamp: datetime
    actor: str
    action: str
    dataset: str
    decision: Decision
    reason: str
    break_glass: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class BreakGlassGrant:
    grant_id: str
    actor: str
    dataset: str
    justification: str
    granted_at: datetime
    expires_at: datetime
    revoked: bool = False

    def is_valid(self, at: datetime | None = None) -> bool:
        now = at or utcnow()
        return not self.revoked and self.granted_at <= now < self.expires_at


@dataclass
class SubjectRecord:
    """One synthetic record belonging to a data subject, stored per dataset."""

    dataset: str
    subject_id: str
    fields: dict


@dataclass
class Tombstone:
    """Proof that a subject's records were erased from a dataset."""

    subject_id: str
    dataset: str
    erased_count: int
    erased_at: datetime
    requested_by: str


@dataclass
class ErasureRequest:
    request_id: str
    subject_id: str
    status: ErasureStatus
    requested_by: str
    created_at: datetime = field(default_factory=utcnow)
    completed_at: datetime | None = None
    per_dataset_counts: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class QualityCheck:
    name: str
    dataset: str
    description: str = ""
    last_passed: bool | None = None
    last_run: datetime | None = None
    consecutive_failures: int = 0


@dataclass
class SchemaChangeRequest:
    request_id: str
    dataset: str
    proposed_columns: list[Column]
    requested_by: str
    status: SchemaChangeStatus = SchemaChangeStatus.OPEN
    reviewer: str | None = None
    reason: str = ""
    created_at: datetime = field(default_factory=utcnow)
    decided_at: datetime | None = None


class GovernanceError(Exception):
    """Base error for governance-platform rule violations."""


class DatasetNotFound(GovernanceError):
    pass


class DuplicateDataset(GovernanceError):
    pass


class InvalidTransition(GovernanceError):
    pass


class CertificationError(GovernanceError):
    pass


class PolicyDenied(GovernanceError):
    pass
