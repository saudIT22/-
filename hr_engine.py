"""
NABBAH 2.9 — People Intelligence engine (pure, deterministic; AI only explains).
People → Cost → Performance → Productivity → Risk → Decision.
Missing ≠ zero · indicators, not predictions (flight risk shows evidence + confidence) · salaries are
sensitive: the engine returns them, the API strips them for roles without payroll access.
"""
import os, sys, hashlib, calendar
from datetime import date, timedelta
from decimal import Decimal

_here = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21", "../phase22", "../phase23", "../phase24", "../phase25", "../phase26", "../phase27", "../phase28"):
    _p = os.path.join(_here, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from nabbah_finance import to_decimal, round_money, round_pct, safe_divide

HR_VERSION = "1.0"
D0 = Decimal("0")
RULES = {"turnover_high_factor": Decimal("1.5"), "min_leavers": 2, "high_perf": 80, "low_perf": 60,
         "promotion_years": 3, "absence_high_days": 8, "cost_change_pct": 5, "low_training_hours": 4,
         "flight_min_indicators": 2}
LEFT_WORDS = ("مستقيل", "منتهي", "مغادر", "مفصول", "left", "resigned", "terminated", "inactive", "غير نشط")
VOLUNTARY = ("استقال", "مستقيل", "طوعي", "resign", "voluntary")
INVOLUNTARY = ("فصل", "مفصول", "إنهاء", "انهاء", "غير طوعي", "terminated", "involuntary", "dismiss")


def _d(v):
    return to_decimal(v) if v not in (None, "") else None


def _money(v):
    return None if v is None else {"value": float(round_money(v)), "value_decimal": str(round_money(v))}


def _num(v, p=2):
    return None if v is None else float(round_pct(v, p))


def _pct(n, d):
    r = safe_divide(n, d)
    return None if r is None else float(round_pct(r * 100))


def _date(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _norm(s):
    return str(s or "").strip().lower().replace("أ", "ا").replace("إ", "ا").replace("ة", "ه").replace("ى", "ي")


def _sid(*p):
    return "hr-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def _month_end(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _month_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _perf(v):
    """تقييم الأداء إلى 0–100: مقياس 1–5 يُضرب في 20؛ نسبة مئوية كما هي."""
    x = _d(v)
    if x is None:
        return None
    return x * 20 if x <= 5 else min(x, Decimal(100))


def _cost(e):
    c = _d(e.get("monthly_cost"))
    if c is not None:
        return c
    parts = [_d(e.get(k)) for k in ("basic_salary", "allowances", "benefits")]
    return sum((p for p in parts if p is not None), D0) if any(p is not None for p in parts) else None


def analyze_hr(employees, *, openings=None, period=None, today=None, sales_rows=None, payroll_cash=None,
               currency="SAR", rules=None):
    """employees: [{employee_code, name, branch_name, department, role, employment_status, employment_type, manager,
                    hire_date, termination_date, termination_type, monthly_cost|basic_salary+allowances+benefits,
                    performance_rating, last_promotion_date, training_hours, absence_days, overtime_hours,
                    critical_role, successors}]"""
    R = dict(RULES, **(rules or {}))
    openings, sales_rows = openings or [], sales_rows or []
    emps = [dict(e) for e in employees if e.get("employee_code") or e.get("name")]
    if not emps:
        return {"has_data": False, "version": f"hr-v{HR_VERSION}",
                "message_ar": "لا توجد بيانات موظفين بعد. ارفع ملف الموظفين من مركز البيانات.",
                "required": {"required": ["رقم الموظف", "اسم الموظف"],
                             "recommended": ["الفرع", "القسم", "الوظيفة", "تاريخ التعيين", "تاريخ انتهاء الخدمة", "الراتب"],
                             "optional": ["تقييم الأداء", "ساعات التدريب", "أيام الغياب", "آخر ترقية", "وظيفة حرجة/البدلاء",
                                          "نوع التوظيف", "المدير المباشر", "ملف الوظائف الشاغرة"]}}
    today = today or date.today()
    cur = period or today.strftime("%Y-%m")
    as_of = min(_month_end(cur), today) if cur == today.strftime("%Y-%m") else _month_end(cur)
    for e in emps:
        e["_hire"], e["_term"] = _date(e.get("hire_date")), _date(e.get("termination_date"))
        st = _norm(e.get("employment_status"))
        e["_left_no_date"] = e["_term"] is None and any(_norm(w) in st for w in LEFT_WORDS)
        e["_cost"], e["_perf"] = _cost(e), _perf(e.get("performance_rating"))
        e["_dept"] = e.get("department") or "غير محدد"
        e["_branch"] = e.get("branch_name") or None

    def active_at(d):
        return [e for e in emps if not e["_left_no_date"] and (e["_hire"] is None or e["_hire"] <= d)
                and (e["_term"] is None or e["_term"] > d)]
    act = active_at(as_of)
    unknown_hire = sum(1 for e in emps if e["_hire"] is None)
    m_start = date(int(cur[:4]), int(cur[5:7]), 1)
    joiners = [e for e in emps if e["_hire"] and m_start <= e["_hire"] <= as_of]
    leavers = [e for e in emps if e["_term"] and m_start <= e["_term"] <= as_of]
    y_start = as_of - timedelta(days=364)
    leavers12 = [e for e in emps if e["_term"] and y_start <= e["_term"] <= as_of]
    joiners12 = [e for e in emps if e["_hire"] and y_start <= e["_hire"] <= as_of]
    months12 = [_month_add(cur, -k) for k in range(11, -1, -1)]
    hc_series = [len(active_at(min(_month_end(ym), as_of))) for ym in months12]
    avg_hc = Decimal(sum(hc_series)) / len(hc_series) if hc_series else None
    turnover12 = _pct(Decimal(len(leavers12)), avg_hc) if avg_hc else None
    prev_as_of = _month_end(_month_add(cur, -1))
    hc_prev = len(active_at(prev_as_of))

    def group(items, key):
        g = {}
        for e in items:
            g.setdefault(key(e) or "غير محدد", []).append(e)
        return g

    def turnover_of(filt):
        lv = [e for e in leavers12 if filt(e)]
        hs = [len([e for e in active_at(min(_month_end(ym), as_of)) if filt(e)]) for ym in months12]
        avg = Decimal(sum(hs)) / len(hs) if hs else D0
        return {"leavers": len(lv), "avg_headcount": _num(avg, 1), "rate": _pct(Decimal(len(lv)), avg) if avg else None}

    # ── القوى العاملة والهيكل
    types = group(act, lambda e: e.get("employment_type"))
    workforce = {"active": len(act), "previous": hc_prev, "change": len(act) - hc_prev, "joiners": len(joiners),
                 "leavers": len(leavers), "leavers_no_date": sum(1 for e in emps if e["_left_no_date"]),
                 "unknown_hire_date": unknown_hire,
                 "by_type": [{"key": k, "count": len(v)} for k, v in sorted(types.items(), key=lambda x: -len(x[1]))]
                 if any(e.get("employment_type") for e in act) else None,
                 "by_branch": [{"key": k, "count": len(v)} for k, v in sorted(group(act, lambda e: e["_branch"]).items(), key=lambda x: -len(x[1]))],
                 "by_department": [{"key": k, "count": len(v)} for k, v in sorted(group(act, lambda e: e["_dept"]).items(), key=lambda x: -len(x[1]))],
                 "by_role": [{"key": k, "count": len(v)} for k, v in sorted(group(act, lambda e: e.get("role")).items(), key=lambda x: -len(x[1]))][:25],
                 "trend": [{"period": ym, "headcount": h} for ym, h in zip(months12, hc_series)]}
    structure = []
    for dep, es in sorted(group(act, lambda e: e["_dept"]).items(), key=lambda x: -len(x[1])):
        roles = group(es, lambda e: e.get("role"))
        structure.append({"department": dep, "count": len(es),
                          "managers": sorted({e.get("manager") for e in es if e.get("manager")}),
                          "branches": sorted({e["_branch"] for e in es if e["_branch"]}),
                          "roles": [{"role": r, "count": len(v), "employees": [{"code": x.get("employee_code"), "name": x.get("name"),
                                                                              "branch": x["_branch"]} for x in v][:50]}
                                    for r, v in sorted(roles.items(), key=lambda x: -len(x[1]))]})

    # ── الدوران
    vol = [e for e in leavers12 if any(_norm(w) in _norm(e.get("termination_type")) for w in VOLUNTARY)]
    invol = [e for e in leavers12 if any(_norm(w) in _norm(e.get("termination_type")) for w in INVOLUNTARY)]
    has_type = any(e.get("termination_type") for e in leavers12)
    turnover = {"rate_12m": turnover12, "method_ar": "المغادرون خلال 12 شهراً ÷ متوسط عدد الموظفين × 100",
                "leavers_12m": len(leavers12), "joiners_12m": len(joiners12),
                "voluntary": len(vol) if has_type else None, "involuntary": len(invol) if has_type else None,
                "type_note_ar": None if has_type else "نوع المغادرة غير مسجّل — لا يمكن فصل الطوعي عن غير الطوعي",
                "by_branch": [], "by_department": [], "by_role": [],
                "trend": [{"period": ym, "leavers": sum(1 for e in emps if e["_term"] and e["_term"].strftime("%Y-%m") == ym),
                           "joiners": sum(1 for e in emps if e["_hire"] and e["_hire"].strftime("%Y-%m") == ym)} for ym in months12],
                "note_ar": None if not workforce["leavers_no_date"] else
                f"{workforce['leavers_no_date']} موظفاً حالتهم «مغادر» بلا تاريخ انتهاء خدمة — لا يدخلون في معدل الدوران"}
    for key, fn in (("by_branch", lambda e: e["_branch"]), ("by_department", lambda e: e["_dept"]), ("by_role", lambda e: e.get("role"))):
        for k in sorted({fn(e) for e in emps if fn(e)}):
            t = turnover_of(lambda e, k=k, fn=fn: fn(e) == k)
            turnover[key].append({"key": k, **t})
        turnover[key].sort(key=lambda x: -(x["rate"] or 0))

    # ── الاستقرار والحضور
    def avail(field):
        return [e for e in act if _d(e.get(field)) is not None]
    ab, ot = avail("absence_days"), avail("overtime_hours")
    tenure = [(as_of - e["_hire"]).days / 365.25 for e in act if e["_hire"]]
    stability = {"absence": {"available": bool(ab), "total_days": _num(sum((_d(e["absence_days"]) for e in ab), D0), 1),
                             "avg_per_employee": _num(safe_divide(sum((_d(e["absence_days"]) for e in ab), D0), Decimal(len(ab))), 1) if ab else None,
                             "by_department": [{"key": k, "avg": _num(sum((_d(x["absence_days"]) for x in v), D0) / len(v), 1)}
                                               for k, v in group(ab, lambda e: e["_dept"]).items()]},
                 "overtime": {"available": bool(ot), "total_hours": _num(sum((_d(e["overtime_hours"]) for e in ot), D0), 1),
                              "avg_per_employee": _num(sum((_d(e["overtime_hours"]) for e in ot), D0) / len(ot), 1) if ot else None},
                 "tenure": {"avg_years": round(sum(tenure) / len(tenure), 1) if tenure else None,
                            "buckets": [{"bucket": lab, "count": sum(1 for t in tenure if lo <= t < hi)}
                                        for lo, hi, lab in ((0, 1, "أقل من سنة"), (1, 3, "1–3 سنوات"), (3, 5, "3–5 سنوات"), (5, 99, "+5 سنوات"))]},
                 "attendance_note_ar": "الحضور اليومي غير مُتتبَّع في نبّاه — تُعرض أيام الغياب والعمل الإضافي المرفوعة فقط"}

    # ── الأداء
    rated = [e for e in act if e["_perf"] is not None]

    def band(p):
        return "high" if p >= R["high_perf"] else ("low" if p < R["low_perf"] else "average")
    perf = {"available": bool(rated)}
    if rated:
        cnt = {b: sum(1 for e in rated if band(e["_perf"]) == b) for b in ("high", "average", "low")}
        avg_cost = lambda es: _money(safe_divide(sum((e["_cost"] for e in es if e["_cost"] is not None), D0),
                                                 Decimal(sum(1 for e in es if e["_cost"] is not None)))) if any(e["_cost"] is not None for e in es) else None
        perf.update({"rated": len(rated), "coverage_pct": _pct(Decimal(len(rated)), Decimal(len(act))),
                     "average": _num(sum((e["_perf"] for e in rated), D0) / len(rated), 1),
                     "distribution": [{"band": b, "count": cnt[b], "pct": _pct(Decimal(cnt[b]), Decimal(len(rated))),
                                       "avg_cost": avg_cost([e for e in rated if band(e["_perf"]) == b])} for b in ("high", "average", "low")],
                     "rule_ar": f"مرتفع ≥ {R['high_perf']} · منخفض < {R['low_perf']} (مقياس 1–5 يُحوّل إلى 100)",
                     "by_department": [], "by_branch": [], "by_role": []})
        for key, fn in (("by_department", lambda e: e["_dept"]), ("by_branch", lambda e: e["_branch"]), ("by_role", lambda e: e.get("role"))):
            for k, v in group(rated, fn).items():
                perf[key].append({"key": k, "avg": _num(sum((x["_perf"] for x in v), D0) / len(v), 1), "rated": len(v)})
            perf[key].sort(key=lambda x: -(x["avg"] or 0))

    # ── التعويضات (حساسة)
    costed = [e for e in act if e["_cost"] is not None]
    comp = {"available": bool(costed)}
    if costed:
        total = sum((e["_cost"] for e in costed), D0)
        comps = {k: sum((_d(e.get(k)) or D0 for e in act), D0) for k in ("basic_salary", "allowances", "benefits")}
        has_comp = any(_d(e.get(k)) is not None for e in act for k in comps)
        prev_act = [e for e in active_at(prev_as_of) if e["_cost"] is not None]
        prev_total = sum((e["_cost"] for e in prev_act), D0)
        costs = sorted(float(e["_cost"]) for e in costed)
        q = lambda p: costs[min(len(costs) - 1, int(p * (len(costs) - 1)))]
        comp.update({"monthly_total": _money(total), "annualized": _money(total * 12), "avg_per_employee": _money(total / len(costed)),
                     "coverage_pct": _pct(Decimal(len(costed)), Decimal(len(act))),
                     "components": {k: _money(v) for k, v in comps.items()} if has_comp else None,
                     "previous_month_total": _money(prev_total) if prev_act else None,
                     "change_pct": _pct(total - prev_total, prev_total) if prev_total else None,
                     "change_note_ar": "التغير ناتج عن تغيّر عدد الموظفين برواتبهم الحالية — تاريخ تعديل الرواتب غير متوفر",
                     "distribution": {"min": q(0), "p25": q(.25), "median": q(.5), "p75": q(.75), "max": q(1)},
                     "by_department": sorted([{"key": k, "total": _money(sum((x["_cost"] for x in v), D0)), "avg": _money(sum((x["_cost"] for x in v), D0) / len(v)), "count": len(v)}
                                              for k, v in group(costed, lambda e: e["_dept"]).items()], key=lambda x: -x["total"]["value"]),
                     "by_branch": sorted([{"key": k, "total": _money(sum((x["_cost"] for x in v), D0)), "avg": _money(sum((x["_cost"] for x in v), D0) / len(v)), "count": len(v)}
                                          for k, v in group(costed, lambda e: e["_branch"]).items()], key=lambda x: -x["total"]["value"]),
                     "payroll_cash": _money(_d(payroll_cash)) if payroll_cash is not None else None,
                     "payroll_cash_note_ar": "مقارنة بمدفوعات الرواتب الفعلية في التدفق النقدي للفترة" if payroll_cash is not None else None})

    # ── التعلم والتطوير
    tr = avail("training_hours")
    learning = {"available": bool(tr)}
    if tr:
        hours = sum((_d(e["training_hours"]) for e in tr), D0)
        learning.update({"total_hours": _num(hours, 1), "trained": sum(1 for e in tr if _d(e["training_hours"]) > 0),
                         "avg_per_employee": _num(hours / len(act), 1) if act else None,
                         "by_department": sorted([{"key": k, "avg": _num(sum((_d(x["training_hours"]) for x in v), D0) / len(v), 1), "count": len(v)}
                                                  for k, v in group(tr, lambda e: e["_dept"]).items()], key=lambda x: x["avg"]),
                         "completion_note_ar": "نسبة إكمال البرامج غير متاحة — تحتاج سجل البرامج التدريبية"})

    # ── الخلافة
    crit = [e for e in act if _norm(e.get("critical_role")) in ("1", "true", "نعم", "yes", "y", "حرج", "حرجه")]
    succession = {"available": bool(crit)}
    if crit:
        rows = [{"role": e.get("role"), "employee": e.get("name"), "branch": e["_branch"], "department": e["_dept"],
                 "successors": int(_d(e.get("successors")) or 0) if e.get("successors") not in (None, "") else None} for e in crit]
        no_succ = [r for r in rows if r["successors"] == 0]
        known = [r for r in rows if r["successors"] is not None]
        succession.update({"critical_roles": len(rows), "without_successor": len(no_succ),
                           "coverage_pct": _pct(Decimal(sum(1 for r in known if r["successors"] > 0)), Decimal(len(known))) if known else None,
                           "roles": sorted(rows, key=lambda r: (r["successors"] if r["successors"] is not None else -1))})

    # ── مؤشرات مخاطر الفقدان (ليست تنبؤاً)
    dep_rate = {x["key"]: x["rate"] for x in turnover["by_department"]}
    avg_rate = turnover12 or 0
    risk_emps = []
    for e in act:
        ind, possible = [], 0
        if _d(e.get("absence_days")) is not None:
            possible += 1
            if _d(e["absence_days"]) >= R["absence_high_days"]:
                ind.append(f"غياب مرتفع ({_num(_d(e['absence_days']), 0)} يوم)")
        if e["_perf"] is not None:
            possible += 1
            if e["_perf"] < R["low_perf"]:
                ind.append(f"أداء منخفض ({_num(e['_perf'], 0)})")
        if e["_hire"]:
            possible += 1
            lp = _date(e.get("last_promotion_date")) or e["_hire"]
            yrs = (as_of - lp).days / 365.25
            if yrs >= R["promotion_years"]:
                ind.append(f"{yrs:.1f} سنة منذ آخر ترقية/تعيين")
        if dep_rate.get(e["_dept"]) is not None and avg_rate:
            possible += 1
            if dep_rate[e["_dept"]] >= float(Decimal(str(avg_rate)) * R["turnover_high_factor"]):
                ind.append(f"دوران مرتفع في القسم ({dep_rate[e['_dept']]}%)")
        if len(ind) >= R["flight_min_indicators"]:
            risk_emps.append({"code": e.get("employee_code"), "name": e.get("name"), "role": e.get("role"), "department": e["_dept"],
                              "branch": e["_branch"], "indicators": ind, "indicators_count": len(ind), "possible": possible,
                              "confidence": "medium" if possible >= 4 and len(ind) >= 3 else "low",
                              "high_performer": bool(e["_perf"] is not None and e["_perf"] >= R["high_perf"])})
    risk_emps.sort(key=lambda x: -x["indicators_count"])
    flight = {"employees": risk_emps[:100], "count": len(risk_emps),
              "method_ar": f"موظف لديه {R['flight_min_indicators']} مؤشرات أو أكثر: غياب مرتفع، أداء منخفض، مدة طويلة بلا ترقية، دوران مرتفع في قسمه",
              "disclaimer_ar": "مؤشرات مبنية على البيانات المتاحة وليست تنبؤاً بأن الموظف سيغادر"}

    # ── الإنتاجية
    rev12 = None
    by_branch_rev = {}
    if sales_rows:
        rev12 = D0
        for r in sales_rows:
            d = _date(r.get("date"))
            if d and y_start <= d <= as_of:
                v = _d(r.get("net_sales")) if r.get("net_sales") is not None else _d(r.get("gross_sales"))
                if v is not None:
                    rev12 += v
                    if r.get("branch_name"):
                        by_branch_rev[r["branch_name"]] = by_branch_rev.get(r["branch_name"], D0) + v
    productivity = {"revenue_12m": _money(rev12), "avg_headcount": _num(avg_hc, 1),
                    "revenue_per_employee": _money(safe_divide(rev12, avg_hc)) if rev12 is not None and avg_hc else None,
                    "method_ar": "صافي المبيعات لآخر 12 شهراً ÷ متوسط عدد الموظفين",
                    "profit_per_employee": None, "profit_note_ar": "الربح لكل موظف غير متاح — يحتاج الربح من الوحدة المالية (2.11)",
                    "cost_to_revenue_pct": _pct(comp["annualized"]["value"] and Decimal(str(comp["annualized"]["value"])), rev12)
                    if comp.get("available") and rev12 else None, "by_branch": []}
    for b in sorted(by_branch_rev):
        hs = [len([e for e in active_at(min(_month_end(ym), as_of)) if e["_branch"] == b]) for ym in months12]
        avg_b = Decimal(sum(hs)) / len(hs) if hs else D0
        productivity["by_branch"].append({"key": b, "revenue_12m": _money(by_branch_rev[b]), "avg_headcount": _num(avg_b, 1),
                                          "revenue_per_employee": _money(safe_divide(by_branch_rev[b], avg_b)) if avg_b else None})
    productivity["by_branch"].sort(key=lambda x: -((x["revenue_per_employee"] or {}).get("value") or 0))

    # ── التوظيف
    recruitment = {"available": bool(openings)}
    if openings:
        ops = [dict(o, _open=_date(o.get("opened_date")), _fill=_date(o.get("filled_date"))) for o in openings]
        is_open = lambda o: (o["_fill"] is None or o["_fill"] > as_of) and not any(w in _norm(o.get("status")) for w in ("مغلق", "ملغ", "closed", "cancel", "filled", "مشغول"))
        open_ = [o for o in ops if o["_open"] and o["_open"] <= as_of and is_open(o)]
        filled = [o for o in ops if o["_open"] and o["_fill"] and o["_fill"] <= as_of]
        tth = [(o["_fill"] - o["_open"]).days for o in filled if o["_fill"] >= o["_open"]]
        cost_ = [(_d(o.get("hiring_cost")), _d(o.get("hires")) or Decimal(1)) for o in filled if _d(o.get("hiring_cost")) is not None]
        offers = sum((_d(o.get("offers")) or D0 for o in ops), D0)
        hires = sum((_d(o.get("hires")) or D0 for o in ops), D0)
        recruitment.update({"open": len(open_), "filled_12m": sum(1 for o in filled if o["_fill"] >= y_start),
                            "time_to_hire_days": round(sum(tth) / len(tth), 1) if tth else None,
                            "cost_per_hire": _money(safe_divide(sum((c for c, _ in cost_), D0), sum((h for _, h in cost_), D0))) if cost_ else None,
                            "offer_acceptance_pct": _pct(hires, offers) if offers else None,
                            "applicants": _num(sum((_d(o.get("applicants")) or D0 for o in ops), D0), 0) if any(o.get("applicants") not in (None, "") for o in ops) else None,
                            "open_by_department": [{"key": k, "count": len(v)} for k, v in sorted(group(open_, lambda o: o.get("department")).items(), key=lambda x: -len(x[1]))],
                            "open_by_branch": [{"key": k, "count": len(v)} for k, v in sorted(group(open_, lambda o: o.get("branch_name")).items(), key=lambda x: -len(x[1]))],
                            "open_list": [{"title": o.get("title"), "department": o.get("department"), "branch": o.get("branch_name"),
                                           "opened": o["_open"].isoformat(), "days_open": (as_of - o["_open"]).days} for o in sorted(open_, key=lambda o: o["_open"])][:50]})

    # ── حسب الفرع والقسم
    def unit(filt):
        es = [e for e in act if filt(e)]
        c = [e["_cost"] for e in es if e["_cost"] is not None]
        p = [e["_perf"] for e in es if e["_perf"] is not None]
        return {"employees": len(es), "turnover": turnover_of(filt)["rate"], "payroll": _money(sum(c, D0)) if c else None,
                "performance": _num(sum(p, D0) / len(p), 1) if p else None,
                "flight_risk": sum(1 for x in risk_emps if filt({"_branch": x["branch"], "_dept": x["department"]}))}
    units = {"branches": [{"key": b, **unit(lambda e, b=b: e["_branch"] == b)} for b in sorted({e["_branch"] for e in emps if e["_branch"]})],
             "departments": [{"key": d, **unit(lambda e, d=d: e["_dept"] == d)} for d in sorted({e["_dept"] for e in emps})]}
    for b in units["branches"]:
        pb = next((x for x in productivity["by_branch"] if x["key"] == b["key"]), None)
        b["revenue_per_employee"] = pb["revenue_per_employee"] if pb else None

    kpis = {"headcount": {"current": len(act), "previous": hc_prev, "change": len(act) - hc_prev},
            "hires": {"current": len(joiners), "period": cur}, "turnover": {"current": turnover12, "basis": "12 شهراً"},
            "cost": {"current": comp.get("monthly_total"), "change_pct": comp.get("change_pct")},
            "performance": {"current": perf.get("average")},
            "productivity": {"current": productivity["revenue_per_employee"]}}
    signals = _signals(R, cur, currency, turnover, turnover12, comp, succession, learning, recruitment, flight, productivity, perf, workforce)
    sev = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda x: ({"risk": 0, "opportunity": 1, "data_quality": 2}[x["type"]], sev.get(x["severity"], 3)))
    risks = [x for x in signals if x["type"] == "risk"]
    health = {"status": "critical" if any(x["severity"] == "high" for x in risks) else ("attention" if risks else "stable"),
              "risks": len(risks), "opportunities": sum(1 for x in signals if x["type"] == "opportunity"),
              "attention": sum(1 for x in signals if x["type"] == "data_quality"),
              "rule_ar": "حرجة: إشارة خطر عالية · تحتاج انتباه: أي إشارة خطر · مستقرة: لا مخاطر"}
    pillars = {"people": {"headcount": len(act), "turnover": turnover12},
               "performance": {"average": perf.get("average"), "high_share": next((x["pct"] for x in perf.get("distribution", []) if x["band"] == "high"), None)},
               "cost": {"monthly": comp.get("monthly_total"), "avg": comp.get("avg_per_employee")},
               "productivity": productivity["revenue_per_employee"],
               "talent_risk": {"flight_risk": flight["count"], "without_successor": succession.get("without_successor")}}
    return {"has_data": True, "version": f"hr-v{HR_VERSION}", "period": cur, "as_of": as_of.isoformat(), "currency": currency,
            "rules": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in R.items()},
            "health": health, "pillars": pillars, "kpis": kpis, "workforce": workforce, "structure": structure,
            "recruitment": recruitment, "turnover": turnover, "stability": stability, "performance": perf,
            "compensation": comp, "learning": learning, "succession": succession, "flight_risk": flight,
            "productivity": productivity, "units": units, "signals": signals,
            "data_quality": {"employees": len(emps), "unknown_hire_date": unknown_hire,
                             "no_branch_pct": _pct(Decimal(sum(1 for e in act if not e["_branch"])), Decimal(len(act))) if act else None,
                             "no_cost_pct": _pct(Decimal(len(act) - len(costed)), Decimal(len(act))) if act else None,
                             "no_rating_pct": _pct(Decimal(len(act) - len(rated)), Decimal(len(act))) if act else None}}


def _signals(R, period, currency, turnover, t12, comp, succ, learn, rec, flight, prod, perf, wf):
    out = []

    def add(kind, code, ar, sev, dim, evidence, action, impact=None, metric="headcount", confidence=None):
        out.append({"id": _sid(code, dim, period), "type": kind, "code": code, "source_module": "hr", "name_ar": ar,
                    "severity": sev, "dimension": dim, "period": period, "evidence": evidence, "confidence": confidence,
                    "suggested_action_ar": action, "estimated_impact": impact, "metric_id": metric,
                    "method": "rule-based (hr-v1.0)"})
    if t12:
        for scope, lab in (("by_branch", "الفرع"), ("by_department", "القسم")):
            for x in turnover[scope]:
                if x["rate"] is not None and x["leavers"] >= R["min_leavers"] and x["rate"] >= float(Decimal(str(t12)) * R["turnover_high_factor"]):
                    add("risk", "turnover_high", f"دوران مرتفع في {lab}", "high" if x["rate"] >= 2 * t12 else "medium", x["key"],
                        [f"الدوران {x['rate']}% مقابل {t12}% للشركة ({x['leavers']} مغادرين خلال 12 شهراً)"],
                        f"راجع أسباب المغادرة في {x['key']} (مقابلات خروج، رواتب، إدارة مباشرة)", metric="turnover_rate")
    if comp.get("available") and comp.get("change_pct") is not None and comp["change_pct"] >= R["cost_change_pct"]:
        add("risk", "cost_increase", "ارتفاع تكلفة الموظفين", "medium", None,
            [f"الرواتب الشهرية {comp['monthly_total']['value']:,.0f} ({comp['change_pct']:+}% عن الشهر السابق)", comp["change_note_ar"]],
            "راجع التعيينات الجديدة مقابل الحاجة الفعلية", metric="payroll")
    if succ.get("available") and succ["without_successor"]:
        add("risk", "no_successor", "وظائف حرجة بلا بديل", "medium", None,
            [f"{succ['without_successor']} من {succ['critical_roles']} وظيفة حرجة بلا مرشح للخلافة"]
            + [f"{r['role']} — {r['employee']}" for r in succ["roles"] if r["successors"] == 0][:3],
            "حدد مرشحاً لكل وظيفة حرجة وخطة تطوير له", metric="succession")
    hp = [x for x in flight["employees"] if x["high_performer"]]
    if hp:
        add("risk", "flight_risk_high_performers", "مؤشرات فقدان لموظفين عاليي الأداء", "high", None,
            [f"{len(hp)} موظفاً عالي الأداء لديهم مؤشرات مغادرة"] + [f"{x['name']}: {'، '.join(x['indicators'])}" for x in hp[:3]],
            "اجتماع فردي مع المدير المباشر ومراجعة المسار الوظيفي", metric="flight_risk", confidence="low")
    elif flight["count"]:
        add("risk", "flight_risk", "مؤشرات مخاطر فقدان موظفين", "low", None,
            [f"{flight['count']} موظفاً لديهم {R['flight_min_indicators']} مؤشرات أو أكثر", flight["disclaimer_ar"]],
            "راجع القائمة مع مديري الأقسام", metric="flight_risk", confidence="low")
    if rec.get("available") and rec["open"]:
        top = rec["open_by_department"][0] if rec["open_by_department"] else None
        add("risk" if rec["open"] >= 5 else "data_quality", "open_positions", "وظائف شاغرة تحتاج تعيين", "medium" if rec["open"] >= 5 else "low",
            top["key"] if top else None,
            [f"{rec['open']} وظيفة مفتوحة" + (f" — أكثرها في {top['key']} ({top['count']})" if top else "")]
            + ([f"متوسط مدة التوظيف {rec['time_to_hire_days']} يوم"] if rec["time_to_hire_days"] else []),
            "رتّب أولويات التعيين حسب أثرها على الفروع", metric="open_positions")
    if learn.get("available"):
        low = [d for d in learn["by_department"] if d["avg"] is not None and d["avg"] < R["low_training_hours"]]
        if low:
            add("risk", "low_training", "ساعات تدريب منخفضة", "low", low[0]["key"],
                [f"{d['key']}: {d['avg']} ساعة/موظف" for d in low[:3]], "خطة تدريب للأقسام الأقل", metric="training_hours")
    if prod["by_branch"] and len(prod["by_branch"]) >= 2 and prod["by_branch"][0]["revenue_per_employee"]:
        b = prod["by_branch"][0]
        add("opportunity", "top_productivity", "أعلى إنتاجية للموظف", "low", b["key"],
            [f"الإيراد لكل موظف {b['revenue_per_employee']['value']:,.0f} {currency} (12 شهراً)"],
            "انقل الممارسات الناجحة في هذا الفرع للفروع الأخرى", metric="revenue_per_employee")
    if perf.get("available"):
        top = perf["by_department"][0] if perf["by_department"] else None
        if top and top["avg"] >= R["high_perf"]:
            add("opportunity", "strong_team", "فريق عالي الأداء", "low", top["key"], [f"متوسط الأداء {top['avg']}"],
                "استثمر في الاحتفاظ بهذا الفريق وتوسيع دوره", metric="performance")
    else:
        add("data_quality", "no_performance", "تقييمات الأداء غير متاحة", "low", None, ["لا يوجد عمود «تقييم الأداء»"],
            "أضف تقييم الأداء (1–5 أو 0–100) في ملف الموظفين")
    if wf["leavers_no_date"]:
        add("data_quality", "leavers_no_date", "مغادرون بلا تاريخ", "low", None, [turnover["note_ar"]], "أضف «تاريخ انتهاء الخدمة» للمغادرين")
    if not rec.get("available"):
        add("data_quality", "no_recruitment", "بيانات التوظيف غير متاحة", "low", None, ["لا يوجد ملف وظائف شاغرة"],
            "ارفع ملف الوظائف الشاغرة (المسمى، القسم، تاريخ الفتح، تاريخ الشغل، المتقدمون، العروض)")
    return out
