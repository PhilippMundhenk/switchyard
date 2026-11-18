"""Command line: ``switchyard serve`` (default), ``discover`` and ``sim``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .config import load


def _serve(args) -> int:
    import uvicorn

    from .web.app import create_app

    config = load(args.data)
    if args.demo:
        config.values["demo.enabled"] = True
    logging.basicConfig(
        level=config["log.level"].upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        create_app(config),
        host=config["server.host"],
        port=config["server.port"],
        log_level=config["log.level"],
    )
    return 0


async def _discover(args) -> int:
    from .nsdp import Transport, discover

    config = load(args.data)
    transport = Transport(
        config["nsdp.client_port"],
        config["nsdp.device_port"],
        list(config["nsdp.targets"]),
        config["nsdp.interfaces"],
    )
    await transport.start()
    try:
        found = await discover(transport, config["nsdp.discovery_seconds"])
    finally:
        await transport.stop()
    for d in found:
        row = (d["mac"], d["ip"] or "-", d["model"] or "?", d["firmware"] or "?", d["name"])
        print("{}  {:15}  {:10}  {:9}  {}".format(*row))
    if not found:
        print("No switch answered.", file=sys.stderr)
    return 0 if found else 1


async def _sim(args) -> int:
    from .sim import Simulator, demo_switches

    simulator = Simulator(demo_switches())
    port = await simulator.start(args.host, args.port)
    print(f"{len(simulator.switches)} simulated switches on UDP {args.host}:{port}", flush=True)
    await asyncio.Event().wait()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="switchyard", description="Web manager for NSDP switches")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data", help="data folder (default: $SWITCHYARD_DATA_DIR or ./data)")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the web interface (default)")
    serve.add_argument("--demo", action="store_true", help="use simulated switches")
    sub.add_parser("discover", help="search the network once and print what answers")
    sim = sub.add_parser("sim", help="run simulated switches for development")
    sim.add_argument("--host", default="127.0.0.1")
    sim.add_argument("--port", type=int, default=63322)
    args = parser.parse_args(argv)
    if args.command == "discover":
        return asyncio.run(_discover(args))
    if args.command == "sim":
        try:
            return asyncio.run(_sim(args))
        except KeyboardInterrupt:
            return 0
    if args.command is None:
        args.demo = False
    return _serve(args)


if __name__ == "__main__":
    sys.exit(main())
