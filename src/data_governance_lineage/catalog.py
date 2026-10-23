"""Dataset catalog: registration, discovery, schema-version history, retention reporting.

The catalog is the system of record for *what data exists*. Lineage, policies,
privacy, and quality all resolve datasets through it, which is why registration
validates qualified names, non-empty schemas, and ownership up front.
"""

from __future__ import annotations

from dataclasses import dataclass

from data_governance_lineage.models import (
    CertificationStatus,
    Classification,
    Column,
    Dataset,
    DatasetNotFound,
    DuplicateDataset,
    GovernanceError,
    utcnow,
)


@dataclass
class SchemaVersion:
    version: int
    dataset: str
    columns: list[str]
    change_note: str
    applied_at: object  # datetime
    requested_by: str


class Catalog:
    def __init__(self) -> None:
        self._datasets: dict[str, Dataset] = {}
        self._schema_history: dict[str, list[SchemaVersion]] = {}

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, dataset: Dataset, overwrite: bool = False) -> Dataset:
        self._validate(dataset)
        if dataset.name in self._datasets and not overwrite:
            raise DuplicateDataset(f"Dataset already registered: {dataset.name}")
        self._datasets[dataset.name] = dataset
        self._schema_history.setdefault(dataset.name, []).append(
            SchemaVersion(
                version=len(self._schema_history.get(dataset.name, [])) + 1,
                dataset=dataset.name,
                columns=dataset.column_names(),
                change_note="initial registration",
                applied_at=utcnow(),
                requested_by=dataset.owner,
            )
        )
        return dataset

    @staticmethod
    def _validate(dataset: Dataset) -> None:
        if not dataset.name or "." not in dataset.name:
            raise GovernanceError(
                f"Dataset name must be qualified (e.g. 'orders.raw.events'), got {dataset.name!r}"
            )
        if not dataset.owner or not dataset.owner.strip():
            raise GovernanceError(f"Dataset {dataset.name!r} requires an owner")
        if not dataset.domain or not dataset.domain.strip():
            raise GovernanceError(f"Dataset {dataset.name!r} requires a domain")
        if not dataset.schema:
            raise GovernanceError(f"Dataset {dataset.name!r} requires a non-empty schema")
        names = [c.name for c in dataset.schema]
        if len(set(names)) != len(names):
            raise GovernanceError(f"Dataset {dataset.name!r} has duplicate column names")
        if dataset.retention_days is not None and dataset.retention_days <= 0:
            raise GovernanceError("retention_days must be positive when set")

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get(self, name: str) -> Dataset:
        try:
            return self._datasets[name]
        except KeyError:
            raise DatasetNotFound(f"Unknown dataset: {name}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._datasets

    def list(
        self,
        *,
        domain: str | None = None,
        classification: Classification | None = None,
        certification: CertificationStatus | None = None,
        tag: str | None = None,
        owner: str | None = None,
    ) -> list[Dataset]:
        results = list(self._datasets.values())
        if domain is not None:
            results = [d for d in results if d.domain == domain]
        if classification is not None:
            results = [d for d in results if d.classification is classification]
        if certification is not None:
            results = [d for d in results if d.certification is certification]
        if tag is not None:
            results = [d for d in results if tag in d.tags]
        if owner is not None:
            results = [d for d in results if d.owner == owner]
        return sorted(results, key=lambda d: d.name)

    def search(self, query: str) -> list[Dataset]:
        q = query.lower()
        return sorted(
            (
                d
                for d in self._datasets.values()
                if q in d.name.lower() or q in d.domain.lower() or any(q in t.lower() for t in d.tags)
            ),
            key=lambda d: d.name,
        )

    def datasets_owned_by(self, owner: str) -> list[Dataset]:
        return self.list(owner=owner)

    # ------------------------------------------------------------------ #
    # Schema evolution (applied only through the review queue)
    # ------------------------------------------------------------------ #
    def apply_schema(self, name: str, columns: list[Column], change_note: str, requested_by: str) -> Dataset:
        dataset = self.get(name)
        names = [c.name for c in columns]
        if len(set(names)) != len(names):
            raise GovernanceError(f"Proposed schema for {name!r} has duplicate column names")
        dataset.schema = list(columns)
        dataset.updated_at = utcnow()
        history = self._schema_history.setdefault(name, [])
        history.append(
            SchemaVersion(
                version=len(history) + 1,
                dataset=name,
                columns=names,
                change_note=change_note,
                applied_at=utcnow(),
                requested_by=requested_by,
            )
        )
        return dataset

    def schema_history(self, name: str) -> list[SchemaVersion]:
        self.get(name)  # raise if unknown
        return list(self._schema_history.get(name, []))

    # ------------------------------------------------------------------ #
    # Governance metadata updates
    # ------------------------------------------------------------------ #
    def set_steward(self, name: str, steward: str) -> Dataset:
        dataset = self.get(name)
        dataset.steward = steward
        dataset.updated_at = utcnow()
        return dataset

    def set_certification(self, name: str, status: CertificationStatus) -> Dataset:
        dataset = self.get(name)
        dataset.certification = status
        dataset.updated_at = utcnow()
        return dataset

    def set_retention(self, name: str, retention_days: int) -> Dataset:
        if retention_days <= 0:
            raise GovernanceError("retention_days must be positive")
        dataset = self.get(name)
        dataset.retention_days = retention_days
        dataset.updated_at = utcnow()
        return dataset

    def mark_pii_reviewed(self, name: str) -> Dataset:
        dataset = self.get(name)
        dataset.pii_reviewed = True
        dataset.updated_at = utcnow()
        return dataset

    def retention_report(self) -> list[dict]:
        """Retention posture across the estate: what is kept, for how long."""
        report = []
        for d in sorted(self._datasets.values(), key=lambda d: d.name):
            report.append(
                {
                    "dataset": d.name,
                    "classification": d.classification.name,
                    "retention_days": d.retention_days,
                    "retention_set": d.retention_days is not None,
                    "owner": d.owner,
                }
            )
        return report

    def stats(self) -> dict:
        by_domain: dict[str, int] = {}
        by_classification: dict[str, int] = {}
        by_certification: dict[str, int] = {}
        for d in self._datasets.values():
            by_domain[d.domain] = by_domain.get(d.domain, 0) + 1
            by_classification[d.classification.name] = by_classification.get(d.classification.name, 0) + 1
            by_certification[d.certification.name] = by_certification.get(d.certification.name, 0) + 1
        return {
            "datasets": len(self._datasets),
            "by_domain": by_domain,
            "by_classification": by_classification,
            "by_certification": by_certification,
        }
