"""Infer a data contract from a trusted CSV, then check new batches against it.

A contract records what "normal" looks like for each column: its type,
whether it can be empty, its range, its allowed values, its shape, and its
distribution. Checking a new batch against it catches the problems that
break pipelines quietly: a new status code, a column that started arriving
empty, prices in cents instead of dollars, a date format that changed.
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

NULLS = {"", "null", "none", "na", "n/a", "nan"}
MAX_ENUM = 20  # more distinct values than this and a string column is free text, not a category


def is_null(v: str) -> bool:
    return v.strip().lower() in NULLS


def parse(v: str, kind: str):
    """Parse a non-null value as `kind`, or raise ValueError."""
    v = v.strip()
    if kind == "integer":
        if not re.fullmatch(r"[+-]?\d+", v):
            raise ValueError
        return int(v)
    if kind == "number":
        x = float(v)
        if not math.isfinite(x):
            raise ValueError
        return x
    if kind == "boolean":
        if v.lower() not in ("true", "false"):
            raise ValueError
        return v.lower() == "true"
    if kind == "date":
        return date.fromisoformat(v) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else _bad()
    if kind == "datetime":
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v else _bad()
    return v


def _bad():
    raise ValueError


TYPES = ("boolean", "integer", "number", "date", "datetime", "string")


def infer_type(values: list[str]) -> str:
    for kind in TYPES:
        try:
            for v in values:
                parse(v, kind)
            return kind
        except (ValueError, TypeError):
            continue
    return "string"


def shape(v: str) -> str:
    """Generalize a string: 'ORD-004512' becomes 'AAA-999999'."""
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", v)))


def shape_regex(s: str) -> str:
    out = []
    for m in re.finditer(r"(A+|a+|9+|[^Aa9]+)", s):
        run = m.group(0)
        cls = {"A": "[A-Z]", "a": "[a-z]", "9": r"\d"}.get(run[0])
        out.append(f"{cls}{{{len(run)}}}" if cls else re.escape(run))
    return "^" + "".join(out) + "$"


# Distribution profiles for drift --------------------------------------------------

def quantile_edges(xs: list[float], bins: int = 10) -> list[float]:
    s = sorted(xs)
    edges = []
    for i in range(1, bins):
        e = s[min(len(s) - 1, int(i * len(s) / bins))]
        if not edges or e > edges[-1]:
            edges.append(e)
    return edges


def bin_shares(xs: list[float], edges: list[float]) -> list[float]:
    counts = [0] * (len(edges) + 1)
    for x in xs:
        i = 0
        while i < len(edges) and x >= edges[i]:
            i += 1
        counts[i] += 1
    n = max(1, len(xs))
    return [c / n for c in counts]


def psi(expected: list[float], actual: list[float], floor: float = 1e-4) -> float:
    """Population stability index. Under 0.1 is stable, 0.1 to 0.25 is a shift, over 0.25 is a large shift."""
    total = 0.0
    for e, a in zip(expected, actual):
        e, a = max(e, floor), max(a, floor)
        total += (a - e) * math.log(a / e)
    return total


# The contract -------------------------------------------------------------------------

@dataclass
class Column:
    name: str
    type: str
    nullable: bool
    unique: bool = False
    min: float | str | None = None
    max: float | str | None = None
    allowed: list[str] | None = None
    pattern: str | None = None
    max_length: int | None = None
    null_share: float = 0.0
    dist: dict | None = None  # {"edges": [...], "shares": [...]} or {"values": {v: share}}


@dataclass
class Contract:
    columns: list[Column]
    rows: int
    source: str = ""

    def to_dict(self) -> dict:
        return {"source": self.source, "rows": self.rows, "columns": [{k: v for k, v in asdict(c).items() if v is not None} for c in self.columns]}

    @classmethod
    def from_dict(cls, d: dict) -> "Contract":
        return cls([Column(**c) for c in d["columns"]], d.get("rows", 0), d.get("source", ""))


def read_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r]
    if not rows:
        raise ValueError("The file is empty.")
    return [h.strip() for h in rows[0]], rows[1:]


def load_csv(path: str | Path) -> tuple[list[str], list[list[str]]]:
    return read_csv(Path(path).read_text(encoding="utf-8-sig"))


def infer(header: list[str], rows: list[list[str]], source: str = "") -> Contract:
    cols = []
    for i, name in enumerate(header):
        raw = [r[i] if i < len(r) else "" for r in rows]
        present = [v.strip() for v in raw if not is_null(v)]
        kind = infer_type(present) if present else "string"
        col = Column(name=name, type=kind, nullable=len(present) < len(raw), null_share=round(1 - len(present) / max(1, len(raw)), 4))
        # Only IDs are meant to be unique. Distinct prices or dates are a coincidence.
        col.unique = kind in ("string", "integer") and len(present) == len(raw) > 1 and len(set(present)) == len(present)
        if kind in ("integer", "number"):
            nums = [parse(v, kind) for v in present]
            col.min, col.max = min(nums), max(nums)
            if not col.unique and len(nums) >= 20:
                edges = quantile_edges(nums)
                col.dist = {"edges": edges, "shares": [round(s, 4) for s in bin_shares(nums, edges)]}
        elif kind in ("date", "datetime"):
            col.min, col.max = min(present), max(present)
        elif kind == "string" and present:
            distinct = Counter(present)
            if len(distinct) <= MAX_ENUM and len(distinct) < len(present) / 2:
                col.allowed = sorted(distinct)
                col.dist = {"values": {k: round(v / len(present), 4) for k, v in sorted(distinct.items())}}
            shapes = {shape(v) for v in present}
            if len(shapes) == 1 and not col.allowed:
                col.pattern = shape_regex(shapes.pop())
            col.max_length = max(len(v) for v in present)
        cols.append(col)
    return Contract(cols, len(rows), source)


# Checking ---------------------------------------------------------------------------------

ERROR, WARN = "error", "warn"


@dataclass
class Violation:
    severity: str
    column: str
    rule: str
    message: str
    rows: list[int] = field(default_factory=list)  # 1-based data row numbers, first few only
    count: int = 0


def check(contract: Contract, header: list[str], rows: list[list[str]], drift_warn: float = 0.1, drift_error: float = 0.25) -> list[Violation]:
    out: list[Violation] = []
    idx = {h: i for i, h in enumerate(header)}
    for name in header:
        if name not in {c.name for c in contract.columns}:
            out.append(Violation(WARN, name, "unexpected-column", "column is not in the contract"))
    for col in contract.columns:
        if col.name not in idx:
            out.append(Violation(ERROR, col.name, "missing-column", "column is missing"))
            continue
        i = idx[col.name]
        bad: dict[str, list[int]] = {}

        def flag(rule, n):
            bad.setdefault(rule, []).append(n)

        values, seen = [], {}
        for n, r in enumerate(rows, start=1):
            v = r[i] if i < len(r) else ""
            if is_null(v):
                if not col.nullable:
                    flag("null", n)
                continue
            v = v.strip()
            try:
                x = parse(v, col.type)
            except (ValueError, TypeError):
                flag("type", n)
                continue
            values.append(x)
            if col.unique:
                if v in seen:
                    flag("duplicate", n)
                seen[v] = n
            if col.min is not None and col.type in ("integer", "number") and x < col.min:
                flag("negative" if x < 0 <= col.min else "below-min", n)
            if col.max is not None and col.type in ("integer", "number") and x > col.max:
                flag("above-max", n)
            if col.allowed is not None and v not in col.allowed:
                flag("not-allowed", n)
            if col.pattern and not re.fullmatch(col.pattern, v):
                flag("pattern", n)
            if col.max_length and len(v) > col.max_length:
                flag("too-long", n)

        messages = {
            "null": (ERROR, "empty values in a column that has never been empty"),
            "type": (ERROR, f"values that are not {col.type}"),
            "duplicate": (ERROR, "duplicate values in a unique column"),
            "below-min": (WARN, f"values below the old minimum of {col.min}"),
            "negative": (ERROR, "negative values in a column that has never been negative"),
            "above-max": (WARN, f"values above the old maximum of {col.max}"),
            "not-allowed": (ERROR, f"values outside the allowed set {col.allowed}"),
            "pattern": (ERROR, f"values that do not match {col.pattern}"),
            "too-long": (WARN, f"values longer than {col.max_length} characters"),
        }
        for rule, ns in bad.items():
            sev, msg = messages[rule]
            if len(ns) == 1:
                msg = msg.replace("values", "value", 1)
            examples = []
            for n in ns[:3]:
                v = rows[n - 1][i] if i < len(rows[n - 1]) else ""
                examples.append(repr(v))
            out.append(Violation(sev, col.name, rule, f"{len(ns)} {msg}, e.g. {', '.join(examples)}", ns[:5], len(ns)))

        if col.nullable and rows:
            share = sum(1 for r in rows if is_null(r[i] if i < len(r) else "")) / len(rows)
            if share > max(0.05, col.null_share * 3):
                out.append(Violation(WARN, col.name, "more-nulls", f"{share:.0%} empty, up from {col.null_share:.0%}", count=round(share * len(rows))))

        if col.dist and len(values) >= 20:
            if "edges" in col.dist:
                actual = bin_shares(values, col.dist["edges"])
                expected = col.dist["shares"]
            else:
                c = Counter(str(v) for v in values)
                keys = list(col.dist["values"])
                expected = [col.dist["values"][k] for k in keys] + [0.0]
                actual = [c.get(k, 0) / len(values) for k in keys] + [sum(v for k, v in c.items() if k not in col.dist["values"]) / len(values)]
            score = psi(expected, actual)
            if score >= drift_warn:
                sev = ERROR if score >= drift_error else WARN
                out.append(Violation(sev, col.name, "drift", f"distribution shifted, PSI {score:.2f} ({'large' if score >= drift_error else 'moderate'})"))
    order = {ERROR: 0, WARN: 1}
    return sorted(out, key=lambda v: (order[v.severity], v.column))
