"""
NABBAH 2.3 — Intelligence Engine (pure, Decimal, no AI, no database access)

  run_quality_checks   -> data quality warnings (7 checks)
  detect_signals       -> risks + opportunities, each with rule, KPI, evidence, severity basis
  build_recommendations-> problem / evidence / recommendation / priority / impact / confidence / action
  build_executive      -> assembles the Executive Intelligence payload

Inputs are plain rows already scoped to ONE company by the caller.
Every threshold below is a named constant so its origin is visible in the output.
"""
import hashlib
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "phase21"))
sys.path.insert(0, os.path.join(_HERE, "..", "phase22"))
from nabbah_finance import to_decimal, round_money, round_pct  # noqa: E402
from legacy_adapters import central_company_metrics  # noqa: E402
from forecast_engine import forecast_company  # noqa: E402
from rule_catalog import texts as rule_texts, threshold as rule_threshold  # noqa: E402

INTEL_VERSION = "1.0"
T = {  # thresholds — shown in every signal's "method"
    "decline_medium": Decimal(-5), "decline_high": Decimal(-15), "branch_decline": Decimal(-10),
    "expense_ratio_high": Decimal(90), "expense_growth_gap": Decimal(10),
    "repeat_low": Decimal(20), "branch_margin_gap": Decimal(10), "branch_target_low": Decimal(70),
    "momentum": Decimal(10), "target_over": Decimal(110), "dio_high": Decimal(60),
    "deposit_gap": Decimal(20), "margin_tol": Decimal("0.05"),
}
SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _g(r, k, default=None):
    v = r.get(k) if isinstance(r, dict) else getattr(r, k, None)
    return default if v is None else v


def _d(v):
    return to_decimal(v) or Decimal(0)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _money(v):
    return None if v is None else {"value": float(round_money(v)), "value_decimal": str(round_money(v))}


def _pct(num, den):
    num, den = to_decimal(num), to_decimal(den)
    if num is None or den is None or den == 0:
        return None
    return round_pct(num / den * 100)


# ═══════════════════════ 1. DATA QUALITY ═══════════════════════
def run_quality_checks(all_rows, current_rows, branch_map, period_gaps=None):
    """7 checks. Returns list of {code, severity, count, affected_ids, message_ar, message_en, fix}."""
    w = []

    def add(code, sev, ids, ar, en, fix):
        if ids:
            w.append({"code": code, "rule_code": code, "texts": rule_texts(code, n=len(ids)),
                      "severity": sev, "count": len(ids), "affected_ids": ids[:10],
                      "message_ar": ar.format(n=len(ids)), "message_en": en.format(n=len(ids)), "fix": fix})

    cur = list(current_rows or [])
    add("missing_values", "high",
        [_g(r, "id") for r in cur if _d(_g(r, "sales")) == 0 or _d(_g(r, "expenses")) == 0],
        "{n} سجل في الفترة الحالية بلا مبيعات أو مصروفات", "{n} current-period record(s) without sales or expenses",
        "Enter sales and expenses for these branches.")
    seen, dups = {}, []
    for r in all_rows or []:
        k = (_g(r, "branch_id"), str(_g(r, "period", "")).strip())
        if k in seen:
            dups.append(_g(r, "id"))
        seen[k] = True
    add("duplicate_entries", "medium", dups,
        "{n} إدخال مكرر لنفس الفرع والشهر (يُعتمد الأحدث)", "{n} duplicate branch/month entries (latest is used)",
        "Remove superseded submissions or confirm the latest is correct.")
    add("invalid_values", "high",
        [_g(r, "id") for r in all_rows or [] if any(_d(_g(r, f)) < 0 for f in ("sales", "expenses", "invoices", "customers"))],
        "{n} سجل يحتوي قيماً سالبة", "{n} record(s) with negative values", "Correct negative sales/expenses/counts.")
    add("zero_customers_high_sales", "medium",
        [_g(r, "id") for r in cur if _d(_g(r, "customers")) == 0 and _d(_g(r, "sales")) > 0],
        "{n} سجل بمبيعات دون عدد عملاء", "{n} record(s) with sales but zero customers",
        "Enter customer counts to enable basket and retention analysis.")
    bad_margin, bad_total = [], []
    for r in all_rows or []:
        s, e = float(_d(_g(r, "sales"))), float(_d(_g(r, "expenses")))
        if s <= 0:
            continue
        ref = central_company_metrics(s, 0, 0, 0, e)
        if _g(r, "margin") is not None and abs(Decimal(str(_g(r, "margin"))) - Decimal(str(ref["margin"]))) > T["margin_tol"]:
            bad_margin.append(_g(r, "id"))
        if _g(r, "profit") is not None and abs(Decimal(str(_g(r, "profit"))) - Decimal(str(ref["profit"]))) > Decimal("0.01"):
            bad_total.append(_g(r, "id"))
    add("incorrect_margin", "medium", bad_margin,
        "{n} سجل هامشه المخزّن لا يطابق المبيعات والمصروفات", "{n} record(s) whose stored margin does not match sales/expenses",
        "Re-save these entries so margin is recalculated.")
    add("missing_branch_info", "medium",
        [_g(r, "id") for r in all_rows or [] if _g(r, "branch_id") not in branch_map],
        "{n} سجل لفرع غير نشط أو غير موجود (مستبعد)", "{n} record(s) linked to an inactive/unknown branch (excluded)",
        "Reactivate the branch or reassign the records.")
    dep = [_g(r, "id") for r in cur if _d(_g(r, "deposited")) > 0 and _d(_g(r, "sales")) > 0
           and abs(_d(_g(r, "deposited")) - _d(_g(r, "sales"))) / _d(_g(r, "sales")) * 100 > T["deposit_gap"]]
    add("inconsistent_totals", "high", bad_total + dep,
        "{n} سجل بإجماليات غير متسقة (ربح أو إيداع لا يطابق المبيعات)", "{n} record(s) with inconsistent totals (profit or deposits)",
        "Reconcile deposits with sales and re-save profit.")
    for gap in period_gaps or []:
        w.append({"code": gap["missing"], "rule_code": gap["missing"], "texts": rule_texts(gap["missing"]),
                  "severity": "medium", "count": 1, "affected_ids": [],
                  "message_ar": gap["why_it_matters"], "message_en": gap["why_it_matters"], "fix": gap["required_data"]})
    return w


def quality_status(warnings):
    if any(x["severity"] == "high" for x in warnings):
        return "warning" if len(warnings) < 3 else "fail"
    return "warning" if warnings else "pass"


# ═══════════════════════ 2. RISKS & OPPORTUNITIES ═══════════════════════
def _branch_stats(rows, branch_map):
    out = {}
    for r in rows or []:
        b = _g(r, "branch_id")
        if b not in branch_map:
            continue
        st = out.setdefault(b, {"sales": Decimal(0), "expenses": Decimal(0), "customers": Decimal(0), "repeat": Decimal(0)})
        st["sales"] += _d(_g(r, "sales")); st["expenses"] += _d(_g(r, "expenses"))
        st["customers"] += _d(_g(r, "customers")); st["repeat"] += _d(_g(r, "repeat_customers"))
    return out


def _totals(stats):
    t = {"sales": Decimal(0), "expenses": Decimal(0), "customers": Decimal(0), "repeat": Decimal(0)}
    for st in stats.values():
        for k in t:
            t[k] += st[k]
    return t


def detect_signals(current_rows, comparison_rows, branch_map, *, period=None, company_target_margin=None,
                   inventory=None, quality_warnings=None, currency="SAR"):
    """Returns {"risks": [...], "opportunities": [...], "not_evaluated": [...]}."""
    cur_b, prev_b = _branch_stats(current_rows, branch_map), _branch_stats(comparison_rows, branch_map)
    cur, prev = _totals(cur_b), _totals(prev_b)
    has_prev = bool(prev_b) and prev["sales"] > 0
    margin = _pct(cur["sales"] - cur["expenses"], cur["sales"])
    risks, opps, not_eval = [], [], []

    def sig(kind, code, name_ar, name_en, *, branch=None, metric, current, reference, ref_type, severity,
            method, impact=None, impact_formula=None, action_ar, action_en, desc_ar, category, rule=None):
        conf = "high" if has_prev and not quality_warnings else ("medium" if has_prev or not quality_warnings else "low")
        item = {"id": _sid(kind, code, branch, period), "type": kind, "code": code, "name_ar": name_ar, "name_en": name_en,
                "description_ar": desc_ar, "category": category, "branch_id": branch,
                "branch_name": branch_map.get(branch) if branch else None, "metric_id": metric,
                "current_value": None if current is None else float(current),
                "reference_value": None if reference is None else float(reference), "reference_type": ref_type,
                "deviation": None if current is None or reference is None else float(round_pct(current - reference)),
                "severity": severity, "method": method, "confidence": conf,
                "estimated_impact": None if impact is None else {**_money(impact), "currency": currency,
                                                                  "is_estimate": True, "formula": impact_formula},
                "source": "companyentry", "period": period, "detected_at": _now(), "status": "open",
                "suggested_action_ar": action_ar, "suggested_action_en": action_en}
        rc = rule or code
        fv = lambda v: None if v is None else f"{float(v):,.2f}".rstrip("0").rstrip(".")
        item.update({"rule_code": rc, "technical_expression": method, "threshold": rule_threshold(rc),
                     "texts": rule_texts(rc, current=fv(current), reference=fv(reference),
                                         branch=branch_map.get(branch) if branch else "")})
        (risks if kind == "risk" else opps).append(item)

    # R1 declining sales
    if has_prev:
        g = _pct(cur["sales"] - prev["sales"], prev["sales"])
        if g is not None and g <= T["decline_medium"]:
            sev = "high" if g <= T["decline_high"] else "medium"
            sig("risk", "declining_sales", "تراجع المبيعات", "Declining sales", metric="growth", current=g, reference=Decimal(0),
                ref_type="previous_period", severity=sev, impact=cur["sales"] - prev["sales"], impact_formula="current − previous net sales",
                method=f"growth ≤ {T['decline_medium']}% (high ≤ {T['decline_high']}%)", category="sales",
                desc_ar=f"المبيعات تغيّرت {g}% عن الشهر السابق",
                action_ar="حلّل عدد الفواتير ومتوسطها لمعرفة مصدر التراجع", action_en="Split the drop into transactions vs basket size")
        elif g is not None and g >= T["momentum"]:
            sig("opportunity", "sales_momentum", "زخم نمو المبيعات", "Sales momentum", metric="growth", current=g, reference=Decimal(0),
                ref_type="previous_period", severity="medium", impact=cur["sales"] - prev["sales"], impact_formula="current − previous net sales",
                method=f"growth ≥ {T['momentum']}%", category="sales", desc_ar=f"نمو {g}% عن الشهر السابق",
                action_ar="حدّد الفروع والمنتجات المحرّكة للنمو ووسّعها", action_en="Identify and scale what drives the growth")
        for b, st in cur_b.items():
            p = prev_b.get(b)
            if p and p["sales"] > 0:
                bg = _pct(st["sales"] - p["sales"], p["sales"])
                if bg is not None and bg <= T["branch_decline"]:
                    sig("risk", "branch_decline", "تراجع أداء فرع", "Branch decline", branch=b, metric="growth", current=bg,
                        reference=Decimal(0), ref_type="previous_period", severity="medium", impact=st["sales"] - p["sales"],
                        impact_formula="branch current − previous sales", method=f"branch growth ≤ {T['branch_decline']}%",
                        category="branch", desc_ar=f"مبيعات الفرع تغيّرت {bg}%",
                        action_ar="راجع تشغيل الفرع مع مديره هذا الأسبوع", action_en="Review branch operations with its manager")
    else:
        not_eval.append({"code": "declining_sales", "reason": "no comparison period"})

    # R2 negative margin (company + branch)
    if margin is not None and margin < 0:
        sig("risk", "negative_margin", "هامش سالب", "Negative margin", metric="gross_margin", current=margin, reference=Decimal(0),
            ref_type="break_even", severity="critical", impact=cur["sales"] - cur["expenses"], impact_formula="sales − expenses",
            method="margin < 0%", category="profitability", desc_ar=f"الشركة تخسر: الهامش {margin}%",
            action_ar="أوقف النزيف: راجع أعلى ٣ بنود مصروفات فوراً", action_en="Review the top 3 expense lines now")
    for b, st in cur_b.items():
        bm = _pct(st["sales"] - st["expenses"], st["sales"])
        if bm is not None and bm < 0:
            sig("risk", "negative_margin", "فرع بهامش سالب", "Branch negative margin", branch=b, rule="negative_margin_branch", metric="gross_margin", current=bm,
                reference=Decimal(0), ref_type="break_even", severity="high", impact=st["sales"] - st["expenses"],
                impact_formula="branch sales − expenses", method="branch margin < 0%", category="profitability",
                desc_ar=f"الفرع يخسر: الهامش {bm}%", action_ar="خطة تصحيح للفرع خلال أسبوعين", action_en="Two-week recovery plan for the branch")
        elif bm is not None and margin is not None and bm < margin - T["branch_margin_gap"]:
            uplift = st["sales"] * (margin - bm) / 100
            sig("risk", "branch_underperformance", "فرع دون متوسط الهامش", "Branch below average margin", branch=b,
                metric="gross_margin", current=bm, reference=margin, ref_type="company_average", severity="medium",
                impact=-uplift, impact_formula="branch sales × (company margin − branch margin)",
                method=f"branch margin < company margin − {T['branch_margin_gap']} pts", category="branch",
                desc_ar=f"هامش الفرع {bm}% مقابل {margin}% للشركة",
                action_ar="قارن تكاليف الفرع بأفضل فرع", action_en="Benchmark branch costs against the best branch")
            sig("opportunity", "branch_margin_gap", "رفع هامش فرع للمتوسط", "Lift branch margin to average", branch=b,
                metric="gross_margin", current=bm, reference=margin, ref_type="company_average", severity="medium",
                impact=uplift, impact_formula="branch sales × (company margin − branch margin)",
                method="branch margin below company average", category="branch",
                desc_ar="أثر تقديري لو وصل الفرع لمتوسط هامش الشركة",
                action_ar="انقل ممارسات أفضل فرع لهذا الفرع", action_en="Transfer best-branch practices")

    # R3 high expenses
    er = _pct(cur["expenses"], cur["sales"])
    if er is not None and er >= T["expense_ratio_high"]:
        sig("risk", "high_expenses", "مصروفات مرتفعة", "High expenses", metric="expense_ratio", current=er,
            reference=T["expense_ratio_high"], ref_type="threshold", severity="high",
            impact=cur["sales"] * (er - T["expense_ratio_high"]) / 100, impact_formula="sales × (expense ratio − 90%)",
            method=f"expense ratio ≥ {T['expense_ratio_high']}%", category="cost", desc_ar=f"المصروفات {er}% من المبيعات",
            action_ar="راجع العقود والرواتب والإيجارات", action_en="Review contracts, payroll and rent")
    elif has_prev and prev["expenses"] > 0:
        eg = _pct(cur["expenses"] - prev["expenses"], prev["expenses"])
        sg = _pct(cur["sales"] - prev["sales"], prev["sales"])
        if eg is not None and sg is not None and eg - sg >= T["expense_growth_gap"]:
            sig("risk", "high_expenses", "المصروفات تنمو أسرع من المبيعات", "Expenses outgrowing sales", metric="expense_ratio", rule="expense_growth",
                current=eg, reference=sg, ref_type="sales_growth", severity="medium",
                impact=-(cur["expenses"] - prev["expenses"] * (1 + sg / 100)),
                impact_formula="actual expenses − expenses grown at sales rate",
                method=f"expense growth − sales growth ≥ {T['expense_growth_gap']} pts", category="cost",
                desc_ar=f"المصروفات +{eg}% مقابل مبيعات {sg}%", action_ar="حدّد البند الذي قفز", action_en="Find the line item that jumped")
    if company_target_margin and margin is not None and margin < Decimal(str(company_target_margin)):
        sig("risk", "margin_below_target", "الهامش أقل من المستهدف", "Margin below target", metric="gross_margin",
            current=margin, reference=Decimal(str(company_target_margin)), ref_type="target", severity="medium",
            impact=cur["sales"] * (margin - Decimal(str(company_target_margin))) / 100,
            impact_formula="sales × (margin − target margin)", method="margin < company target_margin", category="profitability",
            desc_ar=f"الهامش {margin}% والمستهدف {company_target_margin}%", action_ar="حدّد فجوة الهامش حسب الفرع",
            action_en="Break the margin gap down by branch")

    # R4/R5 missing & inconsistent data (from quality layer)
    for q in quality_warnings or []:
        if q["code"] in ("missing_values", "comparison_period_data", "current_period_data", "valid_period"):
            code, name_ar, name_en, cat = "missing_data", "بيانات ناقصة", "Missing data", "data"
        elif q["code"] in ("inconsistent_totals", "incorrect_margin", "duplicate_entries", "invalid_values"):
            code, name_ar, name_en, cat = "inconsistent_data", "بيانات غير متسقة", "Inconsistent data", "data"
        else:
            continue
        sig("risk", code, name_ar, name_en, metric="data_quality", current=Decimal(q["count"]), reference=Decimal(0),
            ref_type="expected_clean", severity=q["severity"], method=f"data quality check: {q['code']}", category=cat,
            desc_ar=q["message_ar"], action_ar=q["fix"], action_en=q["fix"])

    # R6 high inventory (only if inventory data exists)
    inv_val, cogs = to_decimal((inventory or {}).get("value")), to_decimal((inventory or {}).get("cogs"))
    if inv_val and cogs and cogs > 0:
        dio = round_pct(inv_val / (cogs / 30))
        if dio >= T["dio_high"]:
            sig("risk", "high_inventory", "مخزون مرتفع", "High inventory", metric="dio", current=dio, reference=T["dio_high"],
                ref_type="threshold", severity="medium", impact=inv_val - (cogs / 30) * T["dio_high"],
                impact_formula="inventory value − 60 days of COGS (cash tied up)", method=f"days of inventory ≥ {T['dio_high']}",
                category="inventory", desc_ar=f"المخزون يكفي {dio} يوم", action_ar="خفّض طلبات الأصناف البطيئة",
                action_en="Cut orders for slow items")
    else:
        not_eval.append({"code": "high_inventory", "reason": "no inventory value/COGS data"})

    # R7 low repeat purchase
    if cur["customers"] > 0:
        rr = _pct(cur["repeat"], cur["customers"])
        if rr is not None and rr < T["repeat_low"]:
            sig("risk", "low_repeat", "تكرار شراء منخفض", "Low repeat purchase", metric="repeat_rate", current=rr,
                reference=T["repeat_low"], ref_type="threshold", severity="medium", method=f"repeat rate < {T['repeat_low']}%",
                category="customers", desc_ar=f"نسبة العملاء المتكررين {rr}%",
                action_ar="أطلق برنامج ولاء بسيط لمدة شهر", action_en="Run a one-month loyalty test")
    else:
        not_eval.append({"code": "low_repeat", "reason": "no customer counts"})

    # R8 branch vs target / O target overachievement
    for b, st in cur_b.items():
        tgt = to_decimal(branch_map.get(("target", b)))
        if tgt and tgt > 0:
            att = _pct(st["sales"], tgt)
            if att is not None and att < T["branch_target_low"]:
                sig("risk", "branch_below_target", "فرع دون الهدف", "Branch below target", branch=b, metric="net_sales",
                    current=st["sales"], reference=tgt, ref_type="target", severity="high", impact=st["sales"] - tgt,
                    impact_formula="branch sales − target", method=f"attainment < {T['branch_target_low']}%", category="branch",
                    desc_ar=f"الفرع حقق {att}% من هدفه", action_ar="راجع هدف الفرع وخطة المبيعات",
                    action_en="Review the branch target and sales plan")
            elif att is not None and att >= T["target_over"]:
                sig("opportunity", "target_overachievement", "فرع يتجاوز هدفه", "Branch beating target", branch=b,
                    metric="net_sales", current=st["sales"], reference=tgt, ref_type="target", severity="low",
                    impact=st["sales"] - tgt, impact_formula="branch sales − target", method=f"attainment ≥ {T['target_over']}%",
                    category="branch", desc_ar=f"الفرع حقق {att}% من هدفه",
                    action_ar="وثّق ما يفعله الفرع وعمّمه", action_en="Document and replicate the branch practices")

    key = lambda x: (-SEV_RANK.get(x["severity"], 0), -abs((x["estimated_impact"] or {}).get("value", 0)))
    return {"risks": sorted(risks, key=key), "opportunities": sorted(opps, key=key), "not_evaluated": not_eval}


# ═══════════════════════ 3. RECOMMENDATIONS ═══════════════════════
PRIORITY = {"critical": "P1", "high": "P1", "medium": "P2", "low": "P3"}
DUE_DAYS = {"P1": 7, "P2": 14, "P3": 30}


def build_recommendations(signals, gate_status="QUALIFY", limit=8):
    recs = []
    for s in (signals["risks"] + signals["opportunities"])[:limit]:
        pr = PRIORITY.get(s["severity"], "P3")
        blocked = gate_status == "BLOCK" and s["category"] != "data"
        recs.append({
            "id": "rec-" + s["id"], "signal_id": s["id"], "type": s["type"],
            "problem_ar": s["name_ar"] + (f" — {s['branch_name']}" if s.get("branch_name") else ""),
            "problem_en": s["name_en"],
            "evidence": {"metric_id": s["metric_id"], "current": s["current_value"], "reference": s["reference_value"],
                         "reference_type": s["reference_type"], "rule": s["method"], "period": s["period"],
                         "source": s["source"], "detail_ar": s["description_ar"]},
            "recommendation_ar": s["suggested_action_ar"], "recommendation_en": s["suggested_action_en"],
            "priority": pr,
            "expected_impact": None if blocked else s["estimated_impact"],
            "confidence": "low" if blocked else s["confidence"],
            "suggested_action": {"title_ar": s["suggested_action_ar"], "owner_role": "manager" if s.get("branch_id") else "owner",
                                 "due_in_days": DUE_DAYS[pr]},
            "success_metric": f"{s['metric_id']} moves toward {s['reference_type']} ({s['reference_value']})",
            "status": "requires_data_first" if blocked else "proposed",
            "limitations": ["Data quality gate BLOCK: complete the data before acting on financial figures."] if blocked else [],
            "generated_by": f"rules-v{INTEL_VERSION}",
            "rule_code": s["rule_code"], "texts": s["texts"], "technical_expression": s["technical_expression"],
            "threshold": s["threshold"], "branch_id": s.get("branch_id"), "metric_id": s["metric_id"],
            "problem_type": s["code"], "severity": s["severity"],
        })
    return recs


# ═══════════════════════ 4. EXECUTIVE ASSEMBLY ═══════════════════════
def build_executive(*, all_rows, current_rows, comparison_rows, branches, period, comparison_period,
                    period_gaps=None, company_target_margin=None, inventory=None, currency="SAR",
                    open_decisions=0, overdue_actions=0):
    branch_map = {_g(b, "id"): _g(b, "name") for b in branches}
    for b in branches:
        branch_map[("target", _g(b, "id"))] = _g(b, "target_sales")
    names = {k: v for k, v in branch_map.items() if not isinstance(k, tuple)}
    qw = run_quality_checks(all_rows, current_rows, names, period_gaps)
    qstat = quality_status(qw)
    gate = "BLOCK" if qstat == "fail" or not current_rows else ("QUALIFY" if qstat == "warning" else "ALLOW")
    sigs = detect_signals(current_rows, comparison_rows, branch_map, period=period,
                          company_target_margin=company_target_margin, inventory=inventory,
                          quality_warnings=qw, currency=currency)
    recs = build_recommendations(sigs, gate)

    cur_b = _branch_stats(current_rows, names)
    prev_b = _branch_stats(comparison_rows, names)
    tot, ptot = _totals(cur_b), _totals(prev_b)
    has_data = tot["sales"] > 0
    profit = tot["sales"] - tot["expenses"]
    margin = _pct(profit, tot["sales"])
    target_rev = sum((to_decimal(_g(b, "target_sales")) or Decimal(0)) for b in branches)
    rev_vs_target = {"status": "not_set"} if not target_rev else {
        "status": "ok", "actual": _money(tot["sales"]), "target": _money(target_rev),
        "gap": _money(tot["sales"] - target_rev), "attainment_pct": float(_pct(tot["sales"], target_rev))}
    tm = to_decimal(company_target_margin)
    profit_vs_target = {"status": "not_set"} if not (target_rev and tm) else {
        "status": "ok", "actual": _money(profit), "target": _money(target_rev * tm / 100),
        "gap": _money(profit - target_rev * tm / 100), "basis": "revenue target × target margin"}
    branch_rows = []
    for b in branches:
        bid = _g(b, "id")
        st, p = cur_b.get(bid), prev_b.get(bid)
        tgt = to_decimal(_g(b, "target_sales"))
        branch_rows.append({
            "branch_id": bid, "name": _g(b, "name"), "has_data": st is not None,
            "sales": _money(st["sales"]) if st else None,
            "margin": float(_pct(st["sales"] - st["expenses"], st["sales"])) if st and st["sales"] > 0 else None,
            "growth": float(_pct(st["sales"] - p["sales"], p["sales"])) if st and p and p["sales"] > 0 else None,
            "target_attainment": float(_pct(st["sales"], tgt)) if st and tgt else None})
    branch_rows.sort(key=lambda x: -(x["sales"] or {}).get("value", 0))
    fc = forecast_company(all_rows_in_branches(all_rows, names), branches, currency)
    top = sigs["risks"][:3]
    return {
        "version": f"exec-intel-v{INTEL_VERSION}", "generated_at": _now(), "currency": currency,
        "period": period, "comparison_period": comparison_period, "has_data": has_data,
        "summary": {
            "net_sales": _money(tot["sales"]) if has_data else None,
            "growth_pct": float(_pct(tot["sales"] - ptot["sales"], ptot["sales"])) if has_data and ptot["sales"] > 0 else None,
            "profit": _money(profit) if has_data else None, "margin_pct": float(margin) if margin is not None else None,
            "risk_counts": {s: sum(1 for r in sigs["risks"] if r["severity"] == s) for s in ("critical", "high", "medium", "low")},
            "top_problems_ar": [r["name_ar"] + (f" — {r['branch_name']}" if r.get("branch_name") else "") for r in top],
            "top_opportunities_ar": [o["name_ar"] for o in sigs["opportunities"][:3]],
            "open_decisions": open_decisions, "overdue_actions": overdue_actions},
        "revenue_vs_target": rev_vs_target, "profit_vs_target": profit_vs_target,
        "margin": {"actual_pct": float(margin) if margin is not None else None,
                   "target_pct": float(tm) if tm else None},
        "branch_performance": branch_rows,
        "risks": sigs["risks"], "opportunities": sigs["opportunities"], "not_evaluated": sigs["not_evaluated"],
        "recommendations": recs,
        "data_quality": {"status": qstat, "gate": gate, "warnings": qw},
        "forecast": fc,
        "labels": {"actual": "companyentry values for the period", "estimate": "forecasts and estimated impacts"},
    }


def all_rows_in_branches(rows, names):
    return [r for r in rows or [] if _g(r, "branch_id") in names]
