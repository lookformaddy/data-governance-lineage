"""GovernancePlatform: facade composing every governance subsystem.

One object wires the catalog, lineage graph, policy engine, privacy service,
quality monitor, certification lifecycle, schema-change queue, and steward
directory together so the demo, CLI, API, and tests all share one consistent
view of the estate.
"""

from __future__ import annotations

from data_governance_lineage.catalog import Catalog
from data_governance_lineage.lineage import LineageGraph
from data_governance_lineage.policies import PolicyEngine
from data_governance_lineage.privacy import PrivacyService
from data_governance_lineage.quality import QualityMonitor
from data_governance_lineage.workflows import (
    CertificationLifecycle,
    SchemaChangeQueue,
    StewardDirectory,
)


class GovernancePlatform:
    def __init__(self) -> None:
        self.catalog = Catalog()
        self.lineage = LineageGraph()
        self.policies = PolicyEngine()
        self.privacy = PrivacyService(self.catalog)
        self.quality = QualityMonitor(self.catalog, self.lineage)
        self.certification = CertificationLifecycle(self.catalog, self.quality)
        self.schema_reviews = SchemaChangeQueue(self.catalog)
        self.stewards = StewardDirectory(self.catalog)

    def summary(self) -> dict:
        return {
            "catalog": self.catalog.stats(),
            "lineage_edges": self.lineage.edge_count(),
            "audit": self.policies.audit_summary(),
            "quality": self.quality.quality_summary(),
            "open_schema_reviews": len(self.schema_reviews.open_requests()),
        }
