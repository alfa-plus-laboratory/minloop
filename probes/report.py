"""Pass rates per organism and polarity, with Wilson score intervals. Undecided verdicts are counted, not scored."""
from __future__ import annotations

import math
from collections import defaultdict


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes out of n (95% for z = 1.96); (0, 1) when n is 0."""
    if n == 0:
        return 0.0, 1.0
    p, d = k / n, 1 + z * z / n
    centre, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def summarize(verdicts) -> list[dict]:
    groups = defaultdict(list)
    for v in verdicts:
        groups[(v.cell.split("/")[0], v.polarity)].append(v.passed)
    rows = []
    for (organism, polarity), values in sorted(groups.items()):
        decided = [x for x in values if x is not None]
        k, n = sum(decided), len(decided)
        lo, hi = wilson(k, n)
        rows.append({"organism": organism, "polarity": polarity, "passed": k, "decided": n,
                     "undecided": len(values) - n, "rate": k / n if n else None, "ci95": [round(lo, 3), round(hi, 3)]})
    return rows


def table(rows: list[dict]) -> str:
    lines = [f"{'organism':<22} {'polarity':<9} {'pass':>4}/{'n':<3} {'undecided':>9}  rate   95% CI"]
    for r in rows:
        rate = "  -  " if r["rate"] is None else f"{r['rate']:.2f} "
        lines.append(f"{r['organism']:<22} {r['polarity']:<9} {r['passed']:>4}/{r['decided']:<3} {r['undecided']:>9}  "
                     f"{rate}  [{r['ci95'][0]:.2f}, {r['ci95'][1]:.2f}]")
    return "\n".join(lines)
