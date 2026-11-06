"""Command line entry point: `csvcontract infer | check | profile | sample`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .contract import ERROR, WARN, Contract, check, infer, load_csv
from .sample import build


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="csvcontract", description="Data contracts for CSV files.")
    p.add_argument("--version", action="version", version=f"csvcontract {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    inf = sub.add_parser("infer", help="write a contract from a CSV you trust")
    inf.add_argument("csv")
    inf.add_argument("-o", "--out", help="contract file to write (default: print it)")

    chk = sub.add_parser("check", help="check a new CSV against a contract")
    chk.add_argument("contract")
    chk.add_argument("csv")
    chk.add_argument("--json", action="store_true", help="print violations as JSON")
    chk.add_argument("--fail-on", choices=("error", "warn", "never"), default="error")
    chk.add_argument("--drift-warn", type=float, default=0.1, help="PSI at which drift is a warning (default 0.1)")
    chk.add_argument("--drift-error", type=float, default=0.25, help="PSI at which drift is an error (default 0.25)")

    prof = sub.add_parser("profile", help="summarize each column of a CSV")
    prof.add_argument("csv")

    smp = sub.add_parser("sample", help="write the example order files")
    smp.add_argument("--out", default="examples")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sample":
            folder = build(args.out)
            print(f"Wrote {folder / 'orders_2026_09.csv'} and {folder / 'orders_2026_10.csv'}")
            return 0

        if args.command == "infer":
            header, rows = load_csv(args.csv)
            text = json.dumps(infer(header, rows, Path(args.csv).name).to_dict(), indent=2) + "\n"
            if args.out:
                Path(args.out).write_text(text)
                print(f"Wrote a contract for {len(header)} columns from {len(rows):,} rows to {args.out}")
            else:
                print(text, end="")
            return 0

        if args.command == "profile":
            header, rows = load_csv(args.csv)
            contract = infer(header, rows)
            print(f"{len(rows):,} rows, {len(header)} columns\n")
            width = max(len(c.name) for c in contract.columns)
            for c in contract.columns:
                bits = [c.type, "unique" if c.unique else "", f"{c.null_share:.0%} empty" if c.nullable else "never empty"]
                if c.min is not None:
                    bits.append(f"{c.min} to {c.max}")
                if c.allowed:
                    bits.append(f"{len(c.allowed)} values: {', '.join(c.allowed[:6])}{', ...' if len(c.allowed) > 6 else ''}")
                if c.pattern:
                    bits.append(f"shape {c.pattern}")
                print(f"  {c.name:<{width}}  " + ", ".join(b for b in bits if b))
            return 0

        contract = Contract.from_dict(json.loads(Path(args.contract).read_text()))
        header, rows = load_csv(args.csv)
        found = check(contract, header, rows, args.drift_warn, args.drift_error)
        errors = [v for v in found if v.severity == ERROR]
        warns = [v for v in found if v.severity == WARN]
        if args.json:
            print(json.dumps({"rows": len(rows), "errors": len(errors), "warnings": len(warns), "violations": [asdict(v) for v in found]}, indent=2))
        else:
            print(f"Checked {len(rows):,} rows of {Path(args.csv).name} against the contract from {contract.source or args.contract}.")
            print(f"{len(errors)} errors, {len(warns)} warnings.\n" if found else "No problems found.")
            for v in found:
                print(f"  {v.severity.upper():<5}  {v.column}: {v.message}" + (f"  (rows {', '.join(map(str, v.rows))})" if v.rows else ""))
        fail = {"error": errors, "warn": errors + warns, "never": []}[args.fail_on]
        return 1 if fail else 0
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
