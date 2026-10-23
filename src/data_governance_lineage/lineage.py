"""Lineage graph: dataset- and column-level provenance, impact analysis, exports.

The graph stores directed edges ``upstream -> downstream`` with an optional
column mapping (``downstream_column -> upstream_column``) so both dataset-level
and column-level traversal are supported from the same structure.

Declared transforms are accepted as-is (this is a governance catalog, not a
SQL parser); cycle detection and execution ordering operate on the declared
graph. In production this graph would be populated by parser-based extractors
(sqlglot/OpenLineage) plus manual declarations for non-SQL flows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from data_governance_lineage.models import GovernanceError


@dataclass
class LineageEdge:
    upstream: str
    downstream: str
    transform: str
    column_map: dict[str, str] = field(default_factory=dict)  # downstream_col -> upstream_col


class LineageCycleError(GovernanceError):
    pass


class LineageGraph:
    def __init__(self) -> None:
        self._edges: list[LineageEdge] = []
        self._downstream_of: dict[str, list[LineageEdge]] = {}
        self._upstream_of: dict[str, list[LineageEdge]] = {}

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def add_edge(
        self,
        upstream: str,
        downstream: str,
        transform: str,
        column_map: dict[str, str] | None = None,
    ) -> LineageEdge:
        if not upstream or not downstream:
            raise GovernanceError("Lineage edge endpoints must be non-empty dataset names")
        if upstream == downstream:
            raise GovernanceError(f"Self-loop lineage edge rejected for {upstream!r}")
        if not transform or not transform.strip():
            raise GovernanceError("Lineage edge requires a transform description")
        edge = LineageEdge(upstream, downstream, transform.strip(), dict(column_map or {}))
        self._edges.append(edge)
        self._downstream_of.setdefault(upstream, []).append(edge)
        self._upstream_of.setdefault(downstream, []).append(edge)
        return edge

    def remove_edge(self, upstream: str, downstream: str) -> bool:
        before = len(self._edges)
        self._edges = [e for e in self._edges if not (e.upstream == upstream and e.downstream == downstream)]
        self._rebuild_indexes()
        return len(self._edges) < before

    def _rebuild_indexes(self) -> None:
        self._downstream_of = {}
        self._upstream_of = {}
        for e in self._edges:
            self._downstream_of.setdefault(e.upstream, []).append(e)
            self._upstream_of.setdefault(e.downstream, []).append(e)

    def datasets(self) -> list[str]:
        names: set[str] = set()
        for e in self._edges:
            names.add(e.upstream)
            names.add(e.downstream)
        return sorted(names)

    def edge_count(self) -> int:
        return len(self._edges)

    # ------------------------------------------------------------------ #
    # Dataset-level traversal (BFS with hop distances)
    # ------------------------------------------------------------------ #
    def upstream(self, dataset: str, depth: int | None = None) -> dict[str, int]:
        """All ancestors of ``dataset`` mapped to their hop distance."""
        return self._bfs(dataset, self._upstream_of, lambda e: e.upstream, depth)

    def downstream(self, dataset: str, depth: int | None = None) -> dict[str, int]:
        """All descendants of ``dataset`` mapped to their hop distance."""
        return self._bfs(dataset, self._downstream_of, lambda e: e.downstream, depth)

    @staticmethod
    def _bfs(
        start: str,
        adjacency: dict[str, list[LineageEdge]],
        neighbor: object,
        depth: int | None,
    ) -> dict[str, int]:
        distances: dict[str, int] = {}
        queue: list[tuple[str, int]] = [(start, 0)]
        seen = {start}
        while queue:
            node, dist = queue.pop(0)
            if depth is not None and dist >= depth:
                continue
            for edge in adjacency.get(node, []):
                nxt = neighbor(edge)  # type: ignore[operator]
                if nxt not in seen:
                    seen.add(nxt)
                    distances[nxt] = dist + 1
                    queue.append((nxt, dist + 1))
        return distances

    # ------------------------------------------------------------------ #
    # Column-level traversal
    # ------------------------------------------------------------------ #
    def upstream_columns(self, dataset: str, column: str, depth: int | None = None) -> dict[tuple[str, str], int]:
        return self._column_bfs(dataset, column, upstream=True, depth=depth)

    def downstream_columns(self, dataset: str, column: str, depth: int | None = None) -> dict[tuple[str, str], int]:
        return self._column_bfs(dataset, column, upstream=False, depth=depth)

    def _column_bfs(
        self, dataset: str, column: str, *, upstream: bool, depth: int | None
    ) -> dict[tuple[str, str], int]:
        """BFS over (dataset, column) nodes following declared column mappings.

        Edges without a column mapping are not traversed at column granularity
        (the mapping is the evidence for column provenance).
        """
        distances: dict[tuple[str, str], int] = {}
        queue: list[tuple[str, str, int]] = [(dataset, column, 0)]
        seen = {(dataset, column)}
        while queue:
            ds, col, dist = queue.pop(0)
            if depth is not None and dist >= depth:
                continue
            edges = self._upstream_of.get(ds, []) if upstream else self._downstream_of.get(ds, [])
            for edge in edges:
                nxt = self._mapped_column(edge, ds, col, upstream=upstream)
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    distances[nxt] = dist + 1
                    queue.append((nxt[0], nxt[1], dist + 1))
        return distances

    @staticmethod
    def _mapped_column(
        edge: LineageEdge, dataset: str, column: str, *, upstream: bool
    ) -> tuple[str, str] | None:
        if not edge.column_map:
            return None
        if upstream:
            # edge: other -> dataset ; column_map is downstream_col -> upstream_col
            if dataset == edge.downstream and column in edge.column_map:
                return (edge.upstream, edge.column_map[column])
            return None
        # downstream direction: edge: dataset -> other ; find downstream cols fed by `column`
        if dataset == edge.upstream:
            for down_col, up_col in edge.column_map.items():
                if up_col == column:
                    return (edge.downstream, down_col)
        return None

    # ------------------------------------------------------------------ #
    # Impact analysis
    # ------------------------------------------------------------------ #
    def impact_paths(self, root: str, max_depth: int = 6, max_paths: int = 100) -> list[list[str]]:
        """All downstream paths from ``root`` (DFS, capped to stay tractable)."""
        paths: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            if len(paths) >= max_paths:
                return
            if len(path) - 1 >= max_depth:
                return
            for edge in self._downstream_of.get(node, []):
                if edge.downstream in path:  # avoid looping on cycles
                    continue
                new_path = path + [edge.downstream]
                paths.append(new_path)
                dfs(edge.downstream, new_path)

        dfs(root, [root])
        return paths

    def impact_report(self, root: str) -> list[dict]:
        """Downstream impact summary: dataset, hops, and one representative path."""
        downstream = self.downstream(root)
        paths = self.impact_paths(root)
        first_path: dict[str, list[str]] = {}
        for p in paths:
            for node in p[1:]:
                first_path.setdefault(node, p)
        return [
            {"dataset": ds, "hops": hops, "path": first_path.get(ds, [root, ds])}
            for ds, hops in sorted(downstream.items(), key=lambda kv: (kv[1], kv[0]))
        ]

    # ------------------------------------------------------------------ #
    # Structural analysis
    # ------------------------------------------------------------------ #
    def detect_cycles(self) -> list[list[str]]:
        """Return all simple cycles found via iterative DFS (dataset level)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {d: WHITE for d in self.datasets()}
        cycles: list[list[str]] = []

        def visit(node: str, stack: list[str]) -> None:
            color[node] = GRAY
            stack.append(node)
            for edge in self._downstream_of.get(node, []):
                nxt = edge.downstream
                if color.get(nxt, WHITE) == GRAY:
                    cycle = stack[stack.index(nxt):] + [nxt]
                    cycles.append(cycle)
                elif color.get(nxt, WHITE) == WHITE:
                    visit(nxt, stack)
            stack.pop()
            color[node] = BLACK

        for node in self.datasets():
            if color[node] == WHITE:
                visit(node, [])
        return cycles

    def execution_order(self) -> list[str]:
        """Topological order (Kahn's algorithm) — safe backfill/recompute sequence.

        Raises LineageCycleError when the graph contains a cycle.
        """
        indegree: dict[str, int] = {d: 0 for d in self.datasets()}
        for e in self._edges:
            indegree[e.downstream] = indegree.get(e.downstream, 0) + 1
        queue = sorted([d for d, deg in indegree.items() if deg == 0])
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for edge in sorted(self._downstream_of.get(node, []), key=lambda e: e.downstream):
                indegree[edge.downstream] -= 1
                if indegree[edge.downstream] == 0:
                    queue.append(edge.downstream)
            queue.sort()
        if len(order) != len(indegree):
            raise LineageCycleError("Cannot order execution: lineage graph contains a cycle")
        return order

    # ------------------------------------------------------------------ #
    # Exports
    # ------------------------------------------------------------------ #
    def to_dot(self) -> str:
        lines = ["digraph lineage {", '  rankdir=LR;']
        for e in self._edges:
            label = e.transform.replace('"', "'")
            lines.append(f'  "{e.upstream}" -> "{e.downstream}" [label="{label}"];')
        lines.append("}")
        return "\n".join(lines)

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for e in self._edges:
            label = e.transform.replace('"', "'").replace("|", "/")
            lines.append(f'  {self._mermaid_id(e.upstream)}["{e.upstream}"] -->|"{label}"| {self._mermaid_id(e.downstream)}["{e.downstream}"]')
        return "\n".join(lines)

    @staticmethod
    def _mermaid_id(name: str) -> str:
        return "n_" + "".join(ch if ch.isalnum() else "_" for ch in name)
