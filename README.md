# data-governance-lineage

The governance control plane enterprises put **underneath** their data platform:
a dataset catalog, a column-level lineage graph, a role × classification access-policy
engine with an audit trail and break-glass, a certification lifecycle, schema-change
reviews, PII/privacy handling, and lineage-aware data quality. All data is synthetic;
the core engine is pure standard library.

## The enterprise problem

Past a few hundred datasets, "who owns this table, where did this column come from,
who is allowed to see it, and what breaks if the upstream feed is late" stops being
answerable from tribal knowledge. Audits (SOC 2, GDPR) demand evidence: every access
decision logged, every erasure proven, every certified dataset backed by a checklist.
This project implements that control plane as runnable code.

## What it does

- **Dataset catalog** — registration with qualified names, schema, owner, domain,
  classification (`PUBLIC / INTERNAL / CONFIDENTIAL / RESTRICTED`), retention policy;
  search, filtering, schema-version history, retention reporting.
- **Lineage graph** — dataset- *and* column-level edges (declared transforms with
  column mappings), upstream/downstream BFS with hop distances, column provenance
  (`customer_360.email` ← `fact_orders.email` ← `order_events.email`), impact paths,
  cycle detection, topological execution order, DOT and Mermaid export.
- **Access policies** — role × classification clearance matrix plus action scoping
  (analysts read-only, only admins delete, only stewards/admins certify); every
  evaluation appended to an immutable audit log; **break-glass** grants temporary,
  justified, expiring access for incidents (never widens delete/certify).
- **Governance workflows** — certification lifecycle
  `PROPOSED → UNDER_REVIEW → CERTIFIED` (deprecate from anywhere) gated by a real
  checklist: steward assigned, retention set, PII review done, quality checks passing.
  Schema changes go through a review queue (approve applies + versions, reject needs
  a reason). Steward directory per domain.
- **Privacy** — PII column tagging, classification-aware masking rules (email/phone/
  generic, format-preserving where it matters), subject access request manifests,
  erasure requests with **record-level tombstones** as audit proof.
- **Lineage-aware quality** — failed upstream check *taints* all downstream datasets
  (taint is recomputed from results + lineage, never a stale flag); `impact_report()`
  produces the incident "who to notify" page: hops, path, owner, steward,
  classification per affected dataset.

## Architecture

See [docs/architecture.md](docs/architecture.md) for module responsibilities, the five
key flows (onboarding → certification, schema change, access decision, quality → taint,
erasure), the data model, and production scaling notes.

```
CLI / FastAPI → GovernancePlatform (facade) → catalog · lineage · policies ·
                                              privacy · quality · workflows
```

## Quick start

```bash
pip install -r requirements.txt   # fastapi/uvicorn optional; core is stdlib-only
pip install -e .

# Run the end-to-end demo (synthetic estate: 8 datasets, 7 lineage edges)
python examples/run_demo.py        # or: make demo

# Explore
data-governance-lineage catalog
data-governance-lineage downstream orders.curated.fact_orders
data-governance-lineage impact orders.raw.order_events
data-governance-lineage export --format mermaid
data-governance-lineage audit --limit 20

# API (needs fastapi/uvicorn)
data-governance-lineage serve --port 8000   # or: make serve
```

## Tests

```bash
make test   # PYTHONPATH=src python3 -m pytest -q
```

62 tests across lineage traversal (dataset + column level, cycles, exports),
policy decisions (allow/deny/break-glass/expiry/revocation), certification
checklist gating, schema-review approve/reject, erasure with tombstones, taint
propagation and impact reports. API tests auto-skip when FastAPI isn't installed.

## Key design decisions and trade-offs

1. **Declared lineage, not parsed SQL.** The graph accepts declared transforms with
   column mappings — the same seam where production systems plug in sqlglot/
   OpenLineage extractors. Parsing was deliberately out of scope; the traversal,
   impact, and taint semantics are identical either way.
2. **Taint is derived, never stored.** Recomputed from check results + lineage on
   every write, so it cannot go stale. At very large scale this becomes incremental
   propagation or a materialized closure table — same semantics, noted in the arch doc.
3. **Every policy decision is audited.** There is no access path that skips the audit
   log; break-glass evaluations are flagged, justified, and expiring.
4. **Policy evaluation ≠ storage enforcement.** This engine is the *decision*
   function; in production the same function backs a query-rewrite/view layer, since
   application-level checks alone can't stop direct warehouse access.
5. **Certification is evidence-gated, not a button.** The checklist is re-evaluated
   at transition time (steward, retention, PII review, green quality checks), so a
   dataset can't stay "certified" after its evidence rots.
6. **Tombstones, not just deletes.** Erasure writes per-dataset tombstones (subject,
   dataset, count, timestamp) — the audit proof regulators ask for — without
   retaining the erased values.
7. **Stdlib core, optional web.** Zero third-party deps in the engine; FastAPI is an
   extra for the HTTP surface only, and its tests skip cleanly without it.

## What this demonstrates

For a Director-level data-platform interview: how to design a governance control
plane (catalog → lineage → policy → privacy → quality), why taint must be derived
from lineage rather than flagged manually, how break-glass balances incident velocity
with auditability, and where a demo-scale design breaks (graph stores, immutable
audit ledgers, parser-based lineage capture, query-layer enforcement) — all called
out explicitly in `docs/architecture.md`.

## CI

`docs/github-actions-ci.yml` is a ready-to-use GitHub Actions workflow (tests on
3.11/3.12, demo smoke test, Docker build). **To activate it, move it to
`.github/workflows/ci.yml`** — it lives under `docs/` because the GitHub App
integration used for publishing lacks the Workflows permission.

## Clean-room note

All code is original and all data is synthetic (invented e-commerce/marketing/
supply-chain domains). No employer code, data, or internal designs are included.
