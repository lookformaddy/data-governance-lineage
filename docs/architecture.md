# Architecture — data-governance-lineage

The governance control plane that sits *underneath* a data platform: the
catalog knows what exists, the lineage graph knows how data flows, the policy
engine decides who may touch what, and the privacy/quality/workflow modules
turn those facts into enforceable operations.

```
                        +------------------+
                        |   CLI / FastAPI  |   (cli.py / service.py — thin adapters)
                        +--------+---------+
                                 |
                    +------------+------------+
                    |   GovernancePlatform    |   (platform.py — facade, one shared view)
                    +------------+------------+
                                 |
        +------------+-----------+------------+------------+-----------+
        |            |           |            |            |           |
   +----+----+  +----+----+ +----+-----+ +----+-----+ +----+----+ +----+----+
   | Catalog |  | Lineage | | Policies | | Privacy  | | Quality | |Workflows|
   +----+----+  +----+----+ +----+-----+ +----+-----+ +----+----+ +----+----+
        |            |
   datasets      edges (dataset + column level)
   schemas       impact paths, cycles, exports
   versions
```

## Module responsibilities

| Module | Owns | Never does |
|---|---|---|
| `models.py` | Domain types + errors. Stdlib only. | I/O, business rules |
| `catalog.py` | Registration, validation, discovery, schema-version history, retention reporting | Lineage or policy decisions |
| `lineage.py` | Edge store, BFS traversal (dataset + column level), impact paths, cycle detection, topological order, DOT/Mermaid export | Catalog validation (graph is standalone) |
| `policies.py` | Role × classification matrix, action scoping, append-only audit log, break-glass grants | Storage-layer enforcement (see trade-offs) |
| `privacy.py` | PII tagging, masking rules, subject-data store, SAR manifests, erasure lifecycle with tombstones | Cross-system deletion fan-out |
| `quality.py` | Check registry, result recording, taint recomputation, blast-radius impact reports | Running the checks themselves |
| `workflows.py` | Certification state machine + checklist gate, schema-change review queue, steward directory | Applying schemas directly (goes through catalog) |
| `platform.py` | Composes all subsystems; `summary()` health view | Any domain logic |
| `demo.py` | Builds the synthetic estate; `run_demo()` narrative | Production wiring |
| `service.py` | FastAPI routes (optional dep) | Business logic |
| `cli.py` | argparse commands over a demo platform | Business logic |

## Key flows

### 1. Dataset onboarding → certification
```
register() → propose() → submit_for_review() → certify() [checklist gate] → CERTIFIED
```
The checklist (steward, retention, PII review, passing quality checks) is
evaluated at certify time, not stored as a flag — evidence is re-checked on
every transition, so a dataset that loses its steward cannot be re-certified
until the gap is fixed.

### 2. Schema change
```
SchemaChangeQueue.submit() → OPEN → approve() → catalog.apply_schema() (new version recorded)
                                    reject()  → REJECTED (reason required)
```
Schemas are immutable in the catalog except through an approved request; every
change appends to the schema-version history. This is the same pattern as
migration-based DDL review in real platforms.

### 3. Access decision
```
evaluate(actor, role, dataset, action)
  1. valid break-glass grant? → ALLOW (logged, flagged)
  2. role in action's allowed roles? else DENY
  3. classification ≤ role clearance? else DENY
  4. ALLOW — always appended to the audit log
```
There is no code path that grants access without writing an audit event.

### 4. Quality failure → taint
```
record_result(passed=False) → recompute: failed ∪ downstream(failed) → TAINTED
impact_report() → per-dataset {hops, path, owner, steward, classification} for notification
resolve() → recompute → taint clears
```
Taint is *derived* from check results + lineage on every write, never a stored
flag an operator can forget to clear.

### 5. Erasure request
```
request_erasure() → OPEN → execute_erasure() → per-dataset record removal
                                              + Tombstone(subject, dataset, count)
                                              → COMPLETED (idempotent)
```
Tombstones are the audit proof: they record that N records were removed from
dataset D at time T, without retaining the removed values.

## Data model (logical)

- **Dataset**: qualified name (`domain.layer.name`), kind, domain, owner,
  steward, classification, schema (columns with PII flags), certification
  status, retention days, tags, schema-version history.
- **LineageEdge**: upstream → downstream + transform description + optional
  `downstream_col → upstream_col` mapping (the evidence for column lineage).
- **AuditEvent**: append-only; every policy decision and break-glass lifecycle
  event lands here.
- **BreakGlassGrant**: actor + dataset + justification + expiry; evaluated
  before the matrix, never widens DELETE/CERTIFY/MANAGE_POLICY.
- **Tombstone**: erasure proof without retained values.

## Scaling notes (what changes in production)

- **Lineage graph**: in-memory adjacency → a graph store (or materialized
  closure table in the warehouse) once edges exceed ~10⁵; BFS becomes bounded
  traversal queries. Cycle detection moves to a periodic job, not per-write.
- **Audit log**: append-only list → immutable store (WORM/S3 + Athena, or a
  ledger DB); the "every decision is logged" invariant is preserved by making
  the logger the single choke point (as it is here).
- **Lineage capture**: declared edges → parser-based extractors
  (sqlglot/OpenLineage events from Spark/dbt/Airflow) merged with manual
  declarations for non-SQL flows.
- **Policy enforcement**: evaluation here is the *decision* function; in
  production the same function backs a query-rewrite / view layer, because
  application-level checks cannot stop direct warehouse access.
- **Taint propagation**: eager recompute → incremental propagation or a
  materialized downstream-closure, same semantics.

## Diagram description (lineage of the demo estate)

```
marketing.raw.clickstream ──sessionize──▶ marketing.cleaned.sessions ─┬─last-touch attribution──▶ analytics.mart.marketing_attribution ◀──revenue join── orders.curated.fact_orders
                                                                      └─session features───────▶ analytics.mart.customer_360 ◀──customer agg── orders.curated.fact_orders ◀──dedupe+fraud── orders.raw.order_events
supply.raw.inventory_snapshots ──daily rollup──▶ supply.curated.inventory_daily
```

Render it live: `data-governance-lineage export --format mermaid` (paste into
any Mermaid renderer) or `--format dot` (Graphviz).
