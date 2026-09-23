"""
NABBAH 2.2 — Period-Aware Aggregation (Remediation)

Replaces the unsafe "latest two rows per branch" selection.

Facts about the data model (verified in main.py):
  - CompanyEntry.period is a free-text string; the app's default format is "YYYY-MM".
  - POST /company/entry always INSERTS a new row (no upsert), so one branch can
    have several rows for the same period (re-submissions / corrections).

Rules implemented:
  1. The period string is parsed. Unparseable periods are EXCLUDED and reported
     as a data gap. created_at is NOT used as a substitute period.
  2. Current period = explicit request, else the latest parseable period present.
  3. Comparison period = the calendar month immediately before the current period.
     There is NO fallback to "the second-latest record".
  4. Within one period, per branch: the most recent row (created_at) wins.
     Across branches: rows are aggregated by the caller.
  5. If the comparison period has no data, comparison is empty and a gap is
     returned. Growth must not be calculated in that case.
"""
import re

PERIOD_VERSION = "1.0"

_PATTERNS = [
    (re.compile(r"^\s*(\d{4})[-/.](\d{1,2})\s*$"), "ym"),   # 2026-09, 2026/9
    (re.compile(r"^\s*(\d{1,2})[-/.](\d{4})\s*$"), "my"),   # 9/2026, 09-2026
]


def parse_period(value):
    """Return (year, month) or None. Never guesses from ambiguous text."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for rx, kind in _PATTERNS:
        m = rx.match(s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            year, month = (a, b) if kind == "ym" else (b, a)
            if 1 <= month <= 12 and 1900 <= year <= 2200:
                return (year, month)
            return None
    return None


def format_period(ym):
    return f"{ym[0]:04d}-{ym[1]:02d}" if ym else None


def previous_period(ym):
    y, m = ym
    return (y - 1, 12) if m == 1 else (y, m - 1)


def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _latest_per_branch(rows):
    """One row per branch: the most recent created_at (re-submission = correction)."""
    best = {}
    for r in rows:
        bid = _get(r, "branch_id")
        ts = _get(r, "created_at")
        cur = best.get(bid)
        if cur is None:
            best[bid] = r
            continue
        cur_ts = _get(cur, "created_at")
        if ts is not None and (cur_ts is None or ts > cur_ts):
            best[bid] = r
    return list(best.values())


def split_by_period(entries, current_period=None):
    """Split entries into current and comparison periods.

    Returns a dict:
      current_period, comparison_period       -> "YYYY-MM" or None
      current_entries, comparison_entries     -> deduplicated per branch
      has_comparison                          -> bool
      excluded_unparseable                    -> int
      superseded_duplicates                   -> int (older same-branch same-period rows)
      data_gaps                               -> list of {missing, why_it_matters, required_data}
    """
    gaps = []
    buckets = {}
    excluded = 0
    for e in entries or []:
        ym = parse_period(_get(e, "period"))
        if ym is None:
            excluded += 1
            continue
        buckets.setdefault(ym, []).append(e)

    if excluded:
        gaps.append({
            "missing": "valid_period",
            "why_it_matters": f"{excluded} record(s) have no parseable period and were excluded.",
            "required_data": "Set the period as YYYY-MM for these records.",
        })

    if current_period is not None:
        cur = parse_period(current_period)
        if cur is None:
            gaps.append({
                "missing": "requested_period",
                "why_it_matters": "The requested period could not be parsed.",
                "required_data": "Use the YYYY-MM format.",
            })
            return _empty(excluded, gaps)
    elif buckets:
        cur = max(buckets.keys())
    else:
        gaps.append({
            "missing": "any_period",
            "why_it_matters": "No record has a usable period, so no period can be analyzed.",
            "required_data": "Branch entries with a YYYY-MM period.",
        })
        return _empty(excluded, gaps)

    prev = previous_period(cur)
    cur_rows = buckets.get(cur, [])
    prev_rows = buckets.get(prev, [])
    cur_dedup = _latest_per_branch(cur_rows)
    prev_dedup = _latest_per_branch(prev_rows)
    superseded = (len(cur_rows) - len(cur_dedup)) + (len(prev_rows) - len(prev_dedup))

    if not cur_dedup:
        gaps.append({
            "missing": "current_period_data",
            "why_it_matters": f"No records exist for {format_period(cur)}.",
            "required_data": f"Branch entries for {format_period(cur)}.",
        })
    if not prev_dedup:
        gaps.append({
            "missing": "comparison_period_data",
            "why_it_matters": f"No records for {format_period(prev)}; growth and drivers cannot be calculated.",
            "required_data": f"Branch entries for {format_period(prev)}.",
        })

    return {
        "current_period": format_period(cur),
        "comparison_period": format_period(prev),
        "current_entries": cur_dedup,
        "comparison_entries": prev_dedup,
        "has_comparison": bool(prev_dedup),
        "excluded_unparseable": excluded,
        "superseded_duplicates": superseded,
        "data_gaps": gaps,
        "period_version": f"period-v{PERIOD_VERSION}",
    }


def _empty(excluded, gaps):
    return {
        "current_period": None, "comparison_period": None,
        "current_entries": [], "comparison_entries": [],
        "has_comparison": False, "excluded_unparseable": excluded,
        "superseded_duplicates": 0, "data_gaps": gaps,
        "period_version": f"period-v{PERIOD_VERSION}",
    }
