"""
NABBAH 2.3 — Saved What-if Scenario engine (pure, Decimal, no AI).

Model (documented, deterministic):
  revenue  = base_sales × (1+revenue_growth) × (1+customer_change) × (1+aov_change)
  volume   = (1+revenue_growth) × (1+customer_change)      # price/AOV changes add no cost
  With a cost breakdown (company-level finance data):
     cogs    × volume × (1+cogs_change)
     payroll × (1+payroll_change), rent × (1+rent_change), marketing × (1+marketing_change)
     other   × (1+expense_change)                          # other = expenses − breakdown
  Without a breakdown: expenses × (1+expense_change); component assumptions are NOT applied
  and a warning says costs were held fixed (profit may be overstated when revenue grows).
Results are estimates, never stored as actual financial data.
"""
import os, sys
from decimal import Decimal
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase21"))
from nabbah_finance import to_decimal, round_money, round_pct  # noqa: E402

SCENARIO_VERSION = "1.0"
ASSUMPTIONS = ("revenue_growth_pct", "customer_change_pct", "aov_change_pct", "expense_change_pct",
               "cogs_change_pct", "payroll_change_pct", "rent_change_pct", "marketing_change_pct")
COMPONENT = {"cogs_change_pct": "cogs", "payroll_change_pct": "payroll", "rent_change_pct": "rent",
             "marketing_change_pct": "marketing"}
TYPES = ("growth", "cost_reduction", "pricing", "marketing", "custom")
MIN_PCT, MAX_PCT = Decimal(-90), Decimal(300)


class ScenarioError(ValueError):
    def __init__(self, field, message_ar, message_en):
        super().__init__(message_en)
        self.field, self.message_ar, self.message_en = field, message_ar, message_en


def validate(payload):
    """Returns a clean dict or raises ScenarioError (field + AR/EN message)."""
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise ScenarioError("name", "اسم السيناريو مطلوب (حتى 120 حرفاً)", "Scenario name is required (up to 120 characters)")
    stype = payload.get("scenario_type") or "custom"
    if stype not in TYPES:
        raise ScenarioError("scenario_type", "نوع سيناريو غير صالح", "Invalid scenario type")
    raw = payload.get("assumptions") or {}
    if not isinstance(raw, dict):
        raise ScenarioError("assumptions", "الافتراضات غير صالحة", "Invalid assumptions")
    unknown = set(raw) - set(ASSUMPTIONS)
    if unknown:
        raise ScenarioError(sorted(unknown)[0], "افتراض غير معروف", "Unknown assumption")
    clean = {}
    for k in ASSUMPTIONS:
        if raw.get(k) in (None, ""):
            continue
        v = to_decimal(raw[k])
        if v is None:
            raise ScenarioError(k, "القيمة يجب أن تكون رقماً", "Value must be a number")
        if not MIN_PCT <= v <= MAX_PCT:
            raise ScenarioError(k, "النسبة يجب أن تكون بين −90% و+300%", "Percentage must be between -90% and +300%")
        clean[k] = str(v)
    if not clean:
        raise ScenarioError("assumptions", "أدخل افتراضاً واحداً على الأقل", "Enter at least one assumption")
    bid = payload.get("branch_id")
    return {"name": name, "description": str(payload.get("description") or "")[:500], "scenario_type": stype,
            "assumptions": clean, "branch_id": int(bid) if bid not in (None, "") else None,
            "base_period": (str(payload.get("base_period")).strip() or None) if payload.get("base_period") else None}


def build_baseline(current_rows, branch_id=None, breakdown=None, period=None):
    rows = [r for r in current_rows or [] if branch_id is None or r.get("branch_id") == branch_id]
    if not rows:
        return None
    D = lambda k: sum((to_decimal(r.get(k)) or Decimal(0)) for r in rows)
    base = {"period": period, "branch_id": branch_id, "rows": len(rows),
            "revenue": D("sales"), "expenses": D("expenses"), "customers": D("customers"), "invoices": D("invoices")}
    bd = {}
    if breakdown and branch_id is None:
        for k in ("cogs", "payroll", "rent", "marketing"):
            v = to_decimal(breakdown.get(k))
            if v is not None and v >= 0:
                bd[k] = v
        if sum(bd.values(), Decimal(0)) > base["expenses"]:
            bd = {}  # breakdown larger than total expenses -> unreliable, ignore
    base["breakdown"] = bd
    return base


def run(baseline, assumptions):
    if not baseline or baseline["revenue"] <= 0:
        return {"status": "insufficient_data", "is_estimate": True,
                "warnings": [{"code": "no_baseline", "ar": "لا توجد مبيعات للفترة الأساس — لا يمكن تشغيل السيناريو.",
                              "en": "No sales in the base period — the scenario cannot run."}]}
    a = {k: (to_decimal(v) or Decimal(0)) / 100 for k, v in assumptions.items()}
    g, c, p = a.get("revenue_growth_pct", 0), a.get("customer_change_pct", 0), a.get("aov_change_pct", 0)
    volume = (1 + g) * (1 + c)
    revenue = baseline["revenue"] * volume * (1 + p)
    warnings, applied, not_applied = [], [], []
    bd = baseline["breakdown"]
    if bd:
        comp = {k: bd.get(k, Decimal(0)) for k in ("cogs", "payroll", "rent", "marketing")}
        other = baseline["expenses"] - sum(comp.values())
        cost = comp["cogs"] * volume * (1 + a.get("cogs_change_pct", 0)) \
            + comp["payroll"] * (1 + a.get("payroll_change_pct", 0)) \
            + comp["rent"] * (1 + a.get("rent_change_pct", 0)) \
            + comp["marketing"] * (1 + a.get("marketing_change_pct", 0)) \
            + other * (1 + a.get("expense_change_pct", 0))
        applied = list(assumptions)
    else:
        cost = baseline["expenses"] * (1 + a.get("expense_change_pct", 0))
        for k in assumptions:
            (not_applied if k in COMPONENT else applied).append(k)
        warnings.append({"code": "costs_not_broken_down",
                         "ar": "لا يوجد تفصيل للتكاليف لهذا النطاق، فافتُرضت المصروفات ثابتة إلا بنسبة تغيير المصروفات. قد يكون الربح المتوقع مبالغاً فيه عند نمو المبيعات.",
                         "en": "No cost breakdown for this scope; expenses were held fixed except the expense change. Projected profit may be overstated when sales grow."})
        if not_applied:
            warnings.append({"code": "assumptions_not_applied", "fields": not_applied,
                             "ar": "لم تُطبَّق افتراضات بنود التكلفة لعدم توفر بياناتها.",
                             "en": "Cost-line assumptions were not applied because their data is unavailable."})
    bp, sp = baseline["revenue"] - baseline["expenses"], revenue - cost
    bm, sm = bp / baseline["revenue"] * 100, (sp / revenue * 100 if revenue else None)
    if sm is not None and sm < 0 <= bm:
        warnings.append({"code": "turns_to_loss", "ar": "هذا السيناريو يحوّل الربح إلى خسارة.", "en": "This scenario turns the profit into a loss."})
    m = lambda d: None if d is None else {"value": float(round_money(d)), "value_decimal": str(round_money(d))}
    pct = lambda new, old: None if not old else float(round_pct((new - old) / abs(old) * 100))
    rows = [
        {"metric": "revenue", "baseline": m(baseline["revenue"]), "scenario": m(revenue), "difference": m(revenue - baseline["revenue"]), "change_pct": pct(revenue, baseline["revenue"])},
        {"metric": "costs", "baseline": m(baseline["expenses"]), "scenario": m(cost), "difference": m(cost - baseline["expenses"]), "change_pct": pct(cost, baseline["expenses"])},
        {"metric": "profit", "baseline": m(bp), "scenario": m(sp), "difference": m(sp - bp), "change_pct": pct(sp, bp)},
        {"metric": "margin_pct", "baseline": m(bm), "scenario": m(sm), "difference": m(None if sm is None else sm - bm), "change_pct": None},
    ]
    return {"status": "ok", "is_estimate": True, "method": f"scenario-v{SCENARIO_VERSION}",
            "base_period": baseline["period"], "branch_id": baseline["branch_id"], "source": "companyentry",
            "comparison": rows, "revenue_impact": m(revenue - baseline["revenue"]),
            "profit_impact": m(sp - bp), "margin_impact_pts": None if sm is None else float(round_pct(sm - bm)),
            "applied": applied, "not_applied": not_applied, "warnings": warnings,
            "baseline_values": {"revenue": m(baseline["revenue"]), "expenses": m(baseline["expenses"]), "profit": m(bp),
                                "margin_pct": m(bm), "has_breakdown": bool(bd)}}
