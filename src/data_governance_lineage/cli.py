"""Command-line interface for the governance platform."""

from __future__ import annotations

import argparse
import json
import os

from data_governance_lineage.demo import build_demo_platform, run_demo
from data_governance_lineage.service import FASTAPI_AVAILABLE


def _platform():
    return build_demo_platform()


def cmd_demo(_args: argparse.Namespace) -> int:
    print(run_demo())
    return 0


def cmd_catalog(_args: argparse.Namespace) -> int:
    p = _platform()
    for d in p.catalog.list():
        print(
            f"{d.name:45s} {d.domain:14s} {d.classification.name:13s} "
            f"{d.certification.name:12s} owner={d.owner} steward={d.steward}"
        )
    return 0


def cmd_upstream(args: argparse.Namespace) -> int:
    p = _platform()
    result = p.lineage.upstream(args.dataset, args.depth)
    print(json.dumps({"dataset": args.dataset, "upstream": result}, indent=2))
    return 0


def cmd_downstream(args: argparse.Namespace) -> int:
    p = _platform()
    result = p.lineage.downstream(args.dataset, args.depth)
    print(json.dumps({"dataset": args.dataset, "downstream": result}, indent=2))
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    p = _platform()
    report = p.quality.impact_report(args.dataset)
    print(f"Blast radius of {args.dataset}: {report['impacted_count']} downstream dataset(s)")
    for imp in report["impacted"]:
        print(
            f"  [{' -> '.join(imp['path'])}]\n"
            f"    owner={imp['owner']} steward={imp['steward']} "
            f"classification={imp['classification']} certification={imp['certification']}"
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    p = _platform()
    if args.format == "dot":
        print(p.lineage.to_dot())
    else:
        print(p.lineage.to_mermaid())
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    p = _platform()
    for e in p.policies.audit_log(limit=args.limit):
        bg = " [BREAK-GLASS]" if e.break_glass else ""
        print(f"#{e.event_id:04d} {e.timestamp.isoformat()} {e.actor} {e.action} "
              f"{e.dataset} -> {e.decision.value.upper()}{bg} :: {e.reason}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    if not FASTAPI_AVAILABLE:
        print("FastAPI is not installed. Install with: pip install 'data-governance-lineage[api]'")
        return 1
    from data_governance_lineage.service import serve

    serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data-governance-lineage", description="Data governance & lineage platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Run the end-to-end synthetic demo").set_defaults(func=cmd_demo)
    sub.add_parser("catalog", help="List registered datasets").set_defaults(func=cmd_catalog)

    upstream = sub.add_parser("upstream", help="Show upstream lineage of a dataset")
    upstream.add_argument("dataset")
    upstream.add_argument("--depth", type=int, default=None)
    upstream.set_defaults(func=cmd_upstream)

    downstream = sub.add_parser("downstream", help="Show downstream lineage of a dataset")
    downstream.add_argument("dataset")
    downstream.add_argument("--depth", type=int, default=None)
    downstream.set_defaults(func=cmd_downstream)

    impact = sub.add_parser("impact", help="Blast-radius report for a dataset")
    impact.add_argument("dataset")
    impact.set_defaults(func=cmd_impact)

    export = sub.add_parser("export", help="Export the lineage graph")
    export.add_argument("--format", choices=["dot", "mermaid"], default="mermaid")
    export.set_defaults(func=cmd_export)

    audit = sub.add_parser("audit", help="Show the policy audit trail")
    audit.add_argument("--limit", type=int, default=20)
    audit.set_defaults(func=cmd_audit)

    serve_p = sub.add_parser("serve", help="Start the FastAPI service")
    serve_p.add_argument("--host", default=os.environ.get("DGL_API_HOST", "127.0.0.1"))
    serve_p.add_argument("--port", type=int, default=int(os.environ.get("DGL_API_PORT", "8000")))
    serve_p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
