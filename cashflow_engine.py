"""
NABBAH 2.8 — Cash Flow Intelligence engine (pure, deterministic; AI only explains).
Cash comes ONLY from cash movements (sales are not cash, purchases are not payments).
Missing ≠ zero · no branch allocation by estimate · forecasts show method, confidence and data sufficiency.
"""
import os, sys, hashlib, calendar
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

_here = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21", "../phase22", "../phase23", "../phase24", "../phase25", "../phase26", "../phase27"):
    _p = os.path.join(_here, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from nabbah_finance import to_decimal, round_money, round_pct, safe_divide

CASHFLOW_VERSION = "1.0"
D0 = Decimal("0")
RULES = {"pressure_days": 14, "runway_warn_months": 6, "runway_critical_months": 3, "dso_rise_days": 5,
         "overdue_share_pct": 20, "driver_spike_pct": 20, "concentration_pct": 60, "forecast_months": 12,
         "history_months": 6}
# تصنيف الحركات: كلمات → محرّك (قاعدة منشورة، والباقي «أخرى» لا يُخمَّن)
DRIVERS = {
    "in": [("collections", "تحصيلات العملاء", ("تحصيل", "عميل", "العملاء", "مبيعات", "ايداع مبيعات", "مدى", "نقاط البيع", "pos",
                                             "collection", "customer", "sales", "receipt", "فاتورة")),
           ("financing", "تمويل وقروض", ("قرض", "تمويل", "loan", "financing", "رأس مال", "capital")),
           ("other_in", "تدفقات داخلة أخرى", ())],
    "out": [("suppliers", "مدفوعات الموردين", ("مورد", "موردين", "مشتريات", "supplier", "vendor", "purchase", "أمر شراء")),
            ("payroll", "الرواتب", ("راتب", "رواتب", "أجور", "salary", "salaries", "payroll", "wps")),
            ("rent", "الإيجار", ("ايجار", "إيجار", "rent", "lease")),
            ("expenses", "المصروفات التشغيلية", ("كهرباء", "مياه", "اتصالات", "صيانة", "مصروف", "مصاريف", "تسويق", "رسوم",
                                                 "utilities", "expense", "maintenance", "marketing", "fees", "internet")),
            ("tax", "الضرائب والزكاة", ("ضريبة", "زكاة", "vat", "zakat", "tax", "gosi", "تأمينات")),
            ("financing_out", "سداد تمويل", ("سداد قرض", "قسط", "installment", "loan repayment")),
            ("other_out", "تدفقات خارجة أخرى", ())],
}
LABELS = {k: l for side in DRIVERS.values() for k, l, _ in side}


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
    return str(s or "").strip().lower().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")


def classify(m):
    """المحرّك من نوع الحركة/التصنيف/الطرف. قاعدة كلمات منشورة؛ لا مطابقة = «أخرى»."""
    text = _norm(" ".join(str(m.get(k) or "") for k in ("category", "movement_type", "counterparty", "source")))
    side = "in" if m.get("direction") == "in" else "out"
    for key, _, words in DRIVERS[side]:
        if words and any(_norm(w) in text for w in words):
            return key
    return "other_in" if side == "in" else "other_out"


def _sid(*p):
    return "cash-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def _month_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _month_end(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def analyze_cashflow(movements, *, receivables=None, period=None, settings=None, sales_rows=None,
                     inventory_value=None, inventory_value_prev=None, purchases_change_pct=None, open_po_value=None,
                     currency="SAR", rules=None):
    """movements: [{date, direction in|out, amount, movement_type, category, counterparty, account, branch_name, balance}]
       receivables: [{invoice_date, due_date, amount, paid_amount, paid_date, customer_name, reference, branch_name}]
       settings: {opening_balance, current_liabilities, min_cash, restricted_cash}"""
    R = dict(RULES, **(rules or {}))
    settings, receivables, sales_rows = settings or {}, receivables or [], sales_rows or []
    mv = [m for m in movements if _date(m.get("date")) and _d(m.get("amount")) is not None and m.get("direction") in ("in", "out")]
    if not mv and not receivables:
        return {"has_data": False, "version": f"cashflow-v{CASHFLOW_VERSION}",
                "message_ar": "لا توجد حركات نقدية بعد. ارفع كشف الحساب البنكي أو ملف الحركات النقدية من مركز البيانات.",
                "required": {"required": ["التاريخ", "المبلغ واتجاهه (أو عمودا مدين/دائن)"],
                             "recommended": ["الوصف/التصنيف", "الحساب", "الرصيد بعد الحركة", "الفرع", "الطرف المقابل"],
                             "optional": ["ملف الذمم المدينة (للتحصيل وDSO)", "الالتزامات المتداولة (للنسب)", "الحد الأدنى للنقد"]}}
    for m in mv:
        m["_amt"], m["_d"], m["_driver"] = _d(m["amount"]), _date(m["date"]), classify(m)
        m["_ym"] = m["_d"].strftime("%Y-%m")
    months = sorted({m["_ym"] for m in mv}) or [date.today().strftime("%Y-%m")]
    cur = period if period in months else months[-1]
    prev = _month_add(cur, -1)
    yoy = _month_add(cur, -12)
    as_of = min(_month_end(cur), max((m["_d"] for m in mv), default=_month_end(cur)))

    def sums(ym, filt=lambda m: True):
        i = sum((m["_amt"] for m in mv if m["_ym"] == ym and m["direction"] == "in" and filt(m)), D0)
        o = sum((m["_amt"] for m in mv if m["_ym"] == ym and m["direction"] == "out" and filt(m)), D0)
        has = any(m["_ym"] == ym and filt(m) for m in mv)
        return (i, o, i - o) if has else (None, None, None)

    ci, co, cn = sums(cur)
    pi, po, pn = sums(prev)
    yi, yo, yn = sums(yoy)

    # ── الرصيد: آخر رصيد مُبلَّغ لكل حساب، وإلا الافتتاحي + صافي الحركات؛ وإلا غير متاح
    accounts = {}
    for m in sorted(mv, key=lambda x: x["_d"]):
        a = m.get("account") or "—"
        e = accounts.setdefault(a, {"account": a, "reported": None, "reported_date": None, "net": D0, "net_after": D0})
        e["net"] += m["_amt"] if m["direction"] == "in" else -m["_amt"]
        if m["_d"] <= as_of and _d(m.get("balance")) is not None:
            e["reported"], e["reported_date"], e["net_after"] = _d(m["balance"]), m["_d"], D0
        elif e["reported"] is not None and m["_d"] <= as_of:
            e["net_after"] += m["_amt"] if m["direction"] == "in" else -m["_amt"]
    opening = _d(settings.get("opening_balance"))
    bal_src, balance = None, None
    if any(e["reported"] is not None for e in accounts.values()):
        balance = sum(((e["reported"] + e["net_after"]) if e["reported"] is not None else D0 for e in accounts.values()), D0)
        bal_src = "reported"
    elif opening is not None:
        balance = opening + sum((m["_amt"] if m["direction"] == "in" else -m["_amt"] for m in mv if m["_d"] <= as_of), D0)
        bal_src = "opening_plus_net"
    restricted = _d(settings.get("restricted_cash"))
    by_account = [{"account": e["account"], "balance": _money(e["reported"] + e["net_after"]) if e["reported"] is not None else None,
                   "as_of": e["reported_date"].isoformat() if e["reported_date"] else None} for e in accounts.values()]

    def bal_at(ym):
        """الرصيد في نهاية شهر سابق = الرصيد الحالي − صافي ما بعده (يعتمد على وجود رصيد حالي)."""
        if balance is None:
            return None
        after = sum((m["_amt"] if m["direction"] == "in" else -m["_amt"] for m in mv if m["_ym"] > ym and m["_d"] <= as_of), D0)
        return balance - after
    bal_prev, bal_yoy = bal_at(prev), bal_at(yoy)

    # ── الحركة الشهرية + المحرّكات
    hist = [months[i] for i in range(len(months)) if months[i] <= cur][-24:]
    movement = [{"period": ym, "inflow": _money(sums(ym)[0]), "outflow": _money(sums(ym)[1]), "net": _money(sums(ym)[2]),
                 "balance_end": _money(bal_at(ym))} for ym in hist]

    def drivers_for(ym):
        out = {}
        for m in mv:
            if m["_ym"] == ym:
                out[m["_driver"]] = out.get(m["_driver"], D0) + m["_amt"]
        return out
    dc, dp = drivers_for(cur), drivers_for(prev)
    drivers = []
    for side in ("in", "out"):
        for key, label, _ in DRIVERS[side]:
            if key in dc or key in dp:
                c_, p_ = dc.get(key), dp.get(key)
                drivers.append({"key": key, "label": label, "direction": side, "amount": _money(c_ or D0),
                                "previous": _money(p_) if p_ is not None else None,
                                "delta": _money((c_ or D0) - (p_ or D0)) if p_ is not None else None,
                                "change_pct": _pct((c_ or D0) - p_, p_) if p_ else None,
                                "share_pct": _pct(c_ or D0, ci if side == "in" else co) if (ci if side == "in" else co) else None,
                                "top_items": [{"date": m["_d"].isoformat(), "description": m.get("movement_type") or m.get("category") or "—",
                                               "counterparty": m.get("counterparty"), "amount": _money(m["_amt"]), "branch": m.get("branch_name")}
                                              for m in sorted([x for x in mv if x["_ym"] == cur and x["_driver"] == key],
                                                              key=lambda x: -x["_amt"])[:10]]})
    rule_ar = "التصنيف بكلمات الوصف/التصنيف/الطرف (قاعدة منشورة)؛ ما لا يطابق يُعرض «أخرى» ولا يُخمَّن"

    # ── الانحراف: الحالي مقابل السابق — السبب الرئيسي = أكبر تغيّر مطلق في محرّك
    variance = None
    if cn is not None and pn is not None:
        contrib = []
        for d in drivers:
            if d["delta"] is None:
                continue
            eff = d["delta"]["value"] if d["direction"] == "in" else -d["delta"]["value"]
            contrib.append({"key": d["key"], "label": d["label"], "effect_on_net": round(eff, 2), "direction": d["direction"]})
        contrib.sort(key=lambda x: -abs(x["effect_on_net"]))
        main = contrib[0] if contrib else None
        variance = {"net_current": _money(cn), "net_previous": _money(pn), "delta": _money(cn - pn), "contributions": contrib,
                    "main_cause_ar": (f"سبب الانحراف الرئيسي: {main['label']} "
                                      f"({'+' if main['effect_on_net'] > 0 else ''}{main['effect_on_net']:,.0f} على صافي التدفق)") if main else None,
                    "budget": {"available": False, "reason_ar": "المقارنة بالميزانية غير متاحة — لا توجد بيانات ميزانية"}}

    # ── التوقع 12 شهراً: وسيط آخر N أشهر كاملة للداخل والخارج (طريقة منشورة) + كفاية البيانات
    full = [ym for ym in hist if ym < cur] or hist
    base = full[-R["history_months"]:]
    forecast = {"available": False}
    if len(base) >= 3:
        ins = [sums(ym)[0] or D0 for ym in base]
        outs = [sums(ym)[1] or D0 for ym in base]
        mi, mo = Decimal(str(median(ins))), Decimal(str(median(outs)))
        nets = [a - b for a, b in zip(ins, outs)]
        mean_abs = sum((abs(x) for x in nets), D0) / len(nets)
        spread = (max(nets) - min(nets)) / mean_abs if mean_abs else Decimal(0)
        conf = "high" if len(base) >= 6 and spread < 1 else ("medium" if len(base) >= 4 else "low")
        pts, run, min_pt, press = [], balance, None, []
        min_cash = _d(settings.get("min_cash"))
        for k in range(1, R["forecast_months"] + 1):
            ym = _month_add(cur, k)
            if run is not None:
                run = run + mi - mo
                if min_pt is None or run < min_pt["balance"]:
                    min_pt = {"period": ym, "balance": run}
                if run < 0 or (min_cash is not None and run < min_cash):
                    press.append(ym)
            pts.append({"period": ym, "inflow": _money(mi), "outflow": _money(mo), "net": _money(mi - mo),
                        "balance": _money(run)})
        forecast = {"available": True, "method_ar": f"وسيط التدفقات الداخلة والخارجة لآخر {len(base)} أشهر كاملة، يُسقَط شهرياً",
                    "basis_months": base, "confidence": conf, "sufficiency": "جيدة" if len(base) >= 6 else "محدودة",
                    "points": pts, "expected_net_monthly": _money(mi - mo),
                    "min_cash_point": {"period": min_pt["period"], "balance": _money(min_pt["balance"])} if min_pt else None,
                    "pressure_months": press, "trend": "up" if mi > mo else ("down" if mi < mo else "flat"),
                    "balance_note_ar": None if balance is not None else "لا يوجد رصيد حالي — يُعرض التدفق المتوقع دون الرصيد المتوقع",
                    "is_estimate": True}
    else:
        forecast["reason_ar"] = f"يلزم 3 أشهر كاملة على الأقل من الحركات (المتوفر: {len(base)})"

    # ── الأفق القريب (14 يوماً): متوسط يومي آخر 90 يوماً + الذمم المستحقة + أوامر الشراء المفتوحة
    start90 = as_of - timedelta(days=89)
    last90 = [m for m in mv if start90 <= m["_d"] <= as_of]
    days = max(1, (as_of - max(start90, min(m["_d"] for m in mv))).days + 1) if mv else 1
    avg_in = sum((m["_amt"] for m in last90 if m["direction"] == "in"), D0) / days
    avg_out = sum((m["_amt"] for m in last90 if m["direction"] == "out"), D0) / days
    H_ = R["pressure_days"]
    horizon = as_of + timedelta(days=H_)

    # ── الذمم والتحصيل
    ar_rows = []
    for r in receivables:
        amt, paid = _d(r.get("amount")), _d(r.get("paid_amount")) or D0
        inv, due, pdte = _date(r.get("invoice_date")), _date(r.get("due_date")), _date(r.get("paid_date"))
        if amt is None or not inv or inv > as_of:
            continue
        paid_by = paid if (pdte is None or pdte <= as_of) else D0
        ar_rows.append({**r, "_amt": amt, "_open": max(amt - paid_by, D0), "_inv": inv, "_due": due or inv, "_paid_date": pdte})
    collections = None
    if ar_rows:
        open_rows = [r for r in ar_rows if r["_open"] > 0]
        total_ar = sum((r["_open"] for r in open_rows), D0)
        overdue = [r for r in open_rows if r["_due"] < as_of]
        aging = []
        for lo, hi, lab in ((None, 0, "غير مستحقة"), (1, 30, "1–30"), (31, 60, "31–60"), (61, 90, "61–90"), (91, 10 ** 6, "+90")):
            rs = [r for r in open_rows if ((as_of - r["_due"]).days <= 0 if lo is None else lo <= (as_of - r["_due"]).days <= hi)]
            aging.append({"bucket": lab, "amount": _money(sum((r["_open"] for r in rs), D0)), "count": len(rs)})

        def dso_at(end):
            """DSO = الذمم القائمة في نهاية الفترة ÷ فواتير آخر 90 يوماً × 90."""
            b90 = end - timedelta(days=89)
            open_at = sum((max(r["_amt"] - ((_d(r.get("paid_amount")) or D0) if r["_paid_date"] and r["_paid_date"] <= end else D0), D0)
                           for r in ar_rows if r["_inv"] <= end), D0)
            billed = sum((r["_amt"] for r in ar_rows if b90 <= r["_inv"] <= end), D0)
            return safe_divide(open_at * 90, billed)
        dso_now, dso_prev = dso_at(as_of), dso_at(_month_end(prev))
        coll_cur = sum(((_d(r.get("paid_amount")) or D0) for r in ar_rows if r["_paid_date"] and r["_paid_date"].strftime("%Y-%m") == cur), D0)
        by_cust = {}
        for r in overdue:
            k = r.get("customer_name") or "—"
            by_cust[k] = by_cust.get(k, D0) + r["_open"]
        due_soon = sum((r["_open"] for r in open_rows if as_of <= r["_due"] <= horizon), D0)
        collections = {"total_receivables": _money(total_ar), "overdue": _money(sum((r["_open"] for r in overdue), D0)),
                       "overdue_share_pct": _pct(sum((r["_open"] for r in overdue), D0), total_ar) if total_ar else None,
                       "open_invoices": len(open_rows), "dso": _num(dso_now, 0), "dso_previous": _num(dso_prev, 0),
                       "dso_change": _num(dso_now - dso_prev, 0) if dso_now is not None and dso_prev is not None else None,
                       "dso_method_ar": "الذمم القائمة ÷ فواتير آخر 90 يوماً × 90",
                       "collected_this_period": _money(coll_cur), "aging": aging, "due_next_days": _money(due_soon),
                       "top_overdue_customers": [{"customer": k, "amount": _money(v)} for k, v in sorted(by_cust.items(), key=lambda x: -x[1])[:10]]}
    else:
        due_soon = None

    exp_in = avg_in * H_ + (due_soon or D0)
    opv = _d(open_po_value)
    exp_out = avg_out * H_ + (opv or D0)
    short_term = {"days": H_, "expected_inflow": _money(exp_in), "expected_outflow": _money(exp_out),
                  "net": _money(exp_in - exp_out), "projected_balance": _money(balance + exp_in - exp_out) if balance is not None else None,
                  "basis_ar": f"متوسط يومي لآخر 90 يوماً × {H_}"
                              + (" + ذمم مستحقة خلال الفترة" if due_soon else "") + (" + أوامر شراء مفتوحة" if opv else ""),
                  "is_estimate": True}

    # ── السيولة والمؤشرات
    cl, inv_v = _d(settings.get("current_liabilities")), _d(inventory_value)
    ar_total = _d(collections["total_receivables"]["value"]) if collections else None
    months_net = [sums(ym)[2] for ym in base] if base else []
    avg_net = (sum(months_net, D0) / len(months_net)) if months_net else None
    burn = -avg_net if avg_net is not None and avg_net < 0 else None
    runway = safe_divide(balance, burn) if balance is not None and burn else None
    sales_cur = sum(((_d(r.get("net_sales")) if r.get("net_sales") is not None else _d(r.get("gross_sales"))) or D0
                     for r in sales_rows if str(r.get("date", ""))[:7] == cur), D0) if sales_rows else None
    coll_driver = dc.get("collections")

    def ind(code, ar, value, unit, method, needs=None):
        return {"code": code, "name_ar": ar, "value": value, "unit": unit, "period": cur, "method_ar": method,
                "available": value is not None, "needs_ar": None if value is not None else needs}
    liquidity = [
        ind("current_ratio", "النسبة المتداولة",
            _num(safe_divide((balance or D0) + (ar_total or D0) + (inv_v or D0), cl)) if cl and balance is not None else None, "x",
            "(النقد + الذمم + المخزون) ÷ الالتزامات المتداولة", "يحتاج الالتزامات المتداولة (إدخال يدوي) والرصيد النقدي"),
        ind("quick_ratio", "النسبة السريعة",
            _num(safe_divide((balance or D0) + (ar_total or D0), cl)) if cl and balance is not None else None, "x",
            "(النقد + الذمم) ÷ الالتزامات المتداولة", "يحتاج الالتزامات المتداولة (إدخال يدوي) والرصيد النقدي"),
        ind("runway", "مدة السيولة (Runway)", _num(runway, 1) if runway is not None else None, "months",
            "الرصيد ÷ متوسط صافي الاستنزاف الشهري",
            "لا استنزاف — صافي التدفق موجب" if avg_net is not None and avg_net >= 0 else "يحتاج الرصيد و3 أشهر حركة"),
        ind("cash_burn", "معدل الاستنزاف الشهري", _num(burn) if burn is not None else None, "currency",
            "متوسط صافي التدفق السالب للأشهر الأساسية", "لا استنزاف — صافي التدفق موجب" if avg_net is not None else "يحتاج 3 أشهر حركة"),
        ind("net_cash_flow", "صافي التدفق", _num(cn), "currency", "الداخل − الخارج للفترة", "لا حركات في الفترة"),
        ind("cash_conversion", "تحويل المبيعات إلى نقد", _pct(coll_driver, sales_cur) if coll_driver and sales_cur else None, "percent",
            "تحصيلات العملاء ÷ صافي المبيعات للفترة", "يحتاج بيانات المبيعات وحركات تحصيل مصنّفة"),
    ]

    # ── حسب الفرع (فقط الحركات المسندة لفرع — لا توزيع تقديري)
    branches = []
    for b in sorted({m.get("branch_name") for m in mv if m.get("branch_name")}):
        i, o, n = sums(cur, lambda m, b=b: m.get("branch_name") == b)
        pi_, po_, pn_ = sums(prev, lambda m, b=b: m.get("branch_name") == b)
        bd = {}
        for m in mv:
            if m["_ym"] == cur and m.get("branch_name") == b:
                bd[m["_driver"]] = bd.get(m["_driver"], D0) + m["_amt"]
        branches.append({"branch": b, "inflow": _money(i), "outflow": _money(o), "net": _money(n), "net_previous": _money(pn_),
                         "net_change": _money(n - pn_) if n is not None and pn_ is not None else None,
                         "drivers": [{"key": k, "label": LABELS[k], "amount": _money(v)} for k, v in sorted(bd.items(), key=lambda x: -x[1])],
                         "trend": [{"period": ym, "net": _money(sums(ym, lambda m, b=b: m.get("branch_name") == b)[2])} for ym in hist[-12:]]})
    unassigned = sums(cur, lambda m: not m.get("branch_name"))

    # ── الربط بين الوحدات
    s_prev = sum(((_d(r.get("net_sales")) if r.get("net_sales") is not None else _d(r.get("gross_sales"))) or D0
                  for r in sales_rows if str(r.get("date", ""))[:7] == prev), D0) if sales_rows else None
    cross = {"sales_change_pct": _pct(sales_cur - s_prev, s_prev) if sales_rows and s_prev else None,
             "inventory_change_pct": _pct(_d(inventory_value) - _d(inventory_value_prev), _d(inventory_value_prev))
             if inventory_value is not None and inventory_value_prev else None,
             "purchases_change_pct": purchases_change_pct, "net_cash": _money(cn),
             "net_cash_change": _money(cn - pn) if cn is not None and pn is not None else None,
             "note_ar": "المبيعات ليست نقداً والمشتريات ليست دفعاً — تُعرض الاتجاهات متزامنة ولا تُثبت السبب"}

    signals = _signals(R, cur, currency, short_term, balance, settings, forecast, liquidity, collections, drivers, branches, mv)
    sev = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda x: ({"risk": 0, "opportunity": 1, "data_quality": 2}[x["type"]], sev.get(x["severity"], 3),
                                -((x.get("estimated_impact") or {}).get("value") or 0)))
    risks = [x for x in signals if x["type"] == "risk"]
    status = "critical" if any(x["severity"] == "high" for x in risks) else ("attention" if risks else "stable")
    biggest_in = max((m for m in mv if m["_ym"] == cur and m["direction"] == "in"), key=lambda m: m["_amt"], default=None)
    biggest_out = max((m for m in mv if m["_ym"] == cur and m["direction"] == "out"), key=lambda m: m["_amt"], default=None)
    tag = lambda m: None if not m else {"description": m.get("movement_type") or m.get("category") or "—", "amount": _money(m["_amt"]),
                                        "date": m["_d"].isoformat(), "counterparty": m.get("counterparty"), "branch": m.get("branch_name")}
    unclassified = sum(1 for m in mv if m["_driver"] in ("other_in", "other_out"))
    dq = {"movements": len(mv), "unclassified_pct": _pct(Decimal(unclassified), Decimal(len(mv))) if mv else None,
          "unassigned_branch_pct": _pct(Decimal(sum(1 for m in mv if not m.get("branch_name"))), Decimal(len(mv))) if mv else None,
          "balance_source": bal_src}
    return {"has_data": True, "version": f"cashflow-v{CASHFLOW_VERSION}", "period": cur, "previous_period": prev,
            "periods": months, "as_of": as_of.isoformat(), "currency": currency, "rules": R, "driver_rule_ar": rule_ar,
            "position": {"balance": _money(balance), "balance_source": bal_src,
                         "balance_reason_ar": None if balance is not None else "الرصيد غير متاح — أضف عمود «الرصيد» في الكشف أو أدخل الرصيد الافتتاحي",
                         "available_cash": _money(balance - restricted) if balance is not None and restricted is not None else None,
                         "restricted_cash": _money(restricted), "previous": _money(bal_prev), "yoy": _money(bal_yoy),
                         "change": _money(balance - bal_prev) if balance is not None and bal_prev is not None else None,
                         "change_pct": _pct(balance - bal_prev, bal_prev) if balance is not None and bal_prev else None,
                         "by_account": by_account},
            "flows": {"inflow": _money(ci), "outflow": _money(co), "net": _money(cn),
                      "previous": {"inflow": _money(pi), "outflow": _money(po), "net": _money(pn)},
                      "inflow_change_pct": _pct(ci - pi, pi) if ci is not None and pi else None,
                      "outflow_change_pct": _pct(co - po, po) if co is not None and po else None,
                      "yoy_net": _money(yn)},
            "status": {"status": status, "risks": len(risks), "opportunities": sum(1 for x in signals if x["type"] == "opportunity"),
                       "attention": sum(1 for x in signals if x["type"] == "data_quality"),
                       "rule_ar": "حرجة: إشارة خطر عالية · تحتاج انتباه: أي إشارة خطر · مستقرة: لا مخاطر"},
            "highlights": {"biggest_inflow": tag(biggest_in), "biggest_outflow": tag(biggest_out)},
            "movement": movement, "drivers": drivers, "variance": variance, "forecast": forecast, "short_term": short_term,
            "liquidity": liquidity, "collections": collections, "branches": branches,
            "unassigned": {"net": _money(unassigned[2]), "inflow": _money(unassigned[0]), "outflow": _money(unassigned[1])},
            "cross": cross, "signals": signals, "data_quality": dq}


def _signals(R, period, currency, st, balance, settings, fc, liq, coll, drivers, branches, mv):
    out = []

    def add(kind, code, ar, sev, dim, evidence, action, impact=None, cause=None, metric="net_cash_flow"):
        out.append({"id": _sid(code, dim, period), "type": kind, "code": code, "source_module": "cashflow", "name_ar": ar,
                    "severity": sev, "dimension": dim, "period": period, "evidence": evidence, "cause_ar": cause,
                    "suggested_action_ar": action, "estimated_impact": impact, "metric_id": metric,
                    "method": "rule-based (cashflow-v1.0)"})
    min_cash = _d(settings.get("min_cash"))
    pb = st.get("projected_balance")
    if pb is not None and (pb["value"] < 0 or (min_cash is not None and pb["value"] < float(min_cash))):
        add("risk", "cash_pressure", f"ضغط نقدي متوقع خلال {st['days']} يوماً", "high", None,
            [f"خروج متوقع {st['expected_outflow']['value']:,.0f} مقابل دخول متوقع {st['expected_inflow']['value']:,.0f}",
             f"الرصيد المتوقع {pb['value']:,.0f} {currency}" + (f" (الحد الأدنى {float(min_cash):,.0f})" if min_cash is not None else "")],
            "راجع توقيت المدفوعات الكبيرة وسرّع تحصيل المستحقات", impact=st["net"], cause=st["basis_ar"])
    if fc.get("available") and fc.get("pressure_months"):
        add("risk", "forecast_gap", "فجوة نقدية في التوقع", "high" if fc["pressure_months"][0] <= fc["points"][2]["period"] else "medium",
            fc["pressure_months"][0], [f"الرصيد المتوقع ينخفض دون الحد في {fc['pressure_months'][0]}",
                                      f"أدنى نقطة: {fc['min_cash_point']['period']} ({fc['min_cash_point']['balance']['value']:,.0f})"],
            "خطّط للتمويل أو خفض المصروفات قبل الشهر المحدد", cause=fc["method_ar"])
    rw = next(x for x in liq if x["code"] == "runway")
    if rw["value"] is not None and rw["value"] < R["runway_warn_months"]:
        add("risk", "short_runway", "مدة سيولة قصيرة", "high" if rw["value"] < R["runway_critical_months"] else "medium", None,
            [f"السيولة تكفي {rw['value']} شهراً بمعدل الاستنزاف الحالي"], "خفّض الاستنزاف الشهري أو وفّر تمويلاً", metric="runway")
    if coll:
        if coll["dso_change"] is not None and coll["dso_change"] >= R["dso_rise_days"]:
            add("risk", "dso_rising", "تباطؤ التحصيل", "medium", None,
                [f"DSO ارتفع {coll['dso_change']:.0f} يوماً إلى {coll['dso']:.0f}"], "راجع الحسابات المتأخرة وتواصل مع العملاء",
                metric="dso")
        if coll["dso_change"] is not None and coll["dso_change"] <= -R["dso_rise_days"]:
            add("opportunity", "dso_improving", "تحسّن التحصيل", "low", None,
                [f"DSO انخفض {abs(coll['dso_change']):.0f} يوماً إلى {coll['dso']:.0f}"], "ثبّت الممارسات التي حسّنت التحصيل", metric="dso")
        if coll["overdue_share_pct"] and coll["overdue_share_pct"] >= R["overdue_share_pct"]:
            add("opportunity", "collect_overdue", "تحصيل الذمم المتأخرة", "medium", None,
                [f"متأخرات {coll['overdue']['value']:,.0f} ({coll['overdue_share_pct']}% من الذمم)"]
                + [f"{c['customer']}: {c['amount']['value']:,.0f}" for c in coll["top_overdue_customers"][:3]],
                "ابدأ بأكبر العملاء المتأخرين", impact=coll["overdue"], metric="dso")
    outs = [d for d in drivers if d["direction"] == "out"]
    tot_out = sum((d["amount"]["value"] for d in outs), 0)
    for d in outs:
        if d["change_pct"] is not None and d["change_pct"] >= R["driver_spike_pct"] and d["key"] not in ("other_out",):
            add("risk", "outflow_spike", f"ارتفاع {d['label']}", "medium", d["label"],
                [f"{d['label']}: {d['amount']['value']:,.0f} مقابل {d['previous']['value']:,.0f} ({d['change_pct']:+}%)"],
                "راجع البنود التي ارتفعت ومدى ضرورتها", impact=d["delta"])
        if tot_out and d["amount"]["value"] / tot_out * 100 >= R["concentration_pct"] and d["key"] not in ("other_out",):
            add("risk", "outflow_concentration", "اعتماد كبير على بند خارج واحد", "low", d["label"],
                [f"{d['label']} يمثل {d['amount']['value'] / tot_out * 100:.0f}% من التدفقات الخارجة"], "راجع شروط الدفع لهذا البند")
    sup = next((d for d in outs if d["key"] == "suppliers"), None)
    if sup and sup["amount"]["value"] > 0:
        add("opportunity", "supplier_terms", "فرصة تحسين شروط الدفع للموردين", "low", None,
            [f"مدفوعات الموردين {sup['amount']['value']:,.0f} في الفترة"],
            "فاوض على مدة سداد أطول مع أكبر الموردين (انظر ذكاء المشتريات)", metric="supplier_payments")
    for b in branches:
        if b["net"] and b["net"]["value"] < 0:
            add("risk", "branch_negative", "صافي تدفق سالب لفرع", "medium", b["branch"],
                [f"صافي التدفق {b['net']['value']:,.0f}" + (f" (السابق {b['net_previous']['value']:,.0f})" if b["net_previous"] else "")],
                "راجع مصروفات الفرع وتحصيلاته")
    if balance is None:
        add("data_quality", "no_balance", "الرصيد النقدي غير متاح", "low", None,
            ["لا يوجد رصيد في الكشف ولا رصيد افتتاحي"], "أضف عمود «الرصيد» أو أدخل الرصيد الافتتاحي من الإعدادات")
    unc = sum(1 for m in mv if m["_driver"] in ("other_in", "other_out"))
    if mv and unc / len(mv) >= 0.2:
        add("data_quality", "unclassified", "حركات غير مصنّفة", "low", None,
            [f"{unc / len(mv) * 100:.0f}% من الحركات لا تطابق أي محرّك"], "أضف عمود «التصنيف» (تحصيل، مورد، رواتب، إيجار…)")
    if not coll:
        add("data_quality", "no_receivables", "بيانات الذمم غير متاحة", "low", None,
            ["DSO وتقادم الذمم يحتاجان ملف الذمم المدينة"], "ارفع ملف الذمم (الفاتورة، العميل، الاستحقاق، المحصّل)")
    return out
