"""Lineage-aware data quality: taint propagation and downstream impact reports.

The key governance insight: a failed quality check on an upstream dataset
taints everything downstream until the failure is resolved. This module
recomputes taint from check results + the lineage graph (taint is derived,
never stored as a manual flag), and produces the "who do we notify" impact
report with owners and stewards for each affected dataset.

Trade-off: taint is recomputed eagerly on every result/resolve call. For
graphs with millions of edges this would move to incremental propagation or
a materialized closure table; for a control-plane service the eager
recompute keeps the semantics obvious and race-free.
"""

from __future__ import annotations

from data_governance_lineage.catalog import Catalog
from data_governance_lineage.lineage import LineageGraph
from data_governance_lineage.models import QualityCheck, utcnow


class QualityMonitor:
    def __init__(self, catalog: Catalog, lineage: LineageGraph) -> None:
        self._catalog = catalog
        self._lineage = lineage
        self._checks: dict[tuple[str, str], QualityCheck] = {}
        self._tainted: set[str] = set()
        self._taint_causes: dict[str, list[str]] = {}  # tainted dataset -> upstream failed checks

    # ------------------------------------------------------------------ #
    # Check registration and results
    # ------------------------------------------------------------------ #
    def register_check(self, dataset: str, check_name: str, description: str = "") -> QualityCheck:
        self._catalog.get(dataset)  # validate dataset exists
        key = (dataset, check_name)
        if key in self._checks:
            raise ValueError(f"Check already registered: {dataset}.{check_name}")
        check = QualityCheck(name=check_name, dataset=dataset, description=description)
        self._checks[key] = check
        self._recompute_taint()
        return check

    def record_result(self, dataset: str, check_name: str, passed: bool, details: str = "") -> QualityCheck:
        check = self._get(dataset, check_name)
        check.last_passed = passed
        check.last_run = utcnow()
        check.consecutive_failures = 0 if passed else check.consecutive_failures + 1
        if details:
            check.description = details
        self._recompute_taint()
        return check

    def resolve(self, dataset: str, check_name: str, resolution_note: str = "") -> QualityCheck:
        """Mark a check resolved (passing) with an audit note."""
        return self.record_result(dataset, check_name, True, details=resolution_note)

    def _get(self, dataset: str, check_name: str) -> QualityCheck:
        try:
            return self._checks[(dataset, check_name)]
        except KeyError:
            raise KeyError(f"Unknown check: {dataset}.{check_name}") from None

    def checks_for(self, dataset: str) -> list[QualityCheck]:
        return sorted(
            (c for (ds, _), c in self._checks.items() if ds == dataset),
            key=lambda c: c.name,
        )

    def all_passing(self, dataset: str) -> bool:
        checks = self.checks_for(dataset)
        return bool(checks) and all(c.last_passed for c in checks)

    # ------------------------------------------------------------------ #
    # Taint propagation
    # ------------------------------------------------------------------ #
    def _recompute_taint(self) -> None:
        failed = {ds for (ds, _), c in self._checks.items() if c.last_passed is False}
        tainted: set[str] = set(failed)
        causes: dict[str, list[str]] = {}
        for ds in failed:
            failing = [c.name for c in self.checks_for(ds) if c.last_passed is False]
            causes.setdefault(ds, []).extend(failing)
            for downstream_ds in self._lineage.downstream(ds):
                tainted.add(downstream_ds)
                causes.setdefault(downstream_ds, []).extend(failing)
        self._tainted = tainted
        self._taint_causes = {ds: sorted(set(v)) for ds, v in causes.items()}

    def tainted_datasets(self) -> list[str]:
        return sorted(self._tainted)

    def is_tainted(self, dataset: str) -> bool:
        return dataset in self._tainted

    def taint_causes(self, dataset: str) -> list[str]:
        return list(self._taint_causes.get(dataset, []))

    # ------------------------------------------------------------------ #
    # Downstream impact report: the "who do we notify" page
    # ------------------------------------------------------------------ #
    def impact_report(self, failed_dataset: str) -> dict:
        """Blast-radius report for a failed upstream dataset.

        Lists every downstream dataset with hop distance, owner, steward,
        classification, certification — everything an incident commander
        needs to notify stakeholders and gate consumers.
        """
        self._catalog.get(failed_dataset)  # validate
        impacted = []
        for item in self._lineage.impact_report(failed_dataset):
            ds = self._catalog.get(item["dataset"])
            impacted.append(
                {
                    "dataset": ds.name,
                    "hops": item["hops"],
                    "path": item["path"],
                    "owner": ds.owner,
                    "steward": ds.steward,
                    "classification": ds.classification.name,
                    "certification": ds.certification.name,
                    "taint_causes": self.taint_causes(ds.name),
                }
            )
        failing_checks = [c.name for c in self.checks_for(failed_dataset) if c.last_passed is False]
        return {
            "failed_dataset": failed_dataset,
            "failing_checks": failing_checks,
            "impacted_count": len(impacted),
            "impacted": impacted,
        }

    def quality_summary(self) -> dict:
        total = len(self._checks)
        passing = sum(1 for c in self._checks.values() if c.last_passed is True)
        failing = sum(1 for c in self._checks.values() if c.last_passed is False)
        never_run = total - passing - failing
        return {
            "checks": total,
            "passing": passing,
            "failing": failing,
            "never_run": never_run,
            "tainted_datasets": self.tainted_datasets(),
        }
