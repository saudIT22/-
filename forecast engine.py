"""
NABBAH 2.3 — Forecast Engine (pure, Decimal, no AI)

Method (documented, testable):
  1. Monthly series: parse CompanyEntry.period, per branch per month the latest
     submission wins (same rule as Phase 2.2), branches summed.
  2. Needs >= 3 CONSECUTIVE months ending at the latest month; otherwise no forecast
     and an explicit reason (which months are missing).
  3. Uses the last up to 6 consecutive months. Month-over-month changes:
       - percentage growth when every base month is > 0 (sales),
       - absolute change otherwise (e.g. profit that is zero or negative).
  4. Base  = last value + MEDIAN change (median resists outliers).
     Best  = Base + 1 standard deviation of the changes.
     Worst = Base - 1 standard deviation of the changes.
     (Derived from the company's own volatility — no fixed ±% assumption.)
  5. Outliers: a change is flagged (never removed) when |change - median| exceeds
     max(3 x 1.4826 x MAD, floor). MAD = median absolute deviation (robust for small
     samples). Floor = 10 percentage points (percent series) or 10% of the average
     absolute value (absolute series), so normal small wobbles are not flagged.
  6. Seasonality: not applied (requires >= 24 months) — stated in the output.
  7. Confidence: low if < 4 months or dispersion > 25 pts; high if >= 6 months and
     dispersion <= 10 pts; medium otherwise. Dispersion = std-dev of % changes.
Every result is labelled is_estimate=True.
"""
import os
import sys
from decimal import Decimal, getcontext

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "phase21"))
sys.path.insert(0, os.path.join(_HERE, "..", "phase22"))
from nabbah_finance import to_decimal, round_money, round_pct  # noqa: E402
from period_aggregation import parse_period, format_period, previous_period  # noqa: E402
from rule_catalog import texts as rule_texts  # noqa: E402

getcontext().prec = 28
FORECAST_VERSION = "1.0"
MIN_MONTHS = 3
WINDOW = 6


def _g(r, k):
    return r.get(k) if isinstance(r, dict) else getattr(r, k, None)


def _next_period(ym):
    y, m = ym
    return (y + 1, 1) if m == 12 else (y, m + 1)


def build_monthly_series(rows, branch_id=None):
    """{(y,m): {"sales": Decimal, "profit": Decimal, "branches": n}} — latest submission per branch per month."""
    latest = {}
    for r in rows or []:
        if branch_id is not None and _g(r, "branch_id") != branch_id:
            continue
        ym = parse_period(_g(r, "period"))
        if ym is None:
            continue
        key = (ym, _g(r, "branch_id"))
        cur = latest.get(key)
        if cur is None or (_g(r, "created_at") or 0) > (_g(cur, "created_at") or 0):
            latest[key] = r
    series = {}
    for (ym, _b), r in latest.items():
        s = to_decimal(_g(r, "sales")) or Decimal(0)
        e = to_decimal(_g(r, "expenses")) or Decimal(0)
        cell = series.setdefault(ym, {"sales": Decimal(0), "profit": Decimal(0), "branches": 0})
        cell["sales"] += s
        cell["profit"] += s - e
        cell["branches"] += 1
    return series


def _consecutive_tail(series):
    if not series:
        return []
    months = sorted(series)
    tail = [months[-1]]
    while True:
        prev = previous_period(tail[0])
        if prev in series:
            tail.insert(0, prev)
        else:
            break
    return tail


def _median(vals):
    v = sorted(vals)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _stdev(vals):
    if len(vals) < 2:
        return Decimal(0)
    mean = sum(vals) / len(vals)
    return (sum((x - mean) ** 2 for x in vals) / len(vals)).sqrt()


def _quartiles(vals):
    v = sorted(vals)
    n = len(v)
    lower, upper = v[: n // 2], v[(n + 1) // 2:]
    return _median(lower) if lower else v[0], _median(upper) if upper else v[-1]


def forecast_metric(series, metric="sales", target=None, currency="SAR"):
    """Forecast the next month for one metric. Never fabricates on insufficient data."""
    tail = _consecutive_tail(series)
    latest = sorted(series)[-1] if series else None
    base = {"metric": metric, "is_estimate": True, "currency": currency,
            "method": f"median-change-v{FORECAST_VERSION}", "seasonality": "not_applied (needs 24 months)",
            "warnings": []}
    if len(tail) < MIN_MONTHS:
        missing = []
        if latest:
            p = latest
            for _ in range(MIN_MONTHS - 1):
                p = previous_period(p)
                if p not in series:
                    missing.append(format_period(p))
        base.update({"status": "insufficient_data", "forecast_period": None, "texts": rule_texts("insufficient_data"),
                     "reason": f"Needs {MIN_MONTHS} consecutive months; found {len(tail)}.",
                     "missing_months": missing, "months_used": [format_period(m) for m in tail]})
        return base

    window = tail[-WINDOW:]
    vals = [series[m][metric] for m in window]
    use_pct = all(v > 0 for v in vals[:-1])
    changes = []
    for a, b in zip(vals[:-1], vals[1:]):
        changes.append(((b - a) / a * 100) if use_pct else (b - a))
    med, sd = _median(changes), _stdev(changes)
    last = vals[-1]

    def project(delta):
        return last * (1 + delta / 100) if use_pct else last + delta

    base_v, best_v, worst_v = project(med), project(med + sd), project(med - sd)
    mad = _median([abs(c - med) for c in changes])
    floor = Decimal(10) if use_pct else (sum(abs(v) for v in vals) / len(vals)) * Decimal("0.1")
    limit = max(Decimal("3") * Decimal("1.4826") * mad, floor)
    outliers = [format_period(window[i + 1]) for i, c in enumerate(changes)
                if len(changes) >= 3 and abs(c - med) > limit]
    if outliers:
        base["warnings"].append({"code": "outlier_months", "texts": rule_texts("outlier_months", months="، ".join(outliers)), "months": outliers,
                                 "note": "Unusual month-over-month change; kept, median limits its effect."})
    if any(v < 0 for v in vals):
        base["warnings"].append({"code": "negative_values", "texts": rule_texts("negative_values"), "note": "Series contains negative values."})
    gap_months = sorted(set(series) - set(tail))
    if gap_months:
        base["warnings"].append({"code": "history_gap", "texts": rule_texts("history_gap", months="، ".join(format_period(x) for x in gap_months[-6:])), "note": "Older months before a gap were not used.",
                                 "months": [format_period(m) for m in gap_months][-6:]})

    n = len(window)
    dispersion = sd if use_pct else None
    if n < 4 or (dispersion is not None and dispersion > 25):
        conf = "low"
    elif n >= 6 and (dispersion is None or dispersion <= 10):
        conf = "high"
    else:
        conf = "medium"

    rm = lambda d: {"value": float(round_money(d)), "value_decimal": str(round_money(d))}
    fp = format_period(_next_period(window[-1]))
    base.update({
        "status": "ok", "forecast_period": fp, "months_used": [format_period(m) for m in window],
        "change_type": "percent" if use_pct else "absolute",
        "median_change": float(round_pct(med)), "std_dev": float(round_pct(sd)),
        "last_actual": rm(last),
        "base": rm(base_v), "best": rm(best_v), "worst": rm(worst_v),
        "confidence": conf,
        "confidence_rule": "low: <4 months or dispersion >25 pts; high: >=6 months and dispersion <=10 pts; else medium",
        "assumptions": [f"Next month follows the median {'%' if use_pct else 'absolute'} change of the last {n} months.",
                        "Best/worst = ±1 standard deviation of those changes.",
                        "No seasonality, price changes, openings or closures are modelled."],
        "assumptions_texts": {
            "ar": [f"الشهر القادم يتبع الوسيط لتغيّر آخر {n} أشهر ({'نسبة مئوية' if use_pct else 'قيمة مطلقة'}).",
                   "المتفائل والمتشائم = ± انحراف معياري واحد لتلك التغيّرات.",
                   "لا يشمل الموسمية أو تغيّر الأسعار أو افتتاح أو إغلاق فروع."],
            "en": [f"Next month follows the median {'%' if use_pct else 'absolute'} change of the last {n} months.",
                   "Best/worst = ±1 standard deviation of those changes.",
                   "No seasonality, price changes, openings or closures are modelled."]},
    })
    t = to_decimal(target)
    if t is not None and t > 0 and metric == "sales":
        gap = base_v - t
        base["target"] = {"value": float(t), "gap": rm(gap),
                          "gap_pct": float(round_pct(gap / t * 100)), "status": "above" if gap >= 0 else "below"}
    return base


def forecast_company(rows, branches, currency="SAR"):
    """Company sales + profit forecasts and per-branch sales forecasts."""
    target = sum((to_decimal(_g(b, "target_sales")) or Decimal(0)) for b in branches) or None
    series = build_monthly_series(rows)
    out = {"sales": forecast_metric(series, "sales", target, currency),
           "profit": forecast_metric(series, "profit", None, currency),
           "branches": []}
    for b in branches:
        bs = build_monthly_series(rows, branch_id=_g(b, "id"))
        f = forecast_metric(bs, "sales", _g(b, "target_sales"), currency)
        f.update({"branch_id": _g(b, "id"), "branch_name": _g(b, "name")})
        out["branches"].append(f)
    return out
