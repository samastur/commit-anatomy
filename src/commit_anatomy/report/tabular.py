"""Machine-readable output: the tidy rows as JSON or CSV."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict

from ..rows import FIELDS, Row


def to_json(rows: list[Row]) -> str:
    if not rows:
        return "[]"
    return json.dumps(
        [asdict(r) | {"flags": list(r.flags)} for r in rows], indent=2, ensure_ascii=False
    )


def to_csv(rows: list[Row]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(asdict(r) | {"flags": "|".join(r.flags)})
    return out.getvalue()
