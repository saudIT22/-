"""Phase 3.10 — Board Presentation Intelligence — عرض مجلس الإدارة (Board Decision Pack).

يجمع ما بنته المراحل السابقة (الأداء → المخاطر → المسببات → التوقع → الأهداف → القرارات → المالية) في قصة لمجلس الإدارة.
لا قاعدة بيانات مالية جديدة ولا نسخة من أي محرك: كل رقم يُقرأ من الطبقة الموحدة ويحمل مصدره وفترته وطريقة حسابه وثقته.
قواعد: البيانات الناقصة ≠ صفر · لا ادعاء سببية · الذكاء الاصطناعي يشرح فقط · التقرير المُنشأ لا يُعدّل (نسخة جديدة).
"""
from datetime import date, datetime, timedelta

import risk_engine as RE
import prediction_engine as PE
import goals_engine as G

BOARD_VERSION = "1.0"
MODEL = f"board-v{BOARD_VERSION}"

# ═══════════════════════════════════════════════════════════
# 3.10.1 — Board Data Contract
# ═══════════════════════════════════════════════════════════
SECTIONS = [("overview", "نظرة المجلس"), ("summary", "الملخص التنفيذي"), ("lenses", "المحاور الاستراتيجية الخمسة"), ("financial", "الأداء المالي"),
            ("branches", "أفضل وأسوأ فرع"), ("exceptions", "الاستثناءات"), ("risk", "تقرير المخاطر"), ("forecast", "التوقعات"),
            ("goals", "الأهداف وOKR"), ("initiatives", "المبادرات الاستراتيجية"), ("decisions_required", "القرارات المطلوبة من المجلس"),
            ("decision_status", "حالة القرارات"), ("recommendations", "توصيات المجلس"), ("appendix", "الملحق المالي"), ("preread", "القراءة المسبقة"),
            ("comparison", "مقارنة بالاجتماع السابق"), ("sector", "مؤشرات القطاع"), ("data_quality", "إفصاح جودة البيانات")]
SECTION_AR = dict(SECTIONS)
PACK_PAGES = [("cover", "الغلاف"), ("summary", "الملخص التنفيذي"), ("lenses", "المحاور الخمسة"), ("financial", "الأداء المالي"), ("growth", "النمو"),
              ("risk", "المخاطر"), ("branches", "أفضل/أسوأ فرع"), ("forecast", "التوقعات"), ("goals", "الأهداف وOKR"), ("initiatives", "المبادرات الاستراتيجية"),
              ("decisions_required", "القرارات المطلوبة"), ("appendix", "الملحق: قائمة الدخل · الميزانية · التدفقات"), ("data_quality", "إفصاح جودة البيانات")]
LINKS = {"financial": "company-financial-intelligence.html", "sales": "company-sales-intelligence.html", "cashflow": "company-cashflow-intelligence.html",
         "risk": "company-risk-intelligence.html", "drivers": "company-risk-drivers.html", "prediction": "company-performance-prediction.html",
         "goals": "company-goals-intelligence.html", "decisions": "company-decisions-intelligence.html", "leakage": "company-leakage-intelligence.html",
         "purchases": "company-purchases-intelligence.html", "benchmark": "company-sector-benchmark.html", "operations": "company-ops-intelligence.html",
         "inventory": "company-inventory-intelligence.html", "hr": "company-hr-intelligence.html"}
SRC_AR = {"financial": "الوحدة المالية (2.10)", "sales": "ذكاء المبيعات (2.5)", "cashflow": "التدفق النقدي (2.8)", "risk": "مركز المخاطر (3.3)",
          "drivers": "مسببات المخاطر (3.4)", "prediction": "التنبؤ بالأداء (3.7)", "goals": "الأهداف والنتائج (3.8)", "decisions": "متابعة القرارات (3.9)",
          "leakage": "استرداد الأموال (3.1)", "purchases": "المشتريات (2.7)", "benchmark": "المقارنة بالقطاع (3.5)", "operations": "العمليات (2.9)",
          "inventory": "المخزون (2.6)", "hr": "الموارد البشرية"}


def _n(v):
    return G._num(v)


def metric(key, label, value, source, *, unit="SAR", period=None, calc=None, confidence=None, updated=None, link=None, reason=None):
    """3.10.20 — كل رقم في التقرير قابل للتتبع: المصدر، الفترة، الحساب، الثقة، آخر تحديث، رابط المصدر."""
    v = _n(value)
    return {"key": key, "label": label, "value": None if v is None else round(v, 4), "unit": unit, "source": source, "source_ar": SRC_AR.get(source, source),
            "link": link or LINKS.get(source), "period": period, "calculation_ar": calc, "confidence": confidence, "last_update": updated,
            "available": v is not None, "reason_ar": None if v is not None else (reason or "غير متاح في البيانات — لا يُعرض صفراً")}


def fmt(v, unit="SAR"):
    if v is None:
        return "غير متاح"
    if unit == "%":
        return f"{v:,.1f}%"
    a = abs(v)
    return f"{v / 1e6:,.2f}M" if a >= 1e6 else (f"{v / 1e3:,.0f}K" if a >= 1e4 else f"{v:,.0f}")


# ═══════════════════════════════════════════════════════════
# 3.10.2 — Five Strategic Lenses (من درجات مركز المخاطر — لا درجات بلا مصدر)
# ═══════════════════════════════════════════════════════════
LENSES = [("profitability", "الربحية", "💰", "profit", "financial"), ("liquidity", "السيولة", "💧", "liquidity", "cashflow"),
          ("growth", "النمو", "📈", None, "sales"), ("customer", "العملاء", "🤝", "customer", "sales"), ("risk", "المخاطر", "🛡️", "__index__", "risk")]
LENS_RULE_AR = ("الربحية = 100 − درجة مخاطر انخفاض الربح · السيولة = 100 − درجة مخاطر السيولة · العملاء = 100 − درجة مخاطر فقدان العملاء · "
                "المخاطر = 100 − مؤشر المخاطر العام (3.3) · النمو = 50 + 2.5 × نمو الإيراد (آخر 3 أشهر مقابل الـ3 قبلها) محدود بين 0 و100. "
                "🟢 ≥ 70 · 🟠 60–69 · 🔴 < 60")


def lens_status(score):
    if score is None:
        return "na", "⚪", "غير قابل للتحديد"
    return ("green", "🟢", "جيد") if score >= 70 else (("orange", "🟠", "يحتاج انتباه") if score >= 60 else ("red", "🔴", "حرج"))


def growth_recent(sm):
    pts = PE.series_of(sm["company"], "revenue", exclude=sm.get("partial_month")) if sm and sm.get("company") else []
    if len(pts) < 6:
        return None, pts
    a, b = sum(v for _, v in pts[-3:]), sum(v for _, v in pts[-6:-3])
    return (round((a / b - 1) * 100, 2) if b else None), pts


def lenses(risk, sm, prev=None, pred=None):
    cats = {c["key"]: c for c in (risk or {}).get("categories") or []}
    ix = (risk or {}).get("index") or {}
    g, pts = growth_recent(sm)
    out = []
    pl = {x["key"]: x for x in (prev or {}).get("lenses") or []}
    for key, ar, icon, cat, src in LENSES:
        score, evidence, why = None, [], None
        if cat == "__index__":
            if ix.get("score") is not None:
                score = round(100 - ix["score"], 1)
                evidence = [f"مؤشر المخاطر العام {ix['score']:.1f}/100 ({ix.get('level_ar') or ''})"]
            else:
                why = "مؤشر المخاطر غير محسوب"
        elif cat:
            c = cats.get(cat)
            if c and c.get("score") is not None:
                score = round(100 - c["score"], 1)
                td = c.get("top_driver") or {}
                evidence = [f"درجة {c['ar']} {c['score']:.1f}/100", f"أكبر مسبب: {td.get('name_ar')} ({td.get('score')})" if td else ""]
            else:
                why = (c or {}).get("unable_reasons") and "؛ ".join(c["unable_reasons"][:2]) or "بيانات الفئة غير كافية"
        else:
            if g is not None:
                score = round(max(0.0, min(100.0, 50 + 2.5 * g)), 1)
                evidence = [f"الإيراد آخر 3 أشهر مقابل الـ3 قبلها: {g:+.1f}%"]
                if pred and pred.get("status") == "ok" and pred["outlook"].get("growth_pct") is not None:
                    evidence.append(f"توقع 6 أشهر مقابل آخر 6: {pred['outlook']['growth_pct']:+.1f}% (3.7)")
            else:
                why = "النمو يحتاج 6 أشهر مكتملة من المبيعات"
        stt, light, st_ar = lens_status(score)
        p = (pl.get(key) or {}).get("score")
        trend = None if (score is None or p is None) else ("up" if score - p >= 3 else "down" if p - score >= 3 else "flat")
        out.append({"key": key, "ar": ar, "icon": icon, "score": score, "status": stt, "light": light, "status_ar": st_ar,
                    "trend": trend, "arrow": {"up": "↑", "down": "↓", "flat": "→", None: "—"}[trend], "previous": p,
                    "evidence": [e for e in evidence if e], "reason_ar": why, "source": src, "source_ar": SRC_AR[src], "link": LINKS[src]})
    reds = sum(1 for x in out if x["status"] == "red")
    ambers = sum(1 for x in out if x["status"] == "orange")
    status = ("red", "🔴", "يتطلب تدخّلاً عاجلاً") if reds >= 2 else (("amber", "🟡", "يتطلب انتباهاً") if (reds or ambers) else ("green", "🟢", "على المسار"))
    return out, {"key": status[0], "light": status[1], "ar": status[2], "rule_ar": "🔴 محوران حرجان أو أكثر · 🟡 أي محور حرج أو يحتاج انتباه · 🟢 غير ذلك"}


# ═══════════════════════════════════════════════════════════
# 3.10.7 — Financial Board Summary (من الوحدة المالية — لا نسخة P&L جديدة)
# ═══════════════════════════════════════════════════════════
def fiscal_ytd(trends, fy_start, last=None):
    ps = sorted(trends)
    if not ps:
        return []
    last = last or ps[-1]
    y, m = int(last[:4]), int(last[5:7])
    sy = y if m >= fy_start else y - 1
    start = f"{sy:04d}-{fy_start:02d}"
    return [p for p in ps if start <= p <= last]


def financial(fin, trends, budget, fy_start=1, currency="SAR"):
    fin = fin or {}
    budget = budget or {}
    ytd = fiscal_ytd(trends, fy_start)
    per = f"{ytd[0]} → {ytd[-1]}" if ytd else None
    s = lambda k: (sum(_n(trends[p].get(k)) or 0 for p in ytd) if ytd and all(_n(trends[p].get(k)) is not None for p in ytd) else None)
    months = len(ytd)
    b_rev = _n(budget.get("revenue_monthly"))
    b_opex = sum(_n(v) or 0 for v in (budget.get("opex_monthly") or {}).values()) or None
    rows = []
    for key, ar, inv, act, bud in (("revenue", "الإيراد", False, s("revenue"), b_rev * months if (b_rev and months) else None),
                                   ("gross_profit", "مجمل الربح", False, s("gross_profit"), None),
                                   ("opex", "المصروفات التشغيلية", True, s("opex"), b_opex * months if (b_opex and months) else None),
                                   ("net_profit", "صافي الربح", False, s("net_profit"), None)):
        var = None if (act is None or bud is None) else round(act - bud, 2)
        rows.append({"key": key, "ar": ar, "actual": None if act is None else round(act, 2), "budget": None if bud is None else round(bud, 2), "variance": var,
                     "variance_pct": None if (var is None or not bud) else round(var / bud * 100, 1), "inverse": inv,
                     "bad": None if var is None else ((var > 0) if inv else (var < 0)),
                     "budget_reason_ar": None if bud is not None else ("لا توجد موازنة لهذا البند في الوحدة المالية" if key in ("revenue", "opex") else "الموازنة تُحدد للإيراد والمصروفات فقط — لا موازنة لهذا البند"),
                     "evidence": metric(key, ar, act, "financial", period=per, calc=f"مجموع {ar} الشهري من القوائم ({months} شهر)", confidence="high" if act is not None else None)})
    var = fin.get("variance") or {}
    why = [{"ar": c["label"], "effect": c["effect"]} for c in sorted(var.get("contributions") or [], key=lambda c: c["effect"])[:4]]
    # أين ذهب المال؟ (أساس قائمة الدخل للفترة الحالية: تكلفة المبيعات + بنود المصروفات)
    summ = (fin.get("summary") or {}).get("profitability") or {}
    lines = []
    if _n(summ.get("cogs")) is not None:
        prev_cogs = next((r.get("previous") for r in fin.get("statement") or [] if r.get("key") == "cogs"), None)
        lines.append({"key": "cogs", "ar": "المشتريات / تكلفة المبيعات", "amount": _n(summ["cogs"]), "previous": _n(prev_cogs),
                      "change_pct": None if not _n(prev_cogs) else round((_n(summ["cogs"]) / _n(prev_cogs) - 1) * 100, 1), "link": LINKS["purchases"]})
    for l in (fin.get("expenses") or {}).get("lines") or []:
        lines.append({"key": l["key"], "ar": l["label"], "amount": _n(l.get("current")), "previous": _n(l.get("previous")), "change_pct": l.get("change_pct"),
                      "link": LINKS["financial"]})
    tot = sum(l["amount"] or 0 for l in lines)
    for l in lines:
        l["share_pct"] = round((l["amount"] or 0) / tot * 100, 1) if tot else None
    inc = [l for l in lines if l.get("change_pct") is not None and l["change_pct"] > 0 and (l.get("previous") or 0) > 0]
    biggest = max(inc, key=lambda l: (l["amount"] - l["previous"])) if inc else None
    return {"period_ytd": per, "fiscal_months": months, "rows": rows, "budget_available": bool(b_rev or b_opex),
            "variance_why": why, "variance_main_ar": var.get("main_cause_ar"), "variance_period": fin.get("period"),
            "margin_change_pp": var.get("margin_change_pp"),
            "money": {"period": fin.get("period"), "lines": sorted(lines, key=lambda l: -(l["amount"] or 0)), "total": round(tot, 2) if lines else None,
                      "largest_increase": None if not biggest else {"ar": biggest["ar"], "change_pct": biggest["change_pct"], "delta": round(biggest["amount"] - biggest["previous"], 2), "link": biggest["link"]},
                      "basis_ar": "أساس قائمة الدخل للشهر الحالي: تكلفة المبيعات + بنود المصروفات التشغيلية (وليس التدفق النقدي)"},
            "note_ar": "الأرقام من الوحدة المالية مباشرة — الضغط على أي بند يفتح مصدره"}


def appendix(fin, cf, trends, fy_start=1):
    """3.10.13 — الملحق: قائمة الدخل (الشهر + منذ بداية السنة) · الميزانية العمومية · التدفقات حسب النشاط."""
    fin = fin or {}
    ytd = fiscal_ytd(trends, fy_start)
    s = lambda k: (round(sum(_n(trends[p].get(k)) or 0 for p in ytd), 2) if ytd and all(_n(trends[p].get(k)) is not None for p in ytd) else None)
    summ = fin.get("summary") or {}
    prof, rev = summ.get("profitability") or {}, summ.get("revenue") or {}
    pl = [{"key": k, "ar": a, "month": _n(v), "ytd": y} for k, a, v, y in (
        ("revenue", "الإيراد", rev.get("net_sales"), s("revenue")), ("cogs", "تكلفة المبيعات", prof.get("cogs"), (round(s("revenue") - s("gross_profit"), 2) if (s("revenue") is not None and s("gross_profit") is not None) else None)),
        ("gross_profit", "مجمل الربح", prof.get("gross_profit"), s("gross_profit")), ("opex", "المصروفات التشغيلية", prof.get("opex"), s("opex")),
        ("ebitda", "EBITDA", prof.get("ebitda"), s("ebitda")), ("net_profit", "صافي الربح", prof.get("net_profit"), s("net_profit")))]
    bs = fin.get("balance_sheet") or {}
    def items(lst):
        return [{"key": i.get("key"), "ar": i.get("label"), "value": _n(i.get("value")), "source": i.get("source"),
                 "reason_ar": None if _n(i.get("value")) is not None else "غير متاح — يحتاج إدخالاً"} for i in lst or []]
    tot = lambda xs: (round(sum(x["value"] for x in xs if x["value"] is not None), 2) if xs and all(x["value"] is not None for x in xs) else None)
    A, L, E = items(bs.get("assets")), items(bs.get("liabilities")), items(bs.get("equity"))
    # التدفقات حسب النشاط من تصنيف محركات التدفق النقدي (2.8)
    drv = {d["key"]: (d.get("amount") or {}).get("value") for d in (cf or {}).get("drivers") or []}
    has = bool(drv)
    ins = lambda *k: sum(drv.get(x) or 0 for x in k)
    op = (ins("collections", "other_in") - ins("suppliers", "payroll", "rent", "expenses", "tax", "other_out")) if has else None
    fi = (ins("financing") - ins("financing_out")) if has else None
    cfs = [{"key": "operating", "ar": "الأنشطة التشغيلية", "value": None if op is None else round(op, 2), "basis_ar": "التحصيلات وتدفقات داخلة أخرى − الموردون والرواتب والإيجار والمصروفات والضرائب"},
           {"key": "investing", "ar": "الأنشطة الاستثمارية", "value": None, "reason_ar": "لا يوجد تصنيف استثماري في حركات البنك — غير متاح (لا يُعرض صفراً)"},
           {"key": "financing", "ar": "الأنشطة التمويلية", "value": None if fi is None else round(fi, 2), "basis_ar": "التمويل والقروض − سداد التمويل"}]
    return {"period": fin.get("period"), "ytd": f"{ytd[0]} → {ytd[-1]}" if ytd else None, "pl": pl,
            "balance_sheet": {"assets": A, "liabilities": L, "equity": E, "total_assets": tot(A), "total_liabilities": tot(L), "total_equity": tot(E),
                              "missing": (fin.get("quality") or {}).get("balance_sheet_missing") or [],
                              "note_ar": "الميزانية من الوحدة المالية — البنود غير المُدخلة تظهر غير متاحة"},
            "cash_flow": {"period": (cf or {}).get("period"), "rows": cfs, "classified_from_ar": "تصنيف حركات البنك في وحدة التدفق النقدي (2.8)"},
            "quality": fin.get("quality")}


# ═══════════════════════════════════════════════════════════
# 3.10.4 — Best / Worst Branch (الأسباب من الأدلة الموجودة — لا تخمين)
# ═══════════════════════════════════════════════════════════
def _rank(vals, higher=True):
    xs = sorted({v for v in vals.values() if v is not None}, reverse=higher)
    return {k: (xs.index(v) if v is not None else None) for k, v in vals.items()}


def branches(fin, pred, drivers, ops, goal_branches=None):
    fb = {b["key"]: b for b in (fin or {}).get("branches") or []}
    pb = {b["branch"]: b for b in (pred or {}).get("branches") or [] if b.get("status") == "ok"}
    db = {r["branch"]: r for r in ((drivers or {}).get("branches") or {}).get("rows") or []}
    ob = {b["key"]: b for b in (ops or {}).get("branches") or []}
    names = sorted(set(fb) | set(pb))
    if len(names) < 2:
        return {"status": "unavailable", "reason_ar": "المقارنة تحتاج فرعين على الأقل ببيانات"}
    dims = {"growth": ({k: (fb.get(k) or {}).get("revenue_growth_pct") for k in names}, True, "نمو الإيراد"),
            "margin": ({k: (fb.get(k) or {}).get("gross_margin") for k in names}, True, "هامش مجمل الربح"),
            "forecast": ({k: (pb.get(k) or {}).get("change_pct") for k in names}, True, "توقع 3 أشهر"),
            "efficiency": ({k: (ob.get(k) or {}).get("efficiency") for k in names}, True, "الكفاءة التشغيلية"),
            "risk": ({k: (sum(1 for x in (db.get(k) or {}).get("all") or [] if x.get("level") in ("high", "critical")) if k in db else None) for k in names}, False, "مسببات مرتفعة/حرجة")}
    ranks = {d: _rank(v, hi) for d, (v, hi, _a) in dims.items()}
    score = {}
    for k in names:
        rs = [ranks[d][k] for d in dims if ranks[d][k] is not None]
        score[k] = sum(rs) / len(rs) if rs else None
    ok = [k for k in names if score[k] is not None]
    if len(ok) < 2:
        return {"status": "unavailable", "reason_ar": "لا توجد أبعاد كافية لمقارنة الفروع"}
    best, worst = min(ok, key=lambda k: (score[k], k)), max(ok, key=lambda k: (score[k], k))

    def card(k, good):
        f, p, d, o = fb.get(k) or {}, pb.get(k) or {}, db.get(k) or {}, ob.get(k) or {}
        m = [metric("growth", "نمو الإيراد", f.get("revenue_growth_pct"), "financial", unit="%", period=(fin or {}).get("period")),
             metric("margin", "هامش مجمل الربح", f.get("gross_margin"), "financial", unit="%", period=(fin or {}).get("period")),
             metric("sales", "الإيراد", f.get("revenue"), "financial", period=(fin or {}).get("period")),
             metric("efficiency", "الكفاءة التشغيلية", o.get("efficiency"), "operations", unit="/100"),
             metric("forecast", "توقع 3 أشهر", p.get("change_pct"), "prediction", unit="%", confidence=p.get("confidence")),
             metric("risk_drivers", "مسببات مرتفعة/حرجة", dims["risk"][0].get(k), "drivers", unit="")]
        why = []
        for dname, (vals, hi, ar) in dims.items():
            v = vals.get(k)
            if v is None:
                continue
            others = [x for kk, x in vals.items() if kk != k and x is not None]
            if not others:
                continue
            avg = sum(others) / len(others)
            better = (v > avg) if hi else (v < avg)
            if better == good and v != avg:
                why.append(f"{ar}: {v:,.1f} مقابل متوسط بقية الفروع {avg:,.1f}")
        if not good:
            for x in (d.get("top") or [])[:3]:
                why.append(f"مسبب {RE.LEVEL_AR.get(x['level'])}: {x['name_ar']} ({x['value']} {x.get('unit') or ''}) — {x.get('category_ar')}")
        else:
            low = [x for x in d.get("all") or [] if x.get("level") == "low"][:2]
            why += [f"مسبب منخفض الخطورة: {x['name_ar']}" for x in low]
        return {"branch": k, "rank_score": round(score[k], 2), "metrics": m, "why": why[:6], "goals": (goal_branches or {}).get(k),
                "links": {"sales": LINKS["sales"], "drivers": LINKS["drivers"], "prediction": LINKS["prediction"]},
                "note_ar": "الأسباب من أدلة المحركات (المالية/العمليات/المسببات/التنبؤ) — علاقات وليست سببية مثبتة"}
    return {"status": "ok", "best": card(best, True), "worst": card(worst, False), "ranking": sorted(({"branch": k, "score": score[k]} for k in ok), key=lambda x: x["score"]),
            "rule_ar": "ترتيب كل فرع في كل بُعد (النمو، الهامش، التوقع، الكفاءة، عدد المسببات المرتفعة) ثم متوسط الترتيب — الأقل أفضل",
            "dimensions": {d: a for d, (_v, _h, a) in dims.items()}}


# ═══════════════════════════════════════════════════════════
# 3.10.6 — Risk Board Report (من 3.3 + 3.4 + 3.9)
# ═══════════════════════════════════════════════════════════
def risk_report(risk, drivers, dec_res):
    dd = {d["key"]: d for d in (drivers or {}).get("drivers") or []}
    decs = (dec_res or {}).get("decisions") or []
    out = []
    for c in sorted([c for c in (risk or {}).get("categories") or [] if c.get("score") is not None], key=lambda c: -c["score"]):
        imp = {i["type"]: i["amount"] for i in c.get("impacts") or [] if i.get("amount")}
        low = (imp.get("actual") or 0) + (imp.get("potential") or 0)
        high = low + (imp.get("exposure") or 0)
        td = c.get("top_driver") or {}
        d = dd.get(td.get("key")) or {}
        cand = (d.get("candidates") or [{}])[0]
        keys = {k for k in [td.get("key")] + [x["key"] for x in (risk or {}).get("drivers") or [] if x.get("category") == c["key"]] if k}
        dec = next((e for e in decs if e.get("risk_key") in keys or e.get("root_cause") in keys), None)
        out.append({"key": c["key"], "ar": c["ar"], "icon": c.get("icon"), "score": c["score"], "level": c.get("level"), "level_ar": c.get("level_ar"),
                    "impact_range": [round(low, 2), round(high, 2)] if (low or high) else None,
                    "impact_note_ar": "الحد الأدنى = الأثر الفعلي + المحتمل · الحد الأعلى = + التعرّض (أنواع منفصلة لا تُجمع المتداخلة)" if (low or high) else "لا أثر مالي مُقاس",
                    "driver": {"key": td.get("key"), "name_ar": td.get("name_ar"), "score": td.get("score"), "evidence": (d.get("evidence") or [])[:2]},
                    "root_cause": {"name_ar": cand.get("name_ar"), "confidence_ar": cand.get("confidence_ar"), "note_ar": "سبب مرشح من 3.4 — 3.6 غير مفعّلة"} if cand else None,
                    "mitigation": {"title": dec["title"], "status_ar": dec["status_ar"], "decision_id": dec["id"], "owner": dec.get("owner")} if dec else
                                  {"title": (d.get("recommendation") or {}).get("text_ar"), "status_ar": "لا قرار بعد", "decision_id": None},
                    "formula_ar": c.get("formula_ar"), "link": LINKS["risk"]})
    ix = (risk or {}).get("index") or {}
    return {"index": ix.get("score"), "level_ar": ix.get("level_ar"), "top": out[:5], "confidence_pct": ((risk or {}).get("confidence") or {}).get("pct"),
            "headline_ar": ((risk or {}).get("brief") or {}).get("headline")}


# ═══════════════════════════════════════════════════════════
# 3.10.8 / 3.10.9 / 3.10.10 — التوقع والأهداف والمبادرات (من 3.7 و3.8)
# ═══════════════════════════════════════════════════════════
def forecast(pred, see_profit=True):
    p = pred or {}
    if p.get("status") != "ok":
        return {"status": "unavailable", "reason_ar": p.get("message_ar") or "التوقع غير متاح — بيانات تاريخية غير كافية"}
    o, cash = p["outlook"], p.get("cash") or {}
    conf = o["confidence"]
    pts = (p.get("sales") or {}).get("points") or []
    prof = p.get("profit") or {}
    rows = [metric("revenue", "الإيراد المتوقع (6 أشهر)", o.get("forecast_revenue"), "prediction", period=f"{pts[0]['period']} → {pts[-1]['period']}" if pts else None,
                   calc="مجموع توقع الأشهر الستة (Theil–Sen مخمّد + موسمية عند توفرها)", confidence=conf["score"]),
            metric("growth", "النمو المتوقع", o.get("growth_pct"), "prediction", unit="%", calc="توقع 6 أشهر ÷ آخر 6 أشهر − 1", confidence=conf["score"])]
    if see_profit:
        last_conf = (prof.get("points") or [{}])[-1].get("confidence") if prof.get("status") == "ok" else None
        rows.append(metric("profit", "صافي الربح المتوقع (6 أشهر)", o.get("forecast_profit"), "prediction", calc="جسر الربح: الإيراد × الهامش − المصروفات", confidence=last_conf,
                           reason=prof.get("reason_ar")))
        cp = cash.get("points") or []
        rows.append(metric("cash", "الرصيد النقدي بعد 6 أشهر", cp[-1]["balance"] if cp else None, "cashflow", calc="الرصيد الحالي + صافي التدفق المتوقع", confidence=cp[-1].get("confidence") if cp else None,
                           reason=cash.get("reason_ar")))
    g = p.get("gap") or {}
    return {"status": "ok", "rows": rows, "confidence": conf, "basis_ar": o.get("basis_ar"),
            "target_gap": {k: g.get(k) for k in ("target", "projection", "gap", "gap_pct", "sentence_ar", "fiscal_year", "target_source_ar")} if g.get("status") == "ok" else None,
            "profit_gap": p.get("profit_gap") if see_profit else None, "risks": [{"ar": r["ar"], "level": r["level"], "horizon_ar": r.get("horizon_ar")} for r in (p.get("risks") or [])[:4]],
            "disclaimer_ar": "توقعات تقديرية بمنهجية معلنة ونطاق ثقة — ليست حقائق مؤكدة"}


def goals_section(gres):
    g = gres or {}
    goals = [x for x in g.get("goals") or [] if x.get("level") == "company"]
    rows = [{"id": x["id"], "name": x["name"], "metric_ar": x.get("metric_ar"), "target": x.get("target"), "actual": x.get("actual"), "unit": x.get("unit"),
             "progress_pct": x.get("progress_pct"), "forecast": x.get("forecast"), "status": x["status"], "status_ar": x["status_ar"], "icon": x.get("icon"),
             "probability_ar": x.get("probability_ar"), "owner": x.get("owner"), "link": f"{LINKS['goals']}#goal/{x['id']}"} for x in goals]
    okr = [{"id": o["id"], "objective": o.get("objective") or o["name"], "progress_pct": o.get("progress_pct"), "status_ar": o["status_ar"], "icon": o.get("icon"),
            "krs": [{"name": k.get("name") or k.get("metric_ar"), "progress_pct": k.get("progress_pct"), "status_ar": k.get("status_ar")} for k in o.get("key_results") or []]}
           for o in g.get("okrs") or []]
    return {"counts": (g.get("overview") or {}).get("counts"), "headline_ar": (g.get("overview") or {}).get("headline_ar"), "company_goals": rows, "okrs": okr,
            "available": bool(g.get("goals")), "reason_ar": None if g.get("goals") else "لا توجد أهداف معرّفة في مركز الأهداف (3.8)"}


def initiatives(gres):
    """المبادرات الاستراتيجية = الركائز الاستراتيجية في 3.8 بأهدافها الفعلية (التقدم = متوسط تقدم الأهداف المرتبطة)."""
    pills = ((gres or {}).get("strategy") or {}).get("pillars") or []
    goals = {x["id"]: x for x in (gres or {}).get("goals") or []}
    out = []
    for p in pills:
        gs = [goals[c["id"]] for c in p.get("chain") or [] if c.get("id") in goals]
        pr = [min(100.0, max(0.0, x["progress_pct"])) for x in gs if x.get("progress_pct") is not None]
        owners = [x.get("owner") for x in gs if x.get("owner")]
        st = p.get("health")
        out.append({"key": p["key"], "ar": p["ar"], "icon": p.get("icon"), "goals": p.get("goals"), "progress_pct": round(sum(pr) / len(pr), 1) if pr else None,
                    "owner": max(set(owners), key=owners.count) if owners else None, "status": st,
                    "light": {"behind": "🔴", "at_risk": "🟠", "on_track": "🟢", "completed": "✅"}.get(st, "⚪"), "warning_ar": p.get("warning_ar"),
                    "link": LINKS["goals"] + "#strategy"})
    return out


# ═══════════════════════════════════════════════════════════
# 3.10.11 / 3.10.12 — القرارات المطلوبة وحالة القرارات (من 3.9) + توصيات المجلس
# ═══════════════════════════════════════════════════════════
def decisions_required(dec_res, gres, risk, today, limit=5):
    items = []
    decs = (dec_res or {}).get("decisions") or []
    levels = {d["key"]: d.get("level") for d in (risk or {}).get("drivers") or []}
    for e in sorted([e for e in decs if e.get("workflow") in ("pending_approval", "under_review")], key=lambda e: -(e.get("expected_impact") or 0)):
        due = G._d(e.get("due"))
        lk = levels.get(e.get("risk_key")) or levels.get(e.get("root_cause"))
        hi = e.get("high_impact") or lk in ("high", "critical")
        miss = e.get("readiness_missing") or []
        items.append({"kind": "pending", "decision_id": e["id"], "title": e["title"], "expected_impact": e.get("expected_impact"), "owner": e.get("owner"),
                      "deadline_days": None if not due else (due - today).days, "due": e.get("due"), "risk_if_delayed": "high" if hi else "medium",
                      "risk_if_delayed_ar": "مرتفع" if hi else "متوسط", "recommendation": "approve" if not miss else "complete",
                      "recommendation_ar": "اعتماد" if not miss else "استكمال قبل الاعتماد: " + "، ".join(miss),
                      "evidence": [x for x in [e.get("rationale"), (e.get("primary") or {}).get("metric_ar") and f"المؤشر: {e['primary']['metric_ar']}", f"المصدر: {e.get('source_ar')}"] if x],
                      "link": f"{LINKS['decisions']}#dec/{e['id']}"})
    # قرارات مقترحة لم تُنشأ بعد: أهداف الشركة المتأخرة/المعرّضة (3.8)
    existing_goals = {e.get("goal_id") for e in decs if e.get("goal_id")}
    for g in sorted([x for x in (gres or {}).get("goals") or [] if x.get("level") == "company" and x.get("decision_options") and x["id"] not in existing_goals],
                    key=lambda x: (x["status"] != "behind", -(x.get("expected_gap") or 0))):
        o = g["decision_options"][0]
        items.append({"kind": "proposed", "decision_id": None, "goal_id": g["id"], "title": o["title"], "expected_impact": o.get("expected_financial_impact"),
                      "owner": g.get("owner"), "deadline_days": None, "risk_if_delayed": "high" if g["status"] == "behind" else "medium",
                      "risk_if_delayed_ar": "مرتفع" if g["status"] == "behind" else "متوسط", "recommendation": "create", "recommendation_ar": "إنشاء قرار ومتابعته",
                      "evidence": [f"الهدف «{g['name']}» {g['status_ar']}", o.get("expected_kpi_impact_ar"), o.get("basis_ar")], "link": f"{LINKS['goals']}#goal/{g['id']}"})
    return items[:limit]


def decision_status(dec_res):
    o = (dec_res or {}).get("overview") or {}
    if not o:
        return {"available": False, "reason_ar": "لا توجد قرارات في متابعة القرارات (3.9)"}
    late_hi = [e for e in (dec_res or {}).get("decisions") or [] if e.get("status") == "late" and e.get("high_impact")]
    return {"available": True, "approved": sum(1 for e in dec_res["decisions"] if e.get("workflow") == "approved"), "in_progress": o.get("in_progress"),
            "pending": o.get("pending_approval"), "late": o.get("overdue"), "completed": o.get("completed"), "measured": o.get("measured"),
            "expected_total": o.get("expected_total"), "actual_total": o.get("actual_total"), "achievement_pct": o.get("achievement_pct"),
            "late_high_impact": len(late_hi), "sentence_ar": (f"{len(late_hi)} قرارات عالية الأثر متأخرة" if late_hi else "لا قرارات عالية الأثر متأخرة"),
            "late_items": [{"id": e["id"], "title": e["title"], "days": (e.get("delay") or {}).get("days"), "impact": e.get("expected_impact")} for e in late_hi][:5],
            "link": LINKS["decisions"]}


# ═══════════════════════════════════════════════════════════
# 3.10.5 — Board Exceptions (الاستثناءات فقط)
# ═══════════════════════════════════════════════════════════
def exceptions(risk, pred, gres, dec_res, fin, brs, see_fin=True):
    ex = []

    def add(sev, title, detail, src, link=None, amount=None):
        ex.append({"severity": sev, "icon": {"red": "🔴", "orange": "🟠", "yellow": "🟡"}[sev], "title_ar": title, "detail_ar": detail, "source": src,
                   "source_ar": SRC_AR.get(src, src), "link": link or LINKS.get(src), "amount": amount})
    p = pred or {}
    if see_fin and (p.get("profit_gap") or {}).get("gap", 0) > 0:
        pg = p["profit_gap"]
        add("red", "توقع الربح دون الهدف", f"المعدل السنوي المتوقع {fmt(pg['run_rate_annual'])} مقابل هدف {fmt(pg['target'])}", "prediction", amount=pg["gap"])
    gp = p.get("gap") or {}
    if gp.get("status") == "ok" and (gp.get("gap") or 0) > 0:
        add("red" if (gp.get("gap_pct") or 0) >= 10 else "orange", "توقع الإيراد دون الهدف السنوي", gp.get("sentence_ar") or "", "prediction", amount=gp.get("gap"))
    if see_fin:
        for b in (fin or {}).get("branches") or []:
            if (b.get("contribution") or 0) < 0:
                add("red", f"فرع {b['key']} بمساهمة سالبة", f"المساهمة {fmt(b['contribution'])} في {(fin or {}).get('period')}", "financial", amount=b["contribution"])
    for c in (risk or {}).get("categories") or []:
        if c.get("level") in ("critical", "high") and (see_fin or c["key"] not in ("profit", "liquidity")):
            td = c.get("top_driver") or {}
            add("red" if c["level"] == "critical" else "orange", f"{c['ar']} {c.get('level_ar')}", f"الدرجة {c['score']:.0f}/100 · أكبر مسبب: {td.get('name_ar')}", "risk")
    for g in (gres or {}).get("goals") or []:
        if g.get("level") == "company" and g["status"] in ("behind", "at_risk"):
            add("orange" if g["status"] == "behind" else "yellow", f"هدف {g['status_ar']}: {g['name']}", "؛ ".join(g.get("why") or [])[:180], "goals",
                link=f"{LINKS['goals']}#goal/{g['id']}", amount=g.get("expected_gap") if g.get("money") else None)
    for e in (dec_res or {}).get("decisions") or []:
        if e.get("status") == "late" and e.get("high_impact"):
            add("orange", f"قرار عالي الأثر متأخر: {e['title']}", (e.get("delay") or {}).get("sentence_ar") or "", "decisions", link=f"{LINKS['decisions']}#dec/{e['id']}",
                amount=e.get("expected_impact"))
        if (e.get("outcome") or {}).get("result") in ("negative", "mixed"):
            add("yellow", f"قرار بنتيجة {e['outcome']['result_ar']}: {e['title']}", e["outcome"].get("mixed_note_ar") or "", "decisions", link=f"{LINKS['decisions']}#dec/{e['id']}")
    order = {"red": 0, "orange": 1, "yellow": 2}
    ex.sort(key=lambda x: (order[x["severity"]], -(abs(x["amount"] or 0))))
    return ex[:12]


def recommendations(req, exc, dec_res):
    decs = {e["id"]: e for e in (dec_res or {}).get("decisions") or []}
    out = []
    for i, r in enumerate(req):
        d = decs.get(r.get("decision_id"))
        out.append({"priority": "high" if r["risk_if_delayed"] == "high" else "medium", "title": r["title"], "evidence": r["evidence"][:3], "impact": r.get("expected_impact"),
                    "owner": r.get("owner"), "decision_status_ar": d["status_ar"] if d else "لم يُنشأ قرار بعد", "link": r["link"]})
    seen = {o["title"] for o in out}
    for x in exc:
        if x["severity"] == "yellow" or x["title_ar"] in seen:
            continue
        out.append({"priority": "high" if x["severity"] == "red" else "medium", "title": f"معالجة: {x['title_ar']}", "evidence": [x["detail_ar"]], "impact": x.get("amount"),
                    "owner": None, "decision_status_ar": "يحتاج قرار", "link": x["link"]})
    out.sort(key=lambda o: o["priority"] != "high")
    return {"high": [o for o in out if o["priority"] == "high"][:5], "medium": [o for o in out if o["priority"] == "medium"][:5],
            "note_ar": "التوصيات مرتبة من القرارات المعلقة والاستثناءات — كل توصية بدليلها وأثرها وحالة قرارها"}


# ═══════════════════════════════════════════════════════════
# 3.10.3 — Executive Board Overview + الملخص التنفيذي (حتمي من الأرقام)
# ═══════════════════════════════════════════════════════════
def _kpi(fin, key):
    return next((k for k in (fin or {}).get("kpis") or [] if k.get("key") == key), {})


def summary(fin, risk_rep, fc, br, req, pred, mods, see_fin=True):
    parts = []
    rev = _kpi(fin, "revenue")
    if rev.get("change") is not None:
        parts.append(f"الإيراد في {fin.get('period')} {'ارتفع' if rev['change'] > 0 else 'انخفض'} {abs(rev['change']):.1f}% عن الشهر السابق")
    if see_fin:
        np_ = _kpi(fin, "net_profit")
        if np_.get("change") is not None and rev.get("change") is not None:
            if rev["change"] < 0 and np_["change"] < rev["change"]:
                parts.append("وتراجع صافي الربح بوتيرة أسرع من المبيعات")
            else:
                parts.append("ونمو الربحية " + ("أقل من" if np_["change"] < rev["change"] else "أعلى من") + " نمو المبيعات")
        if (fin or {}).get("variance", {}).get("main_cause_ar"):
            parts.append(fin["variance"]["main_cause_ar"])
    if fc.get("status") == "ok" and fc.get("target_gap") and (fc["target_gap"].get("gap") or 0) > 0:
        parts.append(f"التوقع الحالي يشير إلى فجوة {fmt(fc['target_gap']['gap'])} عن الهدف السنوي")
    if br.get("status") == "ok":
        parts.append(f"فرع {br['best']['branch']} الأفضل أداءً وفرع {br['worst']['branch']} الأكثر حاجة للتدخل")
    para = "، ".join(parts[:2]) + ("." if parts else "") + (" " + ". ".join(parts[2:]) + "." if len(parts) > 2 else "")
    top = (risk_rep.get("top") or [None])[0]
    biggest_risk = None if not top else {"ar": f"{top['ar']} ({top['score']:.0f}/100)", "detail_ar": f"أكبر مسبب: {top['driver']['name_ar']}",
                                         "amount": (top.get("impact_range") or [None, None])[1], "link": top["link"]}
    opps = []
    for o in (pred or {}).get("opportunities") or []:
        if o.get("range") and o["range"][1]:
            opps.append({"ar": o["ar"], "amount": o["range"][1], "detail_ar": (o.get("note_ar") or ""), "link": LINKS["prediction"]})
    lk = ((mods or {}).get("leakage") or {}).get("money") or {}
    if lk.get("opportunity"):
        opps.append({"ar": "استرداد تسرب الإيرادات", "amount": lk["opportunity"], "detail_ar": "فرصة الشهر الحالي في مركز استرداد الأموال", "link": LINKS["leakage"]})
    for r in (((pred or {}).get("gap") or {}).get("recovery") or {}).get("items") or []:
        opps.append({"ar": r["ar"], "amount": r["amount"], "detail_ar": r.get("basis_ar"), "link": LINKS["prediction"]})
    biggest_opp = max(opps, key=lambda o: o["amount"]) if opps else None
    top_dec = req[0] if req else None
    return {"paragraph_ar": para or "لا توجد بيانات كافية لملخص تنفيذي.", "facts": parts,
            "three": {"risk": biggest_risk, "opportunity": biggest_opp,
                      "decision": None if not top_dec else {"ar": top_dec["title"], "detail_ar": top_dec["recommendation_ar"], "amount": top_dec.get("expected_impact"), "link": top_dec["link"]}},
            "note_ar": "الملخص مُركّب آلياً من أرقام المحركات — بلا صياغة مخمّنة"}


# ═══════════════════════════════════════════════════════════
# 3.10.14 / 3.10.15 — القراءة المسبقة + المقارنة بالاجتماع السابق
# ═══════════════════════════════════════════════════════════
def snapshot(pack):
    fin = pack.get("financial") or {}
    rows = {r["key"]: r.get("actual") for r in fin.get("rows") or []}
    g = (pack.get("goals") or {}).get("company_goals") or []
    pr = [x["progress_pct"] for x in g if x.get("progress_pct") is not None]
    return {"as_of": pack.get("as_of"), "period": pack.get("period"), "lenses": [{"key": l["key"], "score": l["score"]} for l in pack.get("lenses") or []],
            "metrics": {"revenue_month": pack.get("_rev_month"), "net_profit_month": pack.get("_np_month"), "revenue_ytd": rows.get("revenue"),
                        "net_profit_ytd": rows.get("net_profit"), "risk_index": (pack.get("risk") or {}).get("index"), "leakage": pack.get("_leakage"),
                        "goal_progress_avg": round(sum(pr) / len(pr), 2) if pr else None},
            "risk_categories": {r["key"]: r["score"] for r in (pack.get("risk") or {}).get("top") or []},
            "goals_status": {str(x["id"]): x["status"] for x in g},
            "decisions_done": sorted(pack.get("_done_ids") or []), "decisions_measured": sorted(pack.get("_measured_ids") or [])}


def comparison(cur, prev, goals_names=None, dec_titles=None):
    if not prev:
        return {"available": False, "reason_ar": "لا يوجد اجتماع سابق محفوظ — احفظ حزمة المجلس الآن لتصبح أساس المقارنة القادمة"}
    cm, pm = cur["metrics"], prev.get("metrics") or {}
    pct = lambda a, b: None if (a is None or b in (None, 0)) else round((a / b - 1) * 100, 1)
    rows = [{"key": "revenue", "ar": "الإيراد (الشهر)", "change_pct": pct(cm.get("revenue_month"), pm.get("revenue_month")), "now": cm.get("revenue_month"), "before": pm.get("revenue_month")},
            {"key": "profit", "ar": "صافي الربح (الشهر)", "change_pct": pct(cm.get("net_profit_month"), pm.get("net_profit_month")) if (pm.get("net_profit_month") or 0) > 0 else None,
             "now": cm.get("net_profit_month"), "before": pm.get("net_profit_month")},
            {"key": "risk", "ar": "مؤشر المخاطر", "change_points": None if (cm.get("risk_index") is None or pm.get("risk_index") is None) else round(cm["risk_index"] - pm["risk_index"], 1),
             "now": cm.get("risk_index"), "before": pm.get("risk_index")},
            {"key": "leakage", "ar": "التسرب", "change_pct": pct(cm.get("leakage"), pm.get("leakage")), "now": cm.get("leakage"), "before": pm.get("leakage")},
            {"key": "goals", "ar": "متوسط تقدم الأهداف", "change_points": None if (cm.get("goal_progress_avg") is None or pm.get("goal_progress_avg") is None) else round(cm["goal_progress_avg"] - pm["goal_progress_avg"], 1),
             "now": cm.get("goal_progress_avg"), "before": pm.get("goal_progress_avg")}]
    done_new = [i for i in cur["decisions_done"] if i not in set(prev.get("decisions_done") or [])]
    meas_new = [i for i in cur["decisions_measured"] if i not in set(prev.get("decisions_measured") or [])]
    risk_up = [{"key": k, "before": prev.get("risk_categories", {}).get(k), "now": v} for k, v in cur["risk_categories"].items()
               if prev.get("risk_categories", {}).get(k) is not None and v - prev["risk_categories"][k] >= 3]
    goals_changed = [{"id": k, "name": (goals_names or {}).get(k, k), "before": prev.get("goals_status", {}).get(k), "now": v} for k, v in cur["goals_status"].items()
                     if prev.get("goals_status", {}).get(k) and prev["goals_status"][k] != v]
    lines = []
    for r in rows:
        if r.get("change_pct") is not None:
            lines.append(f"{r['ar']}: {r['change_pct']:+.1f}%")
        elif r.get("change_points") is not None:
            lines.append(f"{r['ar']}: {r['change_points']:+.1f} نقطة")
    lines.append(f"قرارات اكتملت: {len(done_new)}")
    return {"available": True, "since": prev.get("as_of"), "rows": rows, "decisions_completed": len(done_new), "decisions_completed_titles": [(dec_titles or {}).get(i, f"#{i}") for i in done_new],
            "results_achieved": [(dec_titles or {}).get(i, f"#{i}") for i in meas_new], "risks_increased": risk_up, "goals_changed": goals_changed, "lines_ar": lines}


def preread(cmpn, exc, req, risk_rep):
    return {"what_changed": cmpn.get("lines_ar") if cmpn.get("available") else [cmpn.get("reason_ar")],
            "attention": [f"{x['icon']} {x['title_ar']}" for x in exc if x["severity"] == "red"][:5],
            "decisions_required": [f"{r['title']} — {r['recommendation_ar']}" for r in req][:5],
            "risks_increased": [f"{RE.CATS.get(r['key'], {}).get('ar', r['key'])}: {r['before']:.0f} → {r['now']:.0f}" for r in cmpn.get("risks_increased") or []],
            "goals_changed": [f"{g['name']}: {G.STATUS_AR.get(g['before'], g['before'])} → {G.STATUS_AR.get(g['now'], g['now'])}" for g in cmpn.get("goals_changed") or []],
            "decisions_completed": cmpn.get("decisions_completed_titles") or [], "results_achieved": cmpn.get("results_achieved") or [],
            "headline_ar": risk_rep.get("headline_ar")}


# ═══════════════════════════════════════════════════════════
# 3.10.16 — Board Presentation Mode (رسالة واحدة · 3 أرقام · دليل · قرار)
# ═══════════════════════════════════════════════════════════
def slides(pack):
    S = []
    sec = lambda k: (lambda v: {} if (not v or (isinstance(v, dict) and v.get("restricted"))) else v)(pack.get(k))
    fin, rr, fc, br, gs, st = (sec(k) for k in ("financial", "risk", "forecast", "branches", "goals", "status"))
    L = pack.get("lenses") if isinstance(pack.get("lenses"), list) else []
    req = pack.get("decisions_required") if isinstance(pack.get("decisions_required"), list) else []
    num = lambda label, v, u="SAR": {"label": label, "value": v, "unit": u}
    sm = sec("summary")
    S.append({"key": "summary", "title": "الملخص التنفيذي", "message": f"{st.get('light', '')} {st.get('ar', '')}",
              "numbers": [num("مؤشر المخاطر", rr.get("index"), "/100")] + [num(r["ar"] + " (منذ بداية السنة)", r["actual"]) for r in (fin.get("rows") or []) if r["key"] in ("revenue", "net_profit")],
              "evidence": [sm.get("paragraph_ar")], "decision": ((sm.get("three") or {}).get("decision") or {}).get("ar")})
    if L:
        w = min([l for l in L if l["score"] is not None] or L, key=lambda l: l["score"] if l["score"] is not None else 999)
        S.append({"key": "lenses", "title": "المحاور الخمسة", "message": f"{w['ar']} هو المحور الأضعف ({w['score']}/100)" if w["score"] is not None else "بيانات المحاور غير كافية",
                  "numbers": [num(l["ar"], l["score"], "/100") for l in sorted([l for l in L if l["score"] is not None], key=lambda l: l["score"])[:3]],
                  "evidence": w.get("evidence") or [], "decision": None})
    if fin.get("rows"):
        mi = (fin.get("money") or {}).get("largest_increase")
        S.append({"key": "financial", "title": "الأداء المالي", "message": fin.get("variance_main_ar") or "الأداء المالي مقابل الموازنة",
                  "numbers": [num(r["ar"], r["actual"]) for r in fin["rows"][:3]], "evidence": [f"أكبر زيادة غير متوقعة: {mi['ar']} {mi['change_pct']:+.1f}%"] if mi else [], "decision": None})
    gl = next((l for l in L if l["key"] == "growth"), None)
    if gl:
        S.append({"key": "growth", "title": "النمو", "message": gl["evidence"][0] if gl.get("evidence") else (gl.get("reason_ar") or ""), "numbers": [num("درجة النمو", gl["score"], "/100")],
                  "evidence": gl.get("evidence")[1:] if gl.get("evidence") else [], "decision": None})
    if rr.get("top"):
        t = rr["top"][0]
        S.append({"key": "risk", "title": "المخاطر", "message": f"{t['ar']} هو الخطر الأكبر ({t['score']:.0f}/100)", "numbers": [num(r["ar"], r["score"], "/100") for r in rr["top"][:3]],
                  "evidence": [f"المسبب: {t['driver']['name_ar']}"] + (t["driver"].get("evidence") or [])[:1], "decision": (t.get("mitigation") or {}).get("title")})
    if br.get("status") == "ok":
        S.append({"key": "branches", "title": "أفضل وأسوأ فرع", "message": f"{br['best']['branch']} الأفضل · {br['worst']['branch']} يحتاج تدخلاً", "numbers": [],
                  "evidence": br["worst"]["why"][:3], "decision": None})
    if fc.get("status") == "ok":
        S.append({"key": "forecast", "title": "التوقعات", "message": (fc.get("target_gap") or {}).get("sentence_ar") or fc.get("basis_ar"),
                  "numbers": [num(m["label"], m["value"], m["unit"]) for m in fc["rows"][:3]], "evidence": [f"الثقة {fc['confidence']['score']:.0f}% ({fc['confidence']['label_ar']})"], "decision": None})
    if gs.get("company_goals"):
        S.append({"key": "goals", "title": "الأهداف وOKR", "message": gs.get("headline_ar"), "numbers": [num(g["name"], g["progress_pct"], "%") for g in gs["company_goals"][:3]],
                  "evidence": [f"{g['icon']} {g['name']}: {g['status_ar']}" for g in gs["company_goals"][:3]], "decision": None})
    if req:
        S.append({"key": "decisions_required", "title": "القرارات المطلوبة", "message": f"{len(req)} قرارات تحتاج اعتماد المجلس",
                  "numbers": [num(r["title"], r.get("expected_impact")) for r in req[:3]], "evidence": [r["recommendation_ar"] for r in req[:3]], "decision": req[0]["title"]})
    return S


# ═══════════════════════════════════════════════════════════
# 3.10.22 — 11 Sector Configuration · 3.10.25 — Data Quality / Confidence
# ═══════════════════════════════════════════════════════════
SECTOR_BOARD = {"fnb": [("gross_margin", "هامش مجمل الربح (تكلفة الطعام)"), ("payroll_pct", "تكلفة العمالة من الإيراد"), ("on_time", "التوصيل في الوقت"), ("branches", "الفروع")],
                "retail": [("inventory_turnover", "دوران المخزون"), ("stockout_rate", "نفاد الأصناف"), ("aov", "حجم السلة"), ("branches", "الفروع")],
                "ecommerce": [("aov", "متوسط الطلب"), ("return_rate", "المرتجعات"), ("on_time", "التسليم في الوقت"), ("repeat_purchase", "الشراء المتكرر")],
                "manufacturing": [("gross_margin", "هامش الإنتاج"), ("on_time", "التسليم في الموعد"), ("inventory_turnover", "دوران المخزون"), ("payroll_pct", "تكلفة العمالة")],
                "contracting": [("gross_margin", "هامش المشاريع"), ("dso", "التحصيل (DSO)"), ("expense_ratio", "تكلفة التشغيل"), ("on_time", "إنجاز في الموعد")],
                "distribution": [("dso", "التحصيل (DSO)"), ("inventory_turnover", "دوران المخزون"), ("stockout_rate", "النفاد"), ("gross_margin", "الهامش")],
                "services": [("revenue_per_employee", "الإيراد لكل موظف"), ("sla_compliance", "مستوى الخدمة"), ("employee_turnover", "دوران الموظفين"), ("payroll_pct", "تكلفة العمالة")],
                "clinics": [("aov", "الإيراد لكل زيارة"), ("dso", "التحصيل"), ("utilization", "استغلال الطاقة"), ("employee_turnover", "استقرار الكادر")],
                "hospitals": [("utilization", "استغلال الطاقة"), ("aov", "الإيراد لكل حالة"), ("dso", "التحصيل (تأمين)"), ("employee_turnover", "استقرار الكادر")],
                "logistics": [("on_time", "التسليم في الوقت"), ("fulfillment_time", "زمن التنفيذ"), ("expense_ratio", "تكلفة التشغيل"), ("utilization", "استغلال الأسطول")],
                "other": [("gross_margin", "هامش مجمل الربح"), ("net_margin", "صافي الهامش"), ("dso", "التحصيل"), ("branches", "الفروع")]}


def sector_kpis(sector, ctx, fin, ops):
    rows = []
    for code, ar in SECTOR_BOARD.get(sector or "other", SECTOR_BOARD["other"]):
        val, unit, src, reason = None, "", "benchmark", None
        if code == "payroll_pct":
            l = next((x for x in (fin or {}).get("expenses", {}).get("lines") or [] if x.get("key") == "payroll"), None)
            val, unit, src = (l or {}).get("pct_of_revenue"), "%", "financial"
            reason = None if val is not None else "لا بند رواتب في المصروفات"
        elif code == "branches":
            val, unit, src = len(ctx.get("branches") or []) or None, "فرع", "sales"
        elif code == "utilization":
            us = [b.get("utilization") for b in (ops or {}).get("branches") or [] if b.get("utilization") is not None]
            val, unit, src = (round(sum(us) / len(us), 1) if us else None), "%", "operations"
            reason = None if us else "لا بيانات طاقة تشغيلية"
        else:
            c = (ctx.get("cm") or {}).get(code) or {}
            val, unit = c.get("value"), (G.METRICS.get(code) or {}).get("unit", "")
            reason = c.get("reason_ar")
        rows.append(metric(code, ar, val, src, unit=unit, reason=reason))
    return {"sector": sector or "other", "rows": rows, "note_ar": "محرك مجلس واحد لكل القطاعات — القطاع يحدد المؤشرات المعروضة فقط"}


def data_quality(ctx, fin, pred, risk, appx):
    de = ctx.get("data_end")
    age = (ctx["today"] - de).days if de else None
    months = len(PE.series_of(ctx["sm"]["company"], "revenue", exclude=ctx["sm"].get("partial_month"))) if ctx["sm"]["company"] else 0
    rev = "high" if (months >= 12 and age is not None and age <= 45) else ("medium" if months >= 3 else "low")
    q = (fin or {}).get("quality") or {}
    prof = "na" if not (fin or {}).get("summary") else ("high" if (q.get("cogs_coverage_pct") or 0) >= 95 and not q.get("net_profit_missing") else
                                                       "medium" if (q.get("cogs_coverage_pct") or 0) >= 80 else "low")
    cust_cat = next((c for c in (risk or {}).get("categories") or [] if c["key"] == "customer"), {})
    cust = "medium" if cust_cat.get("score") is not None else "low"
    fc = (pred or {}).get("outlook", {}).get("confidence", {}).get("score") if (pred or {}).get("status") == "ok" else None
    bs_missing = len(((appx or {}).get("balance_sheet") or {}).get("missing") or [])
    AR = {"high": "مرتفعة", "medium": "متوسطة", "low": "منخفضة", "na": "غير متاحة"}
    rows = [{"key": "revenue", "ar": "الإيراد", "level": rev, "level_ar": AR[rev], "basis_ar": f"{months} شهراً من المبيعات · آخر بيانات {de.isoformat() if de else '—'}"},
            {"key": "profit", "ar": "الربح", "level": prof, "level_ar": AR[prof], "basis_ar": f"تغطية التكلفة {q.get('cogs_coverage_pct')}%" + (f" · ناقص: {'، '.join(q.get('net_profit_missing'))}" if q.get("net_profit_missing") else "")},
            {"key": "customer", "ar": "العملاء", "level": cust, "level_ar": AR[cust], "basis_ar": "من أسماء العملاء في فواتير المبيعات"},
            {"key": "forecast", "ar": "التوقع", "level": None, "level_ar": f"{fc:.0f}%" if fc is not None else "غير متاح", "basis_ar": "ثقة محرك التنبؤ 3.7"},
            {"key": "balance_sheet", "ar": "الميزانية العمومية", "level": "low" if bs_missing > 3 else "medium" if bs_missing else "high",
             "level_ar": AR["low" if bs_missing > 3 else "medium" if bs_missing else "high"], "basis_ar": f"{bs_missing} بنداً غير مُدخل"}]
    return {"rows": rows, "data_end": de.isoformat() if de else None, "age_days": age, "stale": age is not None and age > 45,
            "note_ar": "لا يعطي تقرير المجلس انطباع أن كل شيء مؤكد 100% — هذا إفصاح عن ثقة كل جزء"}


# ═══════════════════════════════════════════════════════════
# 3.10.23 — RBAC + Confidentiality
# ═══════════════════════════════════════════════════════════
ALL = {k for k, _ in SECTIONS}
MANAGER_SECTIONS = {"overview", "lenses", "branches", "exceptions", "goals", "initiatives", "decision_status", "forecast", "sector", "data_quality", "summary"}


def board_scope(v):
    v = v or {}
    role, title = v.get("role"), v.get("title")
    if role == "owner" or title == "ceo":
        return {"kind": "full", "ar": "كامل (مالك / رئيس تنفيذي)", "sections": ALL, "can_generate": True, "can_resolve": True, "financial": True}
    if title == "cfo" or (role == "accountant" and title != "employee"):
        return {"kind": "cfo", "ar": "مالي واستراتيجي (المدير المالي)", "sections": ALL, "can_generate": True, "can_resolve": False, "financial": True}
    if title == "board":
        return {"kind": "board", "ar": "عضو مجلس إدارة — اطلاع فقط دون تعديل البيانات", "sections": ALL, "can_generate": False, "can_resolve": False, "financial": True}
    if role == "manager" or title in ("dept_manager", "branch_manager"):
        return {"kind": "manager", "ar": "مدير — ملخص تشغيلي بلا تفاصيل مالية", "sections": MANAGER_SECTIONS, "can_generate": False, "can_resolve": False, "financial": False}
    return {"kind": "none", "ar": "غير مصرّح", "sections": set(), "can_generate": False, "can_resolve": False, "financial": False}


# ═══════════════════════════════════════════════════════════
# التجميع — حزمة المجلس
# ═══════════════════════════════════════════════════════════
def analyze_board(*, ctx, risk=None, drivers=None, pred=None, goals_res=None, dec_res=None, budget=None, previous=None, sector=None, today=None, viewer=None,
                  currency="SAR", fy_start=1, company_name=None, goal_branches=None):
    today = today or date.today()
    sc = board_scope(viewer)
    mods = ctx.get("mods") or {}
    fin, cf, ops = mods.get("finance") or {}, mods.get("cashflow") or {}, mods.get("operations") or {}
    see_fin = True          # تُبنى الحزمة كاملة ثم تُقيّد بالصلاحية (نفس القيد على النسخ المحفوظة)
    lz, status = lenses(risk, ctx["sm"], previous, pred)
    fin_sec = financial(fin, ctx["trends"], budget, fy_start, currency) if see_fin else None
    appx = appendix(fin, cf, ctx["trends"], fy_start) if see_fin else None
    rr = risk_report(risk, drivers, dec_res)
    fc = forecast(pred, see_profit=see_fin)
    brs = branches(fin, pred, drivers, ops, goal_branches)
    gsec = goals_section(goals_res)
    ini = initiatives(goals_res)
    req = decisions_required(dec_res, goals_res, risk, today)
    dst = decision_status(dec_res)
    exc = exceptions(risk, pred, goals_res, dec_res, fin, brs, see_fin)
    rec = recommendations(req, exc, dec_res)
    sm = summary(fin, rr, fc, brs, req, pred, mods, see_fin)
    period = fin.get("period") or (ctx["data_end"].strftime("%Y-%m") if ctx.get("data_end") else today.strftime("%Y-%m"))
    pack = {"version": MODEL, "as_of": today.isoformat(), "period": period, "company": company_name, "currency": currency, "scope": {k: v for k, v in sc.items() if k != "sections"},
            "status": status, "lenses": lz, "lens_rule_ar": LENS_RULE_AR, "summary": sm, "financial": fin_sec, "branches": brs, "exceptions": exc, "risk": rr,
            "forecast": fc, "goals": gsec, "initiatives": ini, "decisions_required": req, "decision_status": dst, "recommendations": rec, "appendix": appx,
            "sector": sector_kpis(sector, ctx, fin, ops), "data_quality": data_quality(ctx, fin, pred, risk, appx),
            "_rev_month": _kpi(fin, "revenue").get("current"), "_np_month": _kpi(fin, "net_profit").get("current") if see_fin else None,
            "_leakage": ((mods.get("leakage") or {}).get("overview") or {}).get("total"),
            "_done_ids": [e["id"] for e in (dec_res or {}).get("decisions") or [] if e.get("workflow") in ("completed", "measured")],
            "_measured_ids": [e["id"] for e in (dec_res or {}).get("decisions") or [] if e.get("workflow") == "measured"]}
    snap = snapshot(pack)
    cmpn = comparison(snap, previous, {str(x["id"]): x["name"] for x in (goals_res or {}).get("goals") or []},
                      {e["id"]: e["title"] for e in (dec_res or {}).get("decisions") or []})
    pack["comparison"] = cmpn
    pack["overview"] = {"period": period, "status": status, "lenses": [{k: l[k] for k in ("key", "ar", "icon", "score", "light", "status_ar", "arrow", "link")} for l in lz],
                        "three": sm["three"], "exceptions_red": sum(1 for x in exc if x["severity"] == "red"), "decisions_required": len(req),
                        "decision_sentence_ar": dst.get("sentence_ar"), "headline_ar": f"{status['light']} {status['ar']} — {len(req)} قرارات مطلوبة · {sum(1 for x in exc if x['severity'] == 'red')} استثناءات حرجة"}
    pack["preread"] = preread(cmpn, exc, req, rr)
    pack["slides"] = slides(pack)
    pack["pack_pages"] = [{"key": k, "ar": a} for k, a in PACK_PAGES]
    pack["snapshot"] = snap
    for k in ("_rev_month", "_np_month", "_leakage", "_done_ids", "_measured_ids"):
        pack.pop(k, None)
    pack["metrics"] = collect_metrics(pack)
    pack["ai_questions"] = ["ما أهم 3 مشاكل تحتاج قرار المجلس؟", "ماذا تغير منذ الاجتماع السابق؟", "هل الشركة ستحقق أهدافها؟", "ما أكبر خطر خلال 6 أشهر؟",
                            "ما الفرع الذي يحتاج تدخل؟", "ما القرار الذي تأخر أكثر؟", "ما القرارات التي حققت أفضل نتائج؟", "ما أكبر فرصة مالية؟"]
    pack["board_questions"] = BOARD_QUESTIONS
    pack["disclaimer_ar"] = "حزمة مجلس مُركّبة من محركات نبّاه بمنهجية معلنة — كل رقم بمصدره وثقته، والعلاقات ليست سببية مثبتة. سري."
    return restrict(pack, viewer)


FIN_RISK = ("profit", "liquidity")


def restrict(pack, viewer):
    """3.10.23 — يطبّق الصلاحية على الحزمة (المباشرة أو النسخة المحفوظة): أقسام كاملة + تفاصيل مالية داخل الأقسام المسموحة."""
    import copy as _c
    sc = board_scope(viewer)
    p = _c.deepcopy(pack)
    p["scope"] = {k: v for k, v in sc.items() if k != "sections"}
    p["sections_index"] = [{"key": k, "ar": a, "allowed": k in sc["sections"]} for k, a in SECTIONS]
    for k, _a in SECTIONS:
        if k not in sc["sections"] and k in p:
            p[k] = {"restricted": True, "reason_ar": "خارج صلاحيتك"}
    if sc["financial"]:
        return p
    hide = lambda d: isinstance(d, dict) and not d.get("restricted")
    if hide(p.get("risk")):
        p["risk"]["top"] = [r for r in p["risk"]["top"] if r["key"] not in FIN_RISK]
    if hide(p.get("forecast")) and p["forecast"].get("rows"):
        p["forecast"]["rows"] = [r for r in p["forecast"]["rows"] if r["key"] not in ("profit", "cash")]
        p["forecast"]["profit_gap"] = None
    if hide(p.get("branches")) and p["branches"].get("status") == "ok":
        for c in (p["branches"]["best"], p["branches"]["worst"]):
            c["metrics"] = [m for m in c["metrics"] if m["key"] not in ("margin", "sales")]
            c["why"] = [w for w in c["why"] if "هامش" not in w]
    if isinstance(p.get("exceptions"), list):
        p["exceptions"] = [x for x in p["exceptions"] if x["source"] != "financial" and not x["title_ar"].startswith("توقع الربح")
                           and not any(RE.CATS[k]["ar"] in x["title_ar"] for k in FIN_RISK)]
    if hide(p.get("summary")):
        p["summary"]["facts"] = [f for f in p["summary"]["facts"] if "ربح" not in f]
        p["summary"]["paragraph_ar"] = ("، ".join(p["summary"]["facts"]) + ".") if p["summary"]["facts"] else "ملخص تشغيلي — التفاصيل المالية خارج صلاحيتك."
        r = (p["summary"].get("three") or {}).get("risk") or {}
        if any(RE.CATS[k]["ar"] in (r.get("ar") or "") for k in FIN_RISK):
            t = (p.get("risk") or {}).get("top") or []
            p["summary"]["three"]["risk"] = {"ar": f"{t[0]['ar']} ({t[0]['score']:.0f}/100)", "detail_ar": f"أكبر مسبب: {t[0]['driver']['name_ar']}", "amount": None, "link": t[0]["link"]} if t else None
    if isinstance(p.get("overview"), dict) and not p["overview"].get("restricted"):
        p["overview"]["three"] = (p.get("summary") or {}).get("three")
    p["slides"] = [x for x in slides(p) if x["key"] not in ("financial",)]
    for x in p["slides"]:
        x["numbers"] = [n for n in x["numbers"] if "ربح" not in n["label"] and "منذ بداية السنة" not in n["label"]
                        and not any(RE.CATS[k]["ar"] in n["label"] for k in FIN_RISK)]
        if x["key"] == "summary":
            x["evidence"] = [(p.get("summary") or {}).get("paragraph_ar")]
    p["metrics"] = [m for m in p.get("metrics") or [] if m["source_module"] != "financial" and not str(m["metric"]).startswith(("forecast:profit", "forecast:cash"))]
    if isinstance(p.get("snapshot"), dict):
        p["snapshot"] = {"restricted": True}
    return p


# 3.10.20 — أسئلة المجلس: كل سؤال يفتح سلسلة الأدلة عبر الوحدات
BOARD_QUESTIONS = [{"q": "لماذا انخفض الربح؟", "chain": [("financial", "الوحدة المالية — جسر الربح"), ("purchases", "المشتريات — تكلفة الشراء"),
                                                         ("leakage", "استرداد الأموال — التسرب"), ("drivers", "المسببات — الأسباب المرشحة")]},
                   {"q": "لماذا الفرع الأضعف متراجع؟", "chain": [("sales", "المبيعات"), ("inventory", "المخزون"), ("operations", "العمليات"), ("risk", "المخاطر"), ("drivers", "المسببات")]},
                   {"q": "هل سنحقق الهدف السنوي؟", "chain": [("prediction", "التنبؤ — الإسقاط والفجوة"), ("goals", "الأهداف — الحالة والمسار"), ("decisions", "القرارات — الإجراءات المعتمدة")]},
                   {"q": "أين تذهب السيولة؟", "chain": [("cashflow", "التدفق النقدي — المحركات"), ("financial", "الوحدة المالية — الذمم والمصروفات")]}]


def collect_metrics(pack):
    """صفوف Board Report Metric: كل رقم رئيسي مع مصدره وثقته (للحفظ مع النسخة)."""
    out = []
    for r in (pack.get("financial") or {}).get("rows") or [] if isinstance(pack.get("financial"), dict) and not pack["financial"].get("restricted") else []:
        e = r["evidence"]
        out.append({"metric": r["key"], "label": r["ar"], "value": r["actual"], "source_module": "financial", "confidence": e.get("confidence"), "period": e.get("period")})
    for l in pack.get("lenses") or []:
        out.append({"metric": f"lens:{l['key']}", "label": l["ar"], "value": l["score"], "source_module": l["source"], "confidence": None, "period": pack.get("period")})
    rk = pack.get("risk") or {}
    if isinstance(rk, dict) and rk.get("index") is not None:
        out.append({"metric": "risk_index", "label": "مؤشر المخاطر", "value": rk["index"], "source_module": "risk", "confidence": rk.get("confidence_pct"), "period": pack.get("as_of")})
    fc = pack.get("forecast") or {}
    for m in fc.get("rows") or [] if isinstance(fc, dict) else []:
        out.append({"metric": f"forecast:{m['key']}", "label": m["label"], "value": m["value"], "source_module": m["source"], "confidence": m.get("confidence"), "period": m.get("period")})
    return out


def ai_context(pack):
    keep = lambda x: x if not (isinstance(x, dict) and x.get("restricted")) else None
    return {"period": pack.get("period"), "status": pack.get("status"), "lenses": [{k: l[k] for k in ("ar", "score", "status_ar", "arrow", "evidence")} for l in pack.get("lenses") or []],
            "summary": (keep(pack.get("summary")) or {}).get("facts"), "three": (keep(pack.get("summary")) or {}).get("three"),
            "financial": [{k: r[k] for k in ("ar", "actual", "budget", "variance", "variance_pct")} for r in (keep(pack.get("financial")) or {}).get("rows") or []],
            "exceptions": [x["title_ar"] + ": " + x["detail_ar"] for x in (keep(pack.get("exceptions")) if isinstance(pack.get("exceptions"), list) else []) or []][:10],
            "risks": [{k: r.get(k) for k in ("ar", "score", "impact_range")} | {"driver": r["driver"]["name_ar"], "mitigation": (r.get("mitigation") or {}).get("title")}
                      for r in (keep(pack.get("risk")) or {}).get("top") or []],
            "forecast": [{"label": m["label"], "value": m["value"], "confidence": m.get("confidence")} for m in (keep(pack.get("forecast")) or {}).get("rows") or []],
            "goals": (keep(pack.get("goals")) or {}).get("company_goals"), "decisions_required": pack.get("decisions_required") if isinstance(pack.get("decisions_required"), list) else None,
            "decision_status": keep(pack.get("decision_status")), "comparison": (keep(pack.get("comparison")) or {}).get("lines_ar"),
            "branches": {k: (keep(pack.get("branches")) or {}).get(k, {}).get("branch") for k in ("best", "worst")} if (keep(pack.get("branches")) or {}).get("status") == "ok" else None,
            "rule": "اشرح من أرقام حزمة المجلس فقط. لا تخترع أرقاماً ولا تُصدر قراراً بدل المجلس. العلاقات ليست سببية مثبتة. اذكر الثقة."}
