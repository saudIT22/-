"""
NABBAH 2.3 — Decision Memory & Outcomes (pure, no AI).

Outcome statuses:
  pending_measurement : no complete month after the decision yet
  insufficient_data   : a later month exists but has no usable data for the KPI
  measured            : value computed, but not every branch in scope reported (partial)
  verified            : complete month after the decision, all branches in scope reported,
                        supported KPI, baseline recorded. Means the MEASUREMENT is reliable,
                        not that the decision caused the change.
  not_verified        : invalid measurement (unsupported/missing KPI or baseline)
  cancelled           : decision cancelled
Similarity is rule-based and explained; it is never a causal claim.
"""
SUPPORTED_KPIS = ("net_sales", "gross_margin", "growth", "expense_ratio", "repeat_rate")
try:
    from rule_catalog import KPI as _KPI
except Exception:  # pragma: no cover
    _KPI = {}


def _kpi_name(metric, lang):
    pair = _KPI.get(metric)
    return pair[0 if lang == "ar" else 1] if pair else (metric or "—")
CAVEAT = {"ar": "النتائج السابقة تُظهر ما حدث بعد القرار، ولا تثبت أن القرار هو السبب. الظروف قد تختلف.",
          "en": "Past results show what happened after a decision; they do not prove the decision caused it. Conditions may differ."}


def _g(d, k, default=None):
    v = d.get(k) if isinstance(d, dict) else getattr(d, k, None)
    return default if v is None else v


def _kpi_value(metric, rows, prev_rows):
    s = sum(float(r.get("sales") or 0) for r in rows)
    e = sum(float(r.get("expenses") or 0) for r in rows)
    cu = sum(float(r.get("customers") or 0) for r in rows)
    rp = sum(float(r.get("repeat_customers") or 0) for r in rows)
    ps = sum(float(r.get("sales") or 0) for r in prev_rows or [])
    if metric == "net_sales":
        return s if rows else None
    if metric == "gross_margin":
        return round((s - e) / s * 100, 2) if s else None
    if metric == "expense_ratio":
        return round(e / s * 100, 2) if s else None
    if metric == "repeat_rate":
        return round(rp / cu * 100, 2) if cu else None
    if metric == "growth":
        return round((s - ps) / ps * 100, 2) if ps else None
    return None


def evaluate_measurement(decision, month_split, scope_branch_ids, decision_month, measured_month):
    """month_split: split_by_period result for measured_month. Returns an outcome dict (no DB)."""
    base = {"measurement_period": measured_month, "data_source": "companyentry", "caveat": CAVEAT}
    if _g(decision, "status") == "cancelled":
        return {**base, "outcome_status": "cancelled", "reason_code": "decision_cancelled"}
    metric, baseline = _g(decision, "metric_id", ""), _g(decision, "baseline_value")
    if metric not in SUPPORTED_KPIS:
        return {**base, "outcome_status": "not_verified", "reason_code": "unsupported_kpi"}
    if baseline is None:
        return {**base, "outcome_status": "not_verified", "reason_code": "missing_baseline"}
    if not measured_month or not decision_month or measured_month <= decision_month:
        return {**base, "outcome_status": "pending_measurement", "reason_code": "no_later_month"}
    cur = month_split.get("current_entries") or []
    val = _kpi_value(metric, cur, month_split.get("comparison_entries"))
    if val is None:
        return {**base, "outcome_status": "insufficient_data", "reason_code": "kpi_not_computable"}
    reported = {r.get("branch_id") for r in cur}
    missing = sorted(set(scope_branch_ids) - reported)
    status = "measured" if missing else "verified"
    return {**base, "outcome_status": status, "reason_code": "partial_branches" if missing else "complete_month",
            "missing_branches": missing, "actual_value": val, "actual_change": round(val - float(baseline), 2)}


def find_similar(target, candidates, limit=5):
    """Rule-based similarity with the reasons shown. Same company is enforced by the caller."""
    out = []
    for c in candidates:
        if _g(c, "id") == _g(target, "id"):
            continue
        score, why = 0, []
        if _g(target, "metric_id") and _g(c, "metric_id") == _g(target, "metric_id"):
            score += 3; why.append("same_kpi")
        if _g(target, "problem_type") and _g(c, "problem_type") == _g(target, "problem_type"):
            score += 3; why.append("same_problem")
        if _g(target, "branch_id") and _g(c, "branch_id") == _g(target, "branch_id"):
            score += 2; why.append("same_branch")
        if _g(target, "decision_type") and _g(c, "decision_type") == _g(target, "decision_type"):
            score += 1; why.append("same_decision_type")
        if score >= 3:
            out.append({"id": _g(c, "id"), "title": _g(c, "title", ""), "score": score, "reasons": why,
                        "metric_id": _g(c, "metric_id", ""), "branch_id": _g(c, "branch_id"),
                        "problem_type": _g(c, "problem_type", ""), "created_at": str(_g(c, "created_at", "")),
                        "expected_impact_value": _g(c, "expected_impact_value"),
                        "actual_value": _g(c, "actual_value"), "actual_change": _g(c, "actual_impact_value"),
                        "baseline_value": _g(c, "baseline_value"),
                        "outcome_status": _g(c, "outcome_status") or "pending_measurement",
                        "measurement_period": _g(c, "measurement_period", "")})
    out.sort(key=lambda x: (x["score"], x["created_at"]), reverse=True)
    return {"items": out[:limit], "caveat": CAVEAT, "method": "rule-based: kpi +3, problem +3, branch +2, type +1; min 3"}


def summarize(decision):
    """What was expected / measured / still unknown / missing — AR + EN."""
    st = _g(decision, "outcome_status") or "pending_measurement"
    exp = _g(decision, "expected_impact_value")
    ar, en = {"expected": [], "measured": [], "unknown": [], "missing": []}, {"expected": [], "measured": [], "unknown": [], "missing": []}
    if exp is not None:
        ar["expected"].append(f"أثر تقديري {exp} على {_kpi_name(_g(decision, 'metric_id', ''), 'ar')}"); en["expected"].append(f"Estimated impact {exp} on {_kpi_name(_g(decision, 'metric_id', ''), 'en')}")
    else:
        ar["unknown"].append("لم يُحدَّد أثر متوقع رقمي"); en["unknown"].append("No numeric expected impact was set")
    if st in ("verified", "measured"):
        ar["measured"].append(f"القيمة بعد القرار {_g(decision, 'actual_value')} (التغيّر {_g(decision, 'actual_impact_value')}) في {_g(decision, 'measurement_period', '')}")
        en["measured"].append(f"Value after the decision {_g(decision, 'actual_value')} (change {_g(decision, 'actual_impact_value')}) in {_g(decision, 'measurement_period', '')}")
        ar["unknown"].append("هل القرار هو سبب التغيّر؟ غير مثبت"); en["unknown"].append("Whether the decision caused the change is not proven")
    if st == "measured":
        ar["missing"].append("بعض الفروع لم ترسل بيانات شهر القياس"); en["missing"].append("Some branches did not report the measurement month")
    if st in ("pending_measurement", "insufficient_data"):
        ar["unknown"].append("الأثر الفعلي لم يُقَس بعد"); en["unknown"].append("The actual impact has not been measured yet")
        ar["missing"].append("لا يوجد شهر كامل من البيانات بعد القرار"); en["missing"].append("No complete month of data after the decision yet")
    if st == "not_verified":
        ar["missing"].append("المؤشر أو خط الأساس غير صالح للقياس"); en["missing"].append("The KPI or baseline is not valid for measurement")
    return {"status": st, "ar": ar, "en": en}
