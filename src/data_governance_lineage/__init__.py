"""data-governance-lineage: enterprise data governance, lineage, and access-policy platform.

This package implements the catalog + lineage graph + policy enforcement layer that
enterprises place underneath their data platforms:

- ``models``   — shared domain types (datasets, columns, classifications, audit events)
- ``catalog``  — dataset registration, search, schema-version history
- ``lineage``  — dataset- and column-level lineage graph, impact analysis, exports
- ``policies`` — role x classification access matrix, audit trail, break-glass grants
- ``privacy``  — PII tagging, masking rules, subject access/erasure request handling
- ``quality``  — lineage-aware quality: taint propagation and downstream impact reports
- ``workflows``— certification lifecycle, schema-change review queue, steward assignment
- ``platform`` — facade composing every subsystem into one ``GovernancePlatform``
- ``demo``     — builds a fully synthetic demo environment
- ``service``  — optional FastAPI surface (importable only when FastAPI is installed)
- ``cli``      — command-line interface
"""

from data_governance_lineage.models import (
    Action,
    AuditEvent,
    BreakGlassGrant,
    CertificationStatus,
    Classification,
    Column,
    Dataset,
    DatasetKind,
    Decision,
    ErasureRequest,
    ErasureStatus,
    QualityCheck,
    Role,
    SchemaChangeRequest,
    SchemaChangeStatus,
    SubjectRecord,
    Tombstone,
)
from data_governance_lineage.platform import GovernancePlatform

__all__ = [
    "Action",
    "AuditEvent",
    "BreakGlassGrant",
    "CertificationStatus",
    "Classification",
    "Column",
    "Dataset",
    "DatasetKind",
    "Decision",
    "ErasureRequest",
    "ErasureStatus",
    "GovernancePlatform",
    "QualityCheck",
    "Role",
    "SchemaChangeRequest",
    "SchemaChangeStatus",
    "SubjectRecord",
    "Tombstone",
]

__version__ = "0.1.0"
