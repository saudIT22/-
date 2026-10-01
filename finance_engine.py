"""
NABBAH 2.11 — Financial Unit engine (pure, deterministic; AI only explains).
No separate financial database: revenue from Sales (2.5), COGS from product/inventory cost (2.6), expenses from the
expense file or cash movements (2.8), payroll reconciled with HR (2.9), branch context from Operations (2.10).
Every line carries its source; a missing input makes a line «unavailable», never 0.
"""
import os, sys, hashlib
from decimal import Decimal
from statistics import median

_here = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21", "../phase22", "../phase23", "../phase24"):
    _p = os.path.join(_here, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from nabbah_finance import to_decimal, round_money, safe_divide

FIN_VERSION = "1.0"
D0 = Decimal("0")
RULES = {"margin_drop_pp": 1.0, "expense_spike_pct": 15.0, "cost_coverage_min": 80.0, "payroll_gap_pct": 20.0,
         "budget_over_pct": 5.0, "forecast_months": 6}
# تصنيف بنود المصروفات (قائمة الدخل). ما لا يطابق = «أخرى». الموردون/التمويل خارج المصروفات التشغيلية.
OPEX = [("payroll", "الرواتب والأجور", ("راتب", "رواتب", "اجور", "أجور", "salary", "payroll", "gosi", "تامينات", "تأمينات", "بدل")),
        ("rent", "الإيجار", ("ايجار", "إيجار", "rent", "lease")),
        ("marketing", "التسويق", ("تسويق", "اعلان", "إعلان", "marketing", "ads", "حملة")),
        ("delivery", "التوصيل", ("توصيل", "شحن", "delivery", "shipping", "courier", "هنقرستيشن", "جاهز", "مرسول")),
        ("software", "البرمجيات والاشتراكات", ("برنامج", "اشتراك", "software", "subscription", "saas", "license")),
        ("maintenance", "الصيانة", ("صيانة", "صيانه", "maintenance", "repair", "اصلاح")),
        ("utilities", "المرافق", ("كهرباء", "مياه", "اتصالات", "انترنت", "utilities", "electric", "water", "internet"))]
BELOW = [("tax", "الضرائب والزكاة", ("ضريبة", "ضريبه", "زكاة", "زكاه", "vat", "zakat", "tax")),
         ("depreciation", "الإهلاك", ("اهلاك", "إهلاك", "depreciation", "amortization")),
         ("interest", "الفوائد وتكاليف التمويل", ("فائدة", "فوائد", "عمولة تمويل", "interest", "finance charge"))]
EXCLUDE = ("مورد", "موردين", "مشتريات", "supplier", "vendor payment", "purchase", "سداد قرض", "قرض", "loan", "تحويل داخلي",
           "transfer", "ايداع", "إيداع", "تحصيل")
LABEL = {k: l for k, l, _ in OPEX + BELOW}
LABEL["other"] = "مصروفات أخرى"


def _n(s):
    return str(s or "").strip().lower().replace("أ", "ا").replace("إ", "ا").replace("ة", "ه").replace("ى", "ي")


def classify_expense(text):
    t = _n(text)
    for key, _, words in BELOW + OPEX:
        if any(_n(w) in t for w in words):
            return key
    return "other"


def _d(v):
    return to_decimal(v) if v not in (None, "") else None


def _m(v):
    return None if v is None else float(round_money(v))


def _pct(a, b, p=1):
    r = safe_divide(a, b)
    return None if r is None else round(float(r) * 100, p)


def _chg(a, b):
    return _pct(a - b, abs(b)) if a is not None and b not in (None, 0, D0) else None


def _sid(*p):
    return "fin-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def _ym_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _bucket(ym, grain):
    if grain == "quarter":
        return f"{ym[:4]}-Q{(int(ym[5:7]) - 1) // 3 + 1}"
    if grain == "year":
        return ym[:4]
    return ym


def build_expense_lines(expense_rows=None, cash_movements=None):
    """مصدر المصروفات: ملف المصروفات إن وُجد لذلك الشهر، وإلا الحركات النقدية الخارجة (أساس نقدي)."""
    lines = []
    exp_months = set()
    for e in expense_rows or []:
        a = _d(e.get("amount"))
        if a is None or not e.get("date"):
            continue
        ym = str(e["date"])[:7]
        exp_months.add(ym)
        lines.append({"ym": ym, "cat": classify_expense(f"{e.get('category') or ''} {e.get('description') or ''}"),
                      "amount": abs(a), "branch": e.get("branch_name"), "source": "expense", "desc": e.get("description") or e.get("category")})
    for m in cash_movements or []:
        if m.get("direction") != "out" or not m.get("date"):
            continue
        ym = str(m["date"])[:7]
        if ym in exp_months:
            continue
        text = f"{m.get('category') or ''} {m.get('movement_type') or ''} {m.get('counterparty') or ''}"
        if any(_n(w) in _n(text) for w in EXCLUDE):
            continue          # دفعات الموردين = تكلفة المشتريات (COGS) — لا تُحسب مرتين
        a = _d(m.get("amount"))
        if a is None:
            continue
        lines.append({"ym": ym, "cat": classify_expense(text), "amount": abs(a), "branch": m.get("branch_name"),
                      "source": "cash", "desc": m.get("movement_type")})
    return lines


def analyze_finance(sales_rows, *, cost_map=None, expense_lines=None, employees=None, cash=None, receivables_total=None,
                    inventory_value=None, inventory_value_prev=None, settings=None, period=None, grain="month",
                    branch_context=None, currency="SAR", rules=None):
    R = dict(RULES, **(rules or {}))
    st, cost_map, expense_lines, cash = settings or {}, cost_map or {}, expense_lines or [], cash or {}
    rows = [r for r in sales_rows if r.get("date")]
    if not rows:
        return {"has_data": False, "version": f"fin-v{FIN_VERSION}",
                "message_ar": "لا توجد مبيعات بعد — الوحدة المالية تُبنى من بيانات الوحدات الأخرى، وأولها المبيعات.",
                "required": {"required": ["المبيعات (2.5)"],
                             "recommended": ["تكلفة المنتجات (البيانات الأساسية أو المخزون)", "المصروفات أو الكشف البنكي (2.8)", "الموظفون (2.9)"],
                             "optional": ["ملف المصروفات المحاسبي", "بنود الميزانية العمومية والموازنة (إدخال يدوي)"]}}
    for r in rows:
        r["_ym"] = str(r["date"])[:7]
        g, dsc, ret = _d(r.get("gross_sales")), _d(r.get("discount")) or D0, _d(r.get("returns")) or D0
        net = _d(r.get("net_sales"))
        r["_gross"] = g if g is not None else (net + dsc + ret if net is not None else D0)
        r["_disc"], r["_ret"] = dsc, ret
        r["_net"] = net if net is not None else r["_gross"] - dsc - ret
        q, sku = _d(r.get("quantity")), r.get("product_sku")
        uc = cost_map.get((sku, r["_ym"])) or cost_map.get(sku)
        r["_cogs"] = (q * _d(uc)) if q is not None and uc not in (None, "") else None
    months = sorted({r["_ym"] for r in rows})
    cur = period if period in months else months[-1]
    prev, yoy = _ym_add(cur, -1), _ym_add(cur, -12)
    dep_m, int_m = _d(st.get("depreciation_monthly")), _d(st.get("interest_monthly"))

    # رواتب تقديرية من ملف الموظفين (للمطابقة فقط): الموظفون النشطون في الشهر × التكلفة الحالية
    def hr_payroll(ym, branch=None):
        if not employees:
            return None
        end = f"{ym}-31"
        tot, have = D0, False
        for e in employees:
            c = _d(e.get("monthly_cost"))
            if c is None or (branch and e.get("branch_name") != branch):
                continue
            h, t = str(e.get("hire_date") or "")[:10], str(e.get("termination_date") or "")[:10]
            if (not h or h <= end) and (not t or t > f"{ym}-01"):
                tot += c
                have = True
        return tot if have else None

    def pnl(ym_set, branch=None):
        rs = [r for r in rows if r["_ym"] in ym_set and (branch is None or r.get("branch_name") == branch)]
        if not rs:
            return None
        gross = sum((r["_gross"] for r in rs), D0)
        disc, ret = sum((r["_disc"] for r in rs), D0), sum((r["_ret"] for r in rs), D0)
        net = sum((r["_net"] for r in rs), D0)
        costed = [r for r in rs if r["_cogs"] is not None]
        cov = _pct(sum((r["_net"] for r in costed), D0), net) if net else None
        cogs = sum((r["_cogs"] for r in costed), D0)
        if costed and cov is not None and cov < 100:      # تعميم نسبة التكلفة على الإيراد غير المكلَّف — مُعلن
            ratio = safe_divide(cogs, sum((r["_net"] for r in costed), D0))
            cogs_est = cogs + (net - sum((r["_net"] for r in costed), D0)) * ratio if ratio is not None else None
        else:
            cogs_est = cogs if costed else None
        lines = [x for x in expense_lines if x["ym"] in ym_set and (branch is None or x.get("branch") == branch)]
        opex = {}
        for x in lines:
            if x["cat"] in dict((k, 1) for k, _, _ in OPEX) or x["cat"] == "other":
                opex[x["cat"]] = opex.get(x["cat"], D0) + x["amount"]
        below = {k: sum((x["amount"] for x in lines if x["cat"] == k), D0) for k, _, _ in BELOW}
        n_months = len(ym_set)
        if dep_m is not None and branch is None:
            below["depreciation"] = below["depreciation"] + dep_m * n_months
        if int_m is not None and branch is None:
            below["interest"] = below["interest"] + int_m * n_months
        has_opex = bool(lines)
        opex_total = sum(opex.values(), D0) if has_opex else None
        gp = net - cogs_est if cogs_est is not None else None
        ebitda = gp - opex_total if gp is not None and opex_total is not None else None
        dep_known = (dep_m is not None or below["depreciation"] > 0) if branch is None else False
        int_known = (int_m is not None or below["interest"] > 0) if branch is None else False
        np_ = ebitda - below["tax"] - below["depreciation"] - below["interest"] if ebitda is not None else None
        missing = [lab for ok, lab in ((dep_known, "الإهلاك"), (int_known, "الفوائد")) if not ok]
        return {"gross_sales": gross, "discounts": disc, "returns": ret, "net_revenue": net, "cogs": cogs_est,
                "cogs_coverage_pct": cov, "gross_profit": gp, "opex": opex, "opex_total": opex_total, "ebitda": ebitda,
                "tax": below["tax"], "depreciation": below["depreciation"], "interest": below["interest"], "net_profit": np_,
                "net_profit_missing": missing, "expense_sources": sorted({x["source"] for x in lines}),
                "gross_margin": _pct(gp, net), "ebitda_margin": _pct(ebitda, net), "net_margin": _pct(np_, net),
                "opex_ratio": _pct(opex_total, net)}
    P, PP, PY = pnl({cur}), pnl({prev}), pnl({yoy})
    budget = st.get("budget") or {}
    b_rev = _d(budget.get("revenue_monthly"))
    b_opex = {k: _d(v) for k, v in (budget.get("opex_monthly") or {}).items() if v not in (None, "")}

    def line(key, ar, cur_v, prev_v, yoy_v, bud=None, inverse=False, level=0):
        return {"key": key, "label": ar, "level": level, "actual": _m(cur_v), "previous": _m(prev_v), "yoy": _m(yoy_v),
                "budget": _m(bud), "var_prev": _m(cur_v - prev_v) if cur_v is not None and prev_v is not None else None,
                "var_prev_pct": _chg(cur_v, prev_v), "var_budget": _m(cur_v - bud) if cur_v is not None and bud is not None else None,
                "var_budget_pct": _chg(cur_v, bud), "inverse": inverse, "available": cur_v is not None}
    g = lambda p, k: (p or {}).get(k)
    statement = [line("gross_sales", "إجمالي المبيعات", P["gross_sales"], g(PP, "gross_sales"), g(PY, "gross_sales")),
                 line("discounts", "الخصومات", P["discounts"], g(PP, "discounts"), g(PY, "discounts"), inverse=True, level=1),
                 line("returns", "المرتجعات", P["returns"], g(PP, "returns"), g(PY, "returns"), inverse=True, level=1),
                 line("net_revenue", "صافي الإيراد", P["net_revenue"], g(PP, "net_revenue"), g(PY, "net_revenue"), b_rev),
                 line("cogs", "تكلفة المبيعات", P["cogs"], g(PP, "cogs"), g(PY, "cogs"), inverse=True),
                 line("gross_profit", "مجمل الربح", P["gross_profit"], g(PP, "gross_profit"), g(PY, "gross_profit"))]
    cats = [k for k, _, _ in OPEX] + ["other"]
    for k in cats:
        if k in P["opex"] or (PP and k in PP["opex"]):
            statement.append(line(k, LABEL[k], P["opex"].get(k, D0) if P["opex_total"] is not None else None,
                                  (PP["opex"].get(k, D0) if PP and PP["opex_total"] is not None else None),
                                  (PY["opex"].get(k, D0) if PY and PY["opex_total"] is not None else None), b_opex.get(k), inverse=True, level=1))
    statement += [line("opex_total", "المصروفات التشغيلية", P["opex_total"], g(PP, "opex_total"), g(PY, "opex_total"),
                       sum(b_opex.values(), D0) if b_opex else None, inverse=True),
                  line("ebitda", "EBITDA", P["ebitda"], g(PP, "ebitda"), g(PY, "ebitda")),
                  line("tax", "الضرائب والزكاة", P["tax"], g(PP, "tax"), g(PY, "tax"), inverse=True, level=1),
                  line("depreciation", "الإهلاك", P["depreciation"] if "الإهلاك" not in P["net_profit_missing"] else None,
                       g(PP, "depreciation"), g(PY, "depreciation"), inverse=True, level=1),
                  line("interest", "الفوائد", P["interest"] if "الفوائد" not in P["net_profit_missing"] else None,
                       g(PP, "interest"), g(PY, "interest"), inverse=True, level=1),
                  line("net_profit", "صافي الربح", P["net_profit"], g(PP, "net_profit"), g(PY, "net_profit"))]

    # ── تحليل الانحراف: مساهمة كل بند في تغيّر صافي الربح (أثر موجب = يرفع الربح)
    variance = None
    if PP and P["net_profit"] is not None and PP["net_profit"] is not None:
        contrib = [{"key": "net_revenue", "label": "صافي الإيراد", "effect": _m(P["net_revenue"] - PP["net_revenue"])},
                   {"key": "cogs", "label": "تكلفة المبيعات", "effect": _m(-(P["cogs"] - PP["cogs"])) if P["cogs"] is not None and PP["cogs"] is not None else None}]
        for k in cats:
            a, b = P["opex"].get(k, D0), PP["opex"].get(k, D0)
            if a or b:
                contrib.append({"key": k, "label": LABEL[k], "effect": _m(-(a - b))})
        for k in ("tax", "depreciation", "interest"):
            if P[k] or PP[k]:
                contrib.append({"key": k, "label": LABEL[k], "effect": _m(-(P[k] - PP[k]))})
        contrib = [c for c in contrib if c["effect"] is not None]
        contrib.sort(key=lambda c: -abs(c["effect"]))
        top = contrib[0] if contrib else None
        variance = {"net_profit_change": _m(P["net_profit"] - PP["net_profit"]), "contributions": contrib,
                    "margin_change_pp": round(P["net_margin"] - PP["net_margin"], 1) if P["net_margin"] is not None and PP["net_margin"] is not None else None,
                    "gross_margin_change_pp": round(P["gross_margin"] - PP["gross_margin"], 1) if P["gross_margin"] is not None and PP["gross_margin"] is not None else None,
                    "main_cause_ar": f"أكبر أثر على صافي الربح: {top['label']} ({top['effect']:+,.0f})" if top else None,
                    "budget_available": bool(b_rev or b_opex)}

    # ── الإيرادات
    def rev_by(key):
        a, b = {}, {}
        for r in rows:
            k = r.get(key) or "غير محدد"
            if r["_ym"] == cur:
                a[k] = a.get(k, D0) + r["_net"]
            elif r["_ym"] == prev:
                b[k] = b.get(k, D0) + r["_net"]
        tot = sum(a.values(), D0)
        out = []
        for k, v in sorted(a.items(), key=lambda x: -x[1]):
            cr = [r for r in rows if r["_ym"] == cur and (r.get(key) or "غير محدد") == k]
            cc = [r for r in cr if r["_cogs"] is not None]
            out.append({"key": k, "revenue": _m(v), "share_pct": _pct(v, tot), "growth_pct": _chg(v, b.get(k)),
                        "gross_margin": _pct(sum((r["_net"] for r in cc), D0) - sum((r["_cogs"] for r in cc), D0), sum((r["_net"] for r in cc), D0)) if cc else None})
        return out
    revenue = {"total": _m(P["net_revenue"]), "growth_pct": _chg(P["net_revenue"], g(PP, "net_revenue")),
               "yoy_pct": _chg(P["net_revenue"], g(PY, "net_revenue")), "by_category": rev_by("category"),
               "by_product": rev_by("product_sku")[:15], "by_branch": rev_by("branch_name"), "by_channel": rev_by("channel")}

    # ── المصروفات
    hr_now, hr_prev = hr_payroll(cur), hr_payroll(prev)
    exp_rows = []
    for k in cats:
        a = P["opex"].get(k) if P["opex_total"] is not None else None
        b = PP["opex"].get(k) if PP and PP["opex_total"] is not None else None
        if a is None and b is None:
            continue
        exp_rows.append({"key": k, "label": LABEL[k], "current": _m(a or D0), "previous": _m(b) if b is not None else None,
                         "change_pct": _chg(a or D0, b), "pct_of_revenue": _pct(a or D0, P["net_revenue"]),
                         "budget": _m(b_opex.get(k)), "var_budget_pct": _chg(a or D0, b_opex.get(k)),
                         "top_items": sorted([{"desc": x["desc"], "amount": _m(x["amount"]), "branch": x["branch"], "source": x["source"]}
                                              for x in expense_lines if x["ym"] == cur and x["cat"] == k], key=lambda x: -x["amount"])[:8]})
    exp_rows.sort(key=lambda x: -x["current"])
    rec_payroll = P["opex"].get("payroll")
    expenses = {"available": P["opex_total"] is not None, "total": _m(P["opex_total"]), "change_pct": _chg(P["opex_total"], g(PP, "opex_total")),
                "pct_of_revenue": P["opex_ratio"], "lines": exp_rows, "sources": P["expense_sources"],
                "basis_ar": ("من ملف المصروفات المحاسبي" if P["expense_sources"] == ["expense"] else
                             "أساس نقدي: من الحركات النقدية الخارجة (دون دفعات الموردين لأنها ضمن تكلفة المبيعات)" if P["expense_sources"] == ["cash"] else
                             "مختلط: ملف المصروفات لأشهر والحركات النقدية لأخرى") if P["expense_sources"] else None,
                "drivers": sorted([x for x in exp_rows if x["change_pct"] is not None], key=lambda x: -(x["current"] - (x["previous"] or 0)))[:5],
                "payroll_reconciliation": {"recorded": _m(rec_payroll), "hr_estimate": _m(hr_now),
                                           "gap_pct": _chg(hr_now, rec_payroll) if rec_payroll and hr_now is not None else None,
                                           "note_ar": "الرواتب في قائمة الدخل من المصروفات المسجّلة؛ تقدير ملف الموظفين للمطابقة فقط"} if hr_now is not None else None}

    # ── الميزانية العمومية
    bs = st.get("balance") or {}
    bv = lambda k: _d(bs.get(k))
    cash_bal = _d(cash.get("balance"))
    ytd = [m for m in months if m[:4] == cur[:4] and m <= cur]
    P_ytd = pnl(set(ytd))
    assets = [("cash", "النقد", cash_bal, "2.8 التدفق النقدي"), ("receivables", "الذمم المدينة", _d(receivables_total), "2.8 الذمم"),
              ("inventory", "المخزون", _d(inventory_value), "2.6 المخزون"), ("fixed_assets", "الأصول الثابتة", bv("fixed_assets"), "إدخال يدوي"),
              ("other_assets", "أصول أخرى", bv("other_assets"), "إدخال يدوي")]
    liabs = [("payables", "الذمم الدائنة", bv("accounts_payable"), "إدخال يدوي"), ("loans", "القروض", bv("loans"), "إدخال يدوي"),
             ("accrued", "مستحقات", bv("accrued"), "إدخال يدوي"), ("other_liabilities", "التزامات أخرى", bv("other_liabilities"), "إدخال يدوي")]
    equity = [("capital", "رأس المال المدفوع", bv("paid_in_capital"), "إدخال يدوي"), ("retained", "الأرباح المبقاة", bv("retained_earnings"), "إدخال يدوي"),
              ("current_profit", "ربح الفترة (منذ بداية السنة)", P_ytd["net_profit"] if P_ytd else None, "محسوب 2.11")]
    sm = lambda xs: sum((x[2] for x in xs if x[2] is not None), D0)
    ta, tl, te = sm(assets), sm(liabs), sm(equity)
    miss = [x[1] for x in assets + liabs + equity if x[2] is None]
    balance_sheet = {"assets": [{"key": k, "label": l, "value": _m(v), "source": s} for k, l, v, s in assets],
                     "liabilities": [{"key": k, "label": l, "value": _m(v), "source": s} for k, l, v, s in liabs],
                     "equity": [{"key": k, "label": l, "value": _m(v), "source": s} for k, l, v, s in equity],
                     "total_assets": _m(ta), "total_liabilities": _m(tl), "total_equity": _m(te),
                     "difference": _m(ta - tl - te), "balanced": abs(ta - tl - te) < 1 and not miss, "missing": miss,
                     "note_ar": "الميزانية تجمع أرصدة الوحدات + الإدخالات اليدوية — الفرق يعني بنوداً ناقصة أو غير متطابقة، ولا يُخفى"}

    # ── النسب
    ann = lambda v: v * 12 if v is not None else None
    cl = sum((x[2] for x in liabs if x[0] in ("payables", "accrued", "other_liabilities") and x[2] is not None), D0) or None
    inv_avg = (_d(inventory_value) + _d(inventory_value_prev)) / 2 if inventory_value is not None and inventory_value_prev is not None else _d(inventory_value)

    def ratio(group, code, ar, val, unit, method, needs):
        return {"group": group, "code": code, "name_ar": ar, "value": None if val is None else round(float(val), 2), "unit": unit,
                "method_ar": method, "available": val is not None, "needs_ar": None if val is not None else needs}
    eq_total = te if bv("paid_in_capital") is not None or bv("retained_earnings") is not None else None
    ratios = [ratio("profitability", "gross_margin", "هامش مجمل الربح", P["gross_margin"], "%", "مجمل الربح ÷ صافي الإيراد", "يحتاج تكلفة المنتجات"),
              ratio("profitability", "ebitda_margin", "هامش EBITDA", P["ebitda_margin"], "%", "EBITDA ÷ صافي الإيراد", "يحتاج المصروفات"),
              ratio("profitability", "net_margin", "هامش صافي الربح", P["net_margin"], "%", "صافي الربح ÷ صافي الإيراد", "يحتاج المصروفات"),
              ratio("profitability", "roa", "العائد على الأصول (سنوي)", _pct(ann(P["net_profit"]), ta) if ta and P["net_profit"] is not None and not miss else None, "%",
                    "صافي الربح الشهري × 12 ÷ إجمالي الأصول", "يحتاج الميزانية العمومية كاملة"),
              ratio("profitability", "roe", "العائد على حقوق الملكية (سنوي)", _pct(ann(P["net_profit"]), eq_total) if eq_total and P["net_profit"] is not None else None, "%",
                    "صافي الربح الشهري × 12 ÷ حقوق الملكية", "يحتاج رأس المال والأرباح المبقاة"),
              ratio("liquidity", "current_ratio", "النسبة المتداولة", safe_divide((cash_bal or D0) + (_d(receivables_total) or D0) + (_d(inventory_value) or D0), cl) if cl and cash_bal is not None else None, "x",
                    "(النقد + الذمم + المخزون) ÷ الالتزامات المتداولة", "يحتاج الذمم الدائنة/المستحقات (إدخال يدوي) والنقد"),
              ratio("liquidity", "quick_ratio", "النسبة السريعة", safe_divide((cash_bal or D0) + (_d(receivables_total) or D0), cl) if cl and cash_bal is not None and receivables_total is not None else None, "x",
                    "(النقد + الذمم) ÷ الالتزامات المتداولة", "يحتاج الذمم المدينة والالتزامات المتداولة"),
              ratio("liquidity", "cash_ratio", "نسبة النقد", safe_divide(cash_bal, cl) if cl and cash_bal is not None else None, "x", "النقد ÷ الالتزامات المتداولة", "يحتاج الالتزامات المتداولة"),
              ratio("efficiency", "inventory_turnover", "دوران المخزون (سنوي)", safe_divide(ann(P["cogs"]), inv_avg) if P["cogs"] is not None and inv_avg else None, "x",
                    "تكلفة المبيعات × 12 ÷ متوسط المخزون", "يحتاج المخزون وتكلفة المبيعات"),
              ratio("efficiency", "dio", "أيام المخزون", safe_divide(inv_avg * 30, P["cogs"]) if P["cogs"] and inv_avg else None, "days", "متوسط المخزون ÷ تكلفة المبيعات الشهرية × 30", "يحتاج المخزون"),
              ratio("efficiency", "dso", "أيام التحصيل", safe_divide(_d(receivables_total) * 30, P["net_revenue"]) if receivables_total is not None and P["net_revenue"] else None, "days",
                    "الذمم المدينة ÷ الإيراد الشهري × 30", "يحتاج ملف الذمم"),
              ratio("efficiency", "dpo", "أيام السداد", safe_divide(bv("accounts_payable") * 30, P["cogs"]) if bv("accounts_payable") is not None and P["cogs"] else None, "days",
                    "الذمم الدائنة ÷ تكلفة المبيعات الشهرية × 30", "يحتاج الذمم الدائنة (إدخال يدوي)"),
              ratio("efficiency", "asset_turnover", "دوران الأصول (سنوي)", safe_divide(ann(P["net_revenue"]), ta) if ta and not miss else None, "x",
                    "الإيراد × 12 ÷ إجمالي الأصول", "يحتاج الميزانية العمومية كاملة"),
              ratio("leverage", "debt_ratio", "نسبة الديون", _pct(tl, ta) if ta and not miss else None, "%", "إجمالي الالتزامات ÷ إجمالي الأصول", "يحتاج الميزانية العمومية كاملة"),
              ratio("leverage", "debt_to_equity", "الديون إلى حقوق الملكية", safe_divide(bv("loans"), eq_total) if bv("loans") is not None and eq_total else None, "x",
                    "القروض ÷ حقوق الملكية", "يحتاج القروض وحقوق الملكية"),
              ratio("leverage", "interest_coverage", "تغطية الفوائد", safe_divide(P["ebitda"], P["interest"]) if P["ebitda"] is not None and P["interest"] else None, "x",
                    "EBITDA ÷ الفوائد", "يحتاج الفوائد (إدخال يدوي أو مصروفات)")]
    for r_ in ratios:
        if r_["code"] in ("dio", "dso", "dpo") and r_["value"] is not None:
            r_["value"] = round(r_["value"], 0)

    # ── الاتجاهات والتوقع
    series = []
    for b in sorted({_bucket(m, grain) for m in months})[-24:]:
        p = pnl({m for m in months if _bucket(m, grain) == b})
        series.append({"period": b, "revenue": _m(p["net_revenue"]), "gross_profit": _m(p["gross_profit"]), "ebitda": _m(p["ebitda"]),
                       "net_profit": _m(p["net_profit"]), "opex": _m(p["opex_total"]), "gross_margin": p["gross_margin"], "net_margin": p["net_margin"],
                       "net_cash": (cash.get("net_by_month") or {}).get(b) if grain == "month" else None})
    full = [m for m in months if m < cur] + [cur]
    hist = [pnl({m}) for m in full[-6:]]
    forecast = {"available": False}
    if len(hist) >= 3:
        revs = [h["net_revenue"] for h in hist]
        growths = [float((revs[i] - revs[i - 1]) / revs[i - 1]) for i in range(1, len(revs)) if revs[i - 1]]
        gr = max(-0.10, min(0.10, median(growths))) if growths else 0.0
        base_rev = sum(revs[-3:], D0) / len(revs[-3:])
        gms = [h["gross_margin"] for h in hist[-3:] if h["gross_margin"] is not None]
        opx = [h["opex_total"] for h in hist[-3:] if h["opex_total"] is not None]
        gm = sum(gms) / len(gms) if gms else None
        ox = Decimal(str(median([float(x) for x in opx]))) if opx else None
        below_m = sum((h["tax"] + h["depreciation"] + h["interest"] for h in hist[-3:]), D0) / len(hist[-3:])
        pts, rv = [], base_rev
        for k in range(1, R["forecast_months"] + 1):
            rv = rv * Decimal(str(1 + gr))
            gp_ = rv * Decimal(str(gm)) / 100 if gm is not None else None
            eb = gp_ - ox if gp_ is not None and ox is not None else None
            pts.append({"period": _ym_add(cur, k), "revenue": _m(rv), "gross_profit": _m(gp_), "ebitda": _m(eb),
                        "net_profit": _m(eb - below_m) if eb is not None else None,
                        "cash": (cash.get("forecast") or {}).get(_ym_add(cur, k))})
        n = len(hist)
        forecast = {"available": True, "points": pts, "confidence": "medium" if n >= 6 else "low",
                    "sufficiency": f"{n} أشهر من البيانات" + ("" if n >= 12 else " — الموسمية غير ممثلة (تحتاج 12 شهراً)"),
                    "method_ar": f"الإيراد: متوسط آخر 3 أشهر × نمو شهري {gr * 100:+.1f}% (وسيط آخر الأشهر، محدود بـ ±10%) · الهامش: متوسط آخر 3 أشهر · المصروفات: وسيط آخر 3 أشهر",
                    "assumptions": {"growth_pct": round(gr * 100, 2), "gross_margin": gm, "opex_monthly": _m(ox)}, "is_estimate": True}
    else:
        forecast["reason_ar"] = f"فترات تاريخية غير كافية — يلزم 3 أشهر على الأقل (المتوفر {len(hist)})"

    # ── الفروع: مساهمة الفرع = مجمل الربح − مصروفاته المباشرة (لا توزيع تقديري للمصروفات المركزية)
    branches = []
    for b in sorted({r.get("branch_name") for r in rows if r.get("branch_name")}):
        pb, pbp = pnl({cur}, b), pnl({prev}, b)
        if not pb:
            continue
        direct = sum((x["amount"] for x in expense_lines if x["ym"] == cur and x.get("branch") == b and x["cat"] not in ("tax", "depreciation", "interest")), D0)
        contrib = pb["gross_profit"] - direct if pb["gross_profit"] is not None else None
        ctx = (branch_context or {}).get(b, {})
        branches.append({"key": b, "revenue": _m(pb["net_revenue"]), "revenue_growth_pct": _chg(pb["net_revenue"], g(pbp, "net_revenue")),
                         "cogs": _m(pb["cogs"]), "gross_profit": _m(pb["gross_profit"]), "gross_margin": pb["gross_margin"],
                         "gross_margin_prev": g(pbp, "gross_margin"), "direct_opex": _m(direct), "contribution": _m(contrib),
                         "contribution_margin": _pct(contrib, pb["net_revenue"]),
                         "opex_lines": sorted([{"label": LABEL.get(x["cat"], x["cat"]), "amount": _m(x["amount"])} for x in expense_lines
                                               if x["ym"] == cur and x.get("branch") == b], key=lambda x: -x["amount"])[:8],
                         "hr_payroll_estimate": _m(hr_payroll(cur, b)), **ctx})
    central = sum((x["amount"] for x in expense_lines if x["ym"] == cur and not x.get("branch") and x["cat"] not in ("tax", "depreciation", "interest")), D0)

    # ── التدفق النقدي ماليًا
    ncur = (cash.get("net_by_month") or {}).get(cur)
    cash_analysis = {"available": bool(cash), "balance": _m(cash_bal), "net_cash": ncur, "net_profit": _m(P["net_profit"]),
                     "cash_conversion_pct": _pct(_d(ncur), P["net_profit"]) if ncur is not None and P["net_profit"] else None,
                     "runway_months": cash.get("runway"), "inflow": cash.get("inflow"), "outflow": cash.get("outflow"),
                     "gap": _m(_d(ncur) - P["net_profit"]) if ncur is not None and P["net_profit"] is not None else None,
                     "note_ar": "الفرق بين الربح والنقد يأتي من: المخزون والذمم وتوقيت دفعات الموردين والإهلاك — الربح ليس نقداً"}

    summary = {"revenue": {"gross_sales": _m(P["gross_sales"]), "discounts": _m(P["discounts"]), "returns": _m(P["returns"]),
                           "net_sales": _m(P["net_revenue"]), "growth_pct": revenue["growth_pct"]},
               "profitability": {"cogs": _m(P["cogs"]), "gross_profit": _m(P["gross_profit"]), "gross_margin": P["gross_margin"],
                                 "opex": _m(P["opex_total"]), "ebitda": _m(P["ebitda"]), "net_profit": _m(P["net_profit"]), "net_margin": P["net_margin"]},
               "cash": {"balance": _m(cash_bal), "inflow": cash.get("inflow"), "outflow": cash.get("outflow"), "net": ncur},
               "working_capital": {"receivables": _m(_d(receivables_total)), "payables": _m(bv("accounts_payable")), "inventory": _m(_d(inventory_value)),
                                   "working_capital": _m((_d(receivables_total) or D0) + (_d(inventory_value) or D0) - bv("accounts_payable"))
                                   if bv("accounts_payable") is not None and (receivables_total is not None or inventory_value is not None) else None}}

    def kp(key, ar, cur_v, prev_v, yoy_v, unit="money", target=None):
        is_pct = unit == "pct"
        return {"key": key, "name_ar": ar, "unit": unit, "current": (cur_v if is_pct else _m(cur_v)), "previous": (prev_v if is_pct else _m(prev_v)),
                "yoy": (yoy_v if is_pct else _m(yoy_v)), "target": _m(target),
                "change": (round(cur_v - prev_v, 1) if is_pct else _chg(cur_v, prev_v)) if cur_v is not None and prev_v is not None else None,
                "change_unit": "pp" if is_pct else "%", "vs_target_pct": _chg(cur_v, target) if target else None, "available": cur_v is not None}
    kpis = [kp("revenue", "الإيراد", P["net_revenue"], g(PP, "net_revenue"), g(PY, "net_revenue"), target=b_rev or _d(st.get("revenue_target"))),
            kp("gross_profit", "مجمل الربح", P["gross_profit"], g(PP, "gross_profit"), g(PY, "gross_profit")),
            kp("gross_margin", "هامش مجمل الربح", P["gross_margin"], g(PP, "gross_margin"), g(PY, "gross_margin"), "pct"),
            kp("ebitda", "EBITDA", P["ebitda"], g(PP, "ebitda"), g(PY, "ebitda")),
            kp("net_profit", "صافي الربح", P["net_profit"], g(PP, "net_profit"), g(PY, "net_profit")),
            kp("net_margin", "هامش صافي الربح", P["net_margin"], g(PP, "net_margin"), g(PY, "net_margin"), "pct"),
            kp("opex", "المصروفات التشغيلية", P["opex_total"], g(PP, "opex_total"), g(PY, "opex_total")),
            {"key": "cash", "name_ar": "المركز النقدي", "unit": "money", "current": _m(cash_bal), "available": cash_bal is not None,
             "change": None, "previous": None}]
    quality = {"cogs_coverage_pct": P["cogs_coverage_pct"], "cogs_method_ar": "الكمية المباعة × تكلفة الصنف (البيانات الأساسية/المخزون)"
               + ("؛ الإيراد غير المكلَّف قُدّر بنسبة تكلفة الأصناف المكلَّفة" if P["cogs_coverage_pct"] is not None and P["cogs_coverage_pct"] < 100 else ""),
               "expense_basis_ar": expenses["basis_ar"], "net_profit_missing": P["net_profit_missing"],
               "balance_sheet_missing": miss}
    signals = _signals(R, cur, P, PP, variance, expenses, balance_sheet, cash_analysis, branches, forecast, quality, revenue, b_rev, currency)
    sev = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda x: ({"risk": 0, "opportunity": 1, "data_quality": 2}[x["type"]], sev.get(x["severity"], 3)))
    risks = [x for x in signals if x["type"] == "risk"]
    return {"has_data": True, "version": f"fin-v{FIN_VERSION}", "period": cur, "previous_period": prev, "periods": months, "grain": grain,
            "currency": currency, "rules": R, "kpis": kpis, "summary": summary, "statement": statement, "variance": variance,
            "balance_sheet": balance_sheet, "ratios": ratios, "revenue": revenue, "expenses": expenses, "cash_analysis": cash_analysis,
            "trends": series, "forecast": forecast, "branches": branches, "central_opex": _m(central),
            "whatif_base": {"revenue": _m(P["net_revenue"]), "cogs": _m(P["cogs"]), "payroll": _m(P["opex"].get("payroll")),
                            "marketing": _m(P["opex"].get("marketing")), "other_opex": _m((P["opex_total"] or D0) - P["opex"].get("payroll", D0) - P["opex"].get("marketing", D0)) if P["opex_total"] is not None else None,
                            "below": _m(P["tax"] + P["depreciation"] + P["interest"])},
            "quality": quality, "signals": signals,
            "status": {"status": "critical" if any(x["severity"] == "high" for x in risks) else ("attention" if risks else "stable"),
                       "risks": len(risks), "opportunities": sum(1 for x in signals if x["type"] == "opportunity"),
                       "attention": sum(1 for x in signals if x["type"] == "data_quality"),
                       "rule_ar": "حرجة: إشارة خطر عالية · تحتاج انتباه: أي إشارة خطر · مستقرة: لا مخاطر"}}


def _signals(R, period, P, PP, var, exp, bs, ca, branches, fc, q, rev, b_rev, cur_):
    out = []

    def add(kind, code, ar, sev, dim, evidence, impact, action, metric):
        out.append({"id": _sid(code, dim, period), "type": kind, "code": code, "source_module": "finance", "name_ar": ar, "severity": sev,
                    "dimension": dim, "period": period, "evidence": [e for e in evidence if e], "impact_ar": impact,
                    "suggested_action_ar": action, "metric_id": metric, "estimated_impact": None, "method": "rule-based (fin-v1.0)"})
    if var and var["margin_change_pp"] is not None and var["margin_change_pp"] <= -R["margin_drop_pp"] and PP:
        ev = [f"هامش صافي الربح {PP['net_margin']}% ← {P['net_margin']}%"]
        if P["cogs"] is not None and PP["cogs"] is not None:
            ev.append(f"تكلفة المبيعات {_chg(P['cogs'], PP['cogs']):+}%")
        ev += [f"{x['label']} {x['change_pct']:+}%" for x in exp["drivers"][:2] if x["change_pct"] and x["change_pct"] > 0]
        ev.append(f"الإيراد {_chg(P['net_revenue'], PP['net_revenue']):+}%" if _chg(P['net_revenue'], PP['net_revenue']) is not None else None)
        add("risk", "margin_decline", "تراجع الربحية", "high" if var["margin_change_pp"] <= -3 else "medium", None, ev,
            f"ضغط على الهامش بمقدار {abs(var['margin_change_pp'])} نقطة مئوية", "راجع تكلفة الأصناف وأكبر بنود المصروفات المرتفعة", "net_margin")
    if rev["growth_pct"] is not None and rev["growth_pct"] > 0 and var and var["gross_margin_change_pp"] is not None and var["gross_margin_change_pp"] <= -R["margin_drop_pp"]:
        add("risk", "growth_without_margin", "الإيراد ينمو لكن الهامش يتراجع", "medium", None,
            [f"الإيراد {rev['growth_pct']:+}%", f"هامش مجمل الربح {var['gross_margin_change_pp']:+} نقطة"], "النمو يأتي بتكلفة أعلى",
            "حلل هامش الفئات والقنوات الأسرع نمواً", "gross_margin")
    for x in exp["lines"]:
        if x["change_pct"] is not None and x["change_pct"] >= R["expense_spike_pct"] and x["current"] >= 0.02 * (float(P["net_revenue"]) or 1):
            add("risk", "expense_spike", f"ارتفاع {x['label']}", "medium", x["label"],
                [f"{x['previous']:,.0f} ← {x['current']:,.0f} ({x['change_pct']:+}%)", f"{x['pct_of_revenue']}% من الإيراد"],
                f"خفض الربح بنحو {x['current'] - (x['previous'] or 0):,.0f} {cur_}", "راجع بنود هذا المصروف وضرورتها", "opex")
        if x["var_budget_pct"] is not None and x["var_budget_pct"] >= R["budget_over_pct"]:
            add("risk", "budget_overrun", f"تجاوز موازنة {x['label']}", "medium", x["label"], [f"الفعلي {x['current']:,.0f} مقابل الموازنة {x['budget']:,.0f} ({x['var_budget_pct']:+}%)"],
                "تجاوز الموازنة يقلل الربح المخطط", "اعتمد سقفاً شهرياً أو عدّل الموازنة", "opex")
    for b in branches:
        if b["contribution"] is not None and b["contribution"] < 0:
            add("risk", "branch_negative_contribution", "فرع بمساهمة سالبة", "high", b["key"],
                [f"مجمل الربح {b['gross_profit']:,.0f} − مصروفاته المباشرة {b['direct_opex']:,.0f} = {b['contribution']:,.0f}",
                 f"هامش مجمل الربح {b['gross_margin']}%" if b["gross_margin"] is not None else None],
                "الفرع لا يغطي مصروفاته المباشرة قبل المصروفات المركزية", "افتح تحليل الفرع: المخزون والعمليات والمشتريات", "contribution")
        elif b["gross_margin"] is not None and b["gross_margin_prev"] is not None and b["gross_margin"] - b["gross_margin_prev"] <= -R["margin_drop_pp"] * 2:
            add("risk", "branch_margin_drop", "تراجع هامش فرع", "medium", b["key"], [f"هامش مجمل الربح {b['gross_margin_prev']}% ← {b['gross_margin']}%"],
                "تراجع ربحية الفرع", "راجع الخصومات والتكلفة في الفرع", "gross_margin")
    if P["net_profit"] is not None and P["net_profit"] > 0 and ca.get("net_cash") is not None and ca["net_cash"] < 0:
        add("risk", "profit_not_cash", "ربح بلا نقد", "medium", None, [f"صافي الربح {float(P['net_profit']):,.0f} مقابل صافي تدفق نقدي {ca['net_cash']:,.0f}"],
            "الشركة رابحة دفترياً لكن السيولة تتناقص", "راجع المخزون والذمم وتوقيت دفعات الموردين (ذكاء التدفق النقدي)", "cash_conversion")
    if fc.get("available") and any(p["net_profit"] is not None and p["net_profit"] < 0 for p in fc["points"]):
        m_ = next(p for p in fc["points"] if p["net_profit"] is not None and p["net_profit"] < 0)
        add("risk", "forecast_loss", "خسارة متوقعة", "medium", m_["period"], [f"صافي ربح متوقع {m_['net_profit']:,.0f} في {m_['period']}", fc["method_ar"]],
            "تقدير وليس حقيقة — يعتمد على استمرار الاتجاه", "خطط لخفض المصروفات أو رفع الهامش قبل الشهر المحدد", "net_profit")
    if b_rev and P["net_revenue"] is not None and P["net_revenue"] < b_rev * Decimal("0.95"):
        add("risk", "revenue_below_budget", "الإيراد دون الموازنة", "medium", None, [f"{float(P['net_revenue']):,.0f} مقابل {float(b_rev):,.0f} ({_chg(P['net_revenue'], b_rev):+}%)"],
            "فجوة إيراد عن الخطة", "راجع خطة المبيعات حسب الفرع والفئة", "revenue")
    if rev["by_category"] and rev["by_category"][0]["gross_margin"] is not None:
        best = max((c for c in rev["by_category"] if c["gross_margin"] is not None and (c["share_pct"] or 0) >= 5), key=lambda c: c["gross_margin"], default=None)
        if best:
            add("opportunity", "high_margin_category", "فئة عالية الهامش", "low", best["key"], [f"هامش مجمل الربح {best['gross_margin']}% · حصة {best['share_pct']}%"],
                "توسيعها يرفع الهامش الإجمالي", "ركّز الحملات والعرض على هذه الفئة", "gross_margin")
    if q["cogs_coverage_pct"] is not None and q["cogs_coverage_pct"] < R["cost_coverage_min"]:
        add("data_quality", "cogs_coverage", "تكلفة الأصناف ناقصة", "low", None, [f"الإيراد المكلَّف {q['cogs_coverage_pct']}% فقط", q["cogs_method_ar"]],
            "دقة مجمل الربح محدودة", "أضف «التكلفة» لكل منتج في ملف المنتجات", "gross_margin")
    if P["cogs"] is None:
        add("data_quality", "no_cogs", "تكلفة المبيعات غير متاحة", "low", None, ["لا توجد تكلفة لأي صنف مباع"], "مجمل الربح والهوامش غير متاحة",
            "ارفع ملف المنتجات بعمود «التكلفة»", "gross_margin")
    if P["net_profit_missing"] and P["ebitda"] is not None:
        add("data_quality", "net_profit_incomplete", "صافي الربح غير مكتمل", "low", None, [f"لم يُدخل: {'، '.join(P['net_profit_missing'])}"],
            "صافي الربح المعروض قبل هذه البنود", "أدخل الإهلاك والفوائد الشهرية من الإعدادات", "net_profit")
    pr = exp.get("payroll_reconciliation")
    if pr and pr["gap_pct"] is not None and abs(pr["gap_pct"]) >= R["payroll_gap_pct"]:
        add("data_quality", "payroll_mismatch", "الرواتب المسجّلة لا تطابق ملف الموظفين", "medium", None,
            [f"المسجّل {pr['recorded']:,.0f} مقابل تقدير ملف الموظفين {pr['hr_estimate']:,.0f} ({pr['gap_pct']:+}%)"],
            "قد تكون الرواتب في قائمة الدخل أقل أو أكثر من الواقع", "طابق كشف الرواتب مع ملف الموظفين", "payroll")
    if not bs["balanced"]:
        add("data_quality", "balance_sheet_gap", "الميزانية العمومية غير متوازنة", "low", None,
            [f"الفرق {bs['difference']:,.0f}"] + ([f"بنود ناقصة: {'، '.join(bs['missing'][:5])}"] if bs["missing"] else []),
            "النسب المعتمدة على الميزانية محدودة", "أكمل بنود الميزانية من الإعدادات", "balance_sheet")
    return out
