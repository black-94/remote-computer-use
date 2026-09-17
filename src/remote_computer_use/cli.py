from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from .errors import DiscoveryError
from .logging_setup import configure_logging
from .models import load_config
from .service import DiscoveryService
from .server import build_server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remote-computer-use")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start the MCP server")
    _common(serve)
    serve.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    validate = subparsers.add_parser("validate", help="validate configuration")
    validate.add_argument("--config", required=True)

    check = subparsers.add_parser("check", help="run one health refresh and print JSON")
    _common(check)
    check.add_argument("--remote")

    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", required=True)
    parser.add_argument("--log-file")
    parser.add_argument("--verbose", action="store_true")


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    if not values or values[0] not in {"serve", "validate", "check"}:
        return ["serve", *values]
    return values


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(_normalize_argv(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "validate":
            config = load_config(args.config)
            print(json.dumps({"valid": True, "remotes": len(config.remotes)}))
            return 0

        configure_logging(args.log_file, verbose=args.verbose)
        service = DiscoveryService(args.config)
        if args.command == "check":
            result = asyncio.run(service.refresh_health(args.remote))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        server = build_server(service, host=args.host, port=args.port)
        server.run(transport=args.transport)
        return 0
    except DiscoveryError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

