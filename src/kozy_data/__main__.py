"""CLI: fetch spatio-temporal data for gmina Kozy.

Examples:
    python -m kozy_data list
    python -m kozy_data fetch open_meteo --since 2020-01-01
    python -m kozy_data fetch all
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_sources
from .sources import DEFAULT_ORDER, available, get_downloader


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _enabled(name: str) -> bool:
    return bool(load_sources().get(name, {}).get("enabled", False))


def _run_one(name: str, since: str | None) -> bool:
    try:
        dl = get_downloader(name)
        result = dl.run(since=since)
        print(f"  OK  {result}")
        return True
    except Exception as exc:  # noqa: BLE001 - report and continue across sources
        logging.getLogger("kozy_data").exception("source %s failed", name)
        print(f"  ERR {name}: {exc}")
        return False


def cmd_list(_: argparse.Namespace) -> int:
    for name in available():
        flag = "on " if _enabled(name) else "off"
        tier = load_sources().get(name, {}).get("tier", "?")
        print(f"  [{flag}] tier {tier}  {name}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    if args.source == "all":
        targets = [n for n in DEFAULT_ORDER if _enabled(n)]
        if not targets:
            print("No enabled sources in config/sources.yaml")
            return 1
    else:
        targets = [args.source]
    print(f"Fetching: {', '.join(targets)}")
    ok = sum(_run_one(name, args.since) for name in targets)
    print(f"Done: {ok}/{len(targets)} succeeded")
    return 0 if ok == len(targets) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kozy-data", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list sources and enabled state").set_defaults(func=cmd_list)

    p_fetch = sub.add_parser("fetch", help="fetch a source (or 'all')")
    p_fetch.add_argument("source", help="source name or 'all'")
    p_fetch.add_argument("--since", default=None, help="ISO date, e.g. 2020-01-01")
    p_fetch.set_defaults(func=cmd_fetch)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
