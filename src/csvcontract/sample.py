"""Synthetic order exports for the examples: one trusted month and one new batch with planted problems."""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

HEADER = ["order_id", "customer_id", "order_date", "status", "country", "channel", "items", "amount", "is_gift"]
STATUSES = ["completed", "completed", "completed", "completed", "shipped", "returned", "cancelled"]
COUNTRIES = ["US", "US", "US", "CA", "GB", "DE", "AU"]


def _rows(rng: random.Random, start: date, n: int, first_id: int, price_factor: float = 1.0, countries=COUNTRIES):
    rows = []
    for k in range(n):
        items = rng.choice([1, 1, 1, 2, 2, 3, 4])
        amount = round(sum(rng.lognormvariate(3.6, 0.6) for _ in range(items)) * price_factor, 2)
        rows.append([
            f"ORD-{first_id + k:06d}",
            f"C{rng.randrange(10000, 99999)}",
            (start + timedelta(days=rng.randrange(30))).isoformat(),
            rng.choice(STATUSES),
            rng.choice(countries),
            rng.choice(["web", "web", "app", "store"]),
            str(items),
            f"{amount:.2f}",
            rng.choice(["false"] * 9 + ["true"]),
        ])
    return rows


def write(path: Path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def build(folder: str | Path, seed: int = 5) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    write(folder / "orders_2026_09.csv", _rows(rng, date(2026, 9, 1), 1500, 1))

    # October: the same feed after an upstream release. Prices went up, a new
    # market opened, and a few things broke.
    rows = _rows(rng, date(2026, 10, 1), 1500, 1501, price_factor=1.35, countries=COUNTRIES + ["MX", "MX", "MX"])
    for r in rng.sample(rows, 40):
        r[1] = ""  # customer_id started arriving empty
    for r in rng.sample(rows, 12):
        r[3] = "on_hold"  # a new status nobody told the pipeline about
    for r in rng.sample(rows, 6):
        d = date.fromisoformat(r[2])
        r[2] = f"{d.month:02d}/{d.day:02d}/{d.year}"  # one service switched date formats
    for r in rng.sample(rows, 3):
        r[7] = f"-{r[7]}"  # refunds leaking into the orders feed
    rows[700][0] = rows[699][0]  # a retried write produced a duplicate id
    write(folder / "orders_2026_10.csv", rows)
    return folder
