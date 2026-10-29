"""Command line: har2client build | ref | grade | solve."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from importlib import metadata
from pathlib import Path

from har2client import ops
from har2client.build_task import build_task
from har2client.grade import grade
from har2client.refclient import write_ref_client
from har2client.solve.envfile import load_env_key
from har2client.variants import NAMES_MODES, VariantError

EXIT_OK, EXIT_ERROR, EXIT_SKIPPED = 0, 1, 2
FETCH_TIMEOUT_S = 60
USER_AGENT = "har2client/0.1"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"  # har2client/.env, same file run_batch.py reads


def _load_spec(source: str) -> dict:
    if not source.startswith(("http://", "https://")):
        return ops.load_spec(source)
    request = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})  # APIs.guru rejects urllib's default
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310 - user-given URL
        data = resp.read()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "spec.txt"
        path.write_bytes(data)
        return ops.load_spec(path)


def _build(args: argparse.Namespace) -> int:
    try:
        spec = _load_spec(args.spec)
        task_dir = build_task(
            spec, args.api_id, args.out, noise=args.noise, seed=args.seed, names=args.names,
            twin=args.twin, closed_book=args.closed_book, rewordings_dir=args.rewordings_dir,
        )
    except ops.UnusableApi as why:
        print(f"skipped {args.api_id}: {why}", file=sys.stderr)
        return EXIT_SKIPPED
    except VariantError as why:
        print(f"error: {why}", file=sys.stderr)
        return EXIT_ERROR
    print(task_dir)
    return EXIT_OK


def _ref(args: argparse.Namespace) -> int:
    print(write_ref_client(args.task_dir))
    return EXIT_OK


def _grade(args: argparse.Namespace) -> int:
    result = grade(args.task_dir, args.client, timeout=args.timeout)
    out = Path(args.client).parent / "grade.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        f"pass_rate={result['pass_rate']:.3f} passed={result['passed']}/{result['total']} "
        f"failures={result['failure_counts']} invented_endpoint_calls={result['invented_endpoint_calls']} "
        f"hardcoded_secret={result['hardcoded_secret']} hardcoded_host={result['hardcoded_host']}"
    )
    print(f"wrote {out}")
    return EXIT_OK


def _solve(args: argparse.Namespace) -> int:
    try:
        from har2client.solve.beam import solve
    except ImportError as why:
        print(f"error: cannot import har2client.solve.beam.solve ({why})", file=sys.stderr)
        return EXIT_ERROR
    load_env_key(ENV_PATH, os.environ)  # a key already in the environment wins; the value is never printed
    info = solve(Path(args.task_dir), Path(args.out), args.effort)
    print(json.dumps(info, indent=2, default=str))
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="har2client")
    parser.add_argument("--version", action="version", version=f"har2client {metadata.version('har2client')}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="build a task from an OpenAPI 3.0 spec (path or URL)")
    p.add_argument("spec")
    p.add_argument("--api-id", required=True)
    p.add_argument("--out", default="tasks")
    p.add_argument("--noise", type=float, default=0.7)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--names", choices=NAMES_MODES, default="exact", help="goal names: wire-like, reworded, or opaque")
    p.add_argument("--twin", action="store_true", help="rename host, title, paths and fields (memorisation control)")
    p.add_argument("--closed-book", action="store_true", help="empty session.har (answer from memory)")
    p.add_argument("--rewordings-dir", default=None, help="cache of Beam rewordings (default data/rewordings)")
    p.set_defaults(func=_build)

    p = sub.add_parser("ref", help="write ref_client.py into the task dir")
    p.add_argument("task_dir")
    p.set_defaults(func=_ref)

    p = sub.add_parser("grade", help="grade a client.py; writes grade.json next to it")
    p.add_argument("task_dir")
    p.add_argument("client")
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(func=_grade)

    p = sub.add_parser("solve", help="run the Beam baseline")
    p.add_argument("task_dir")
    p.add_argument("--effort", default="high")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_solve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
