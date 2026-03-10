from __future__ import annotations

import argparse
import socket
from pathlib import Path

import uvicorn

from infostream.core.config import BackendConfig
from infostream.main import create_app

# This file is the CLI entry point and implements the ports/CLI entry
# and argument parsing functions

def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Infostream daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--home", type=Path, default=None)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    port = args.port if args.port else _find_free_port(args.host)

    config = BackendConfig(home=args.home)
    app = create_app(config=config, port=port)

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
