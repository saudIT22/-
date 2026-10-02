"""
NABBAH 3.2 — Tax & Zakat Compliance Intelligence engine (pure, deterministic; AI only explains).
Data compliance checks — NOT a legal/tax compliance certificate. Regulatory facts live in a versioned configuration
(rule, effective date, version, source, applicability) that can be updated without code changes.
Reconciliation differences are reported as data differences with possible data causes — never as evasion.
Zakat: readiness of inputs only; no flat-rate shortcut. Exposure: affected records + basis; penalty amounts only from
a configured rule with a source.
"""
import calendar, hashlib
from datetime import date

TAX_VERSION = "1.0"
# طبقة الإعدادات التنظيمية — كل قاعدة بمصدر وتاريخ سريان وإصدار. تُحدَّث من الإعدادات دون إعادة بناء.
REGULATORY_DEFAULTS = {
    "vat_standard_rate": {"value": 15.0, "effective_date": "2020-07-01", "version": "1", "applicability": "السلع والخدمات الخاضعة للنسبة الأساسية",
                          "source": "هيئة الزكاة والضريبة والجمارك — zatca.gov.sa", "unit": "%"},
    "vat_return_due": {"value": "end_of_following_month", "effective_date": "2018-01-01", "version": "1",
                       "applicability": "تقديم الإقرار وسداده: نهاية الشهر التالي لانتهاء الفترة الضريبية",
                       "source": "هيئة الزكاة والضريبة والجمارك — zatca.gov.sa (تحقّق من التحديثات)"},
    "einvoice_phase1": {"value": "2021-12-04", "effective_date": "2021-12-04", "version": "1", "applicability": "مرحلة الإصدار والحفظ",
                        "source": "zatca.gov.sa — الفوترة الإلكترونية"},
    "einvoice_phase2": {"value": "2023-01-01", "effective_date": "2023-01-01", "version": "1", "applicability": "مرحلة الربط والتكامل — على موجات حسب الاستهداف",
                        "source": "zatca.gov.sa — الفوترة الإلكترونية"},
    "required_invoice_fields": {"value": ["invoice_number", "issue_date", "seller_vat", "taxable_amount", "vat_amount", "total_amount", "currency"],
                                "effective_date": "2021-12-04", "version": "1", "applicability": "الحقول الأساسية التي يفحصها نبّاه — ليست كامل مواصفات XML",
                                "source": "مواصفات الفاتورة الإلكترونية — zatca.gov.sa"},
    "b2b_buyer_vat_required": {"value": True, "effective_date": "2021-12-04", "version": "1",
                               "applicability": "الفاتورة الضريبية (بين المنشآت) تتضمن بيانات المشتري الضريبية", "source": "zatca.gov.sa"},
    "penalties": {"value": [], "effective_date": None, "version": "0", "applicability": "لا قواعد غرامات مُعدّة — تُضاف بمصدرها من الإعدادات",
                  "source": None},
}
TOL = 1.0   # تسامح ريال واحد في المطابقة
TYPE_MAP = {"tax": ("ضريبية", "tax invoice", "standard", "b2b", "فاتورة ضريبية"), "simplified": ("مبسطة", "simplified", "b2c"),
            "credit": ("دائن", "credit", "اشعار دائن", "إشعار دائن"), "debit": ("مدين", "debit", "اشعار مدين", "إشعار مدين")}


def _n(s):
    return str(s or "").strip().lower()


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _r(v, p=2):
    return None if v is None else round(v, p)


def _pct(a, b, p=1):
    return None if not b else round(a / b * 100, p)


def _sid(*p):
    return "tax-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def regulatory_config(overrides=None):
    cfg = {k: dict(v) for k, v in REGULATORY_DEFAULTS.items()}
    for k, v in (overrides or {}).items():
        if k in cfg and isinstance(v, dict):
            cfg[k].update({kk: vv for kk, vv in v.items() if kk in ("value", "effective_date", "version", "source", "applicability")})
            cfg[k]["overridden"] = True
    return cfg


def _inv_type(t):
    t_ = _n(t)
    for k, words in TYPE_MAP.items():
        if any(w in t_ for w in words):
            return k
    return "unknown" if t_ else None


def _period_of(ym, freq):
    if freq == "quarterly":
        return f"{ym[:4]}-Q{(int(ym[5:7]) - 1) // 3 + 1}"
    return ym


def _period_end(p):
    if "-Q" in p:
        y, q = int(p[:4]), int(p[-1])
        m = q * 3
    else:
        y, m = int(p[:4]), int(p[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _due_date(p, rule):
    end = _period_end(p)
    if rule == "end_of_following_month":
        y, m = (end.year + 1, 1) if end.month == 12 else (end.year, end.month + 1)
        return date(y, m, calendar.monthrange(y, m)[1])
    return None


def analyze_tax(sales_rows, *, purchases=None, invoices=None, settings=None, balance_inputs=None, tax_payments=None,
                period=None, today=None, sector_tax=None, currency="SAR"):
    st = settings or {}
    reg = regulatory_config(st.get("regulatory_overrides"))
    rate = float(reg["vat_standard_rate"]["value"]) / 100
    today = today or date.today()
    purchases, invoices = purchases or [], invoices or []
    sales = [r for r in sales_rows if r.get("date")]
    if not sales and not invoices:
        return {"has_data": False, "version": f"tax-v{TAX_VERSION}",
                "message_ar": "لا توجد مبيعات أو فواتير ضريبية بعد.",
                "required": {"required": ["المبيعات (2.5) أو سجل الفواتير الضريبية"],
                             "recommended": ["عمود ضريبة القيمة المضافة في المبيعات والمشتريات", "سجل الفواتير الإلكترونية (تصدير نظام الفوترة)",
                                             "الرقم الضريبي للمنشأة ودورية الإقرار (الإعدادات)"],
                             "optional": ["بنود الميزانية (للزكاة)", "سجل الإقرارات المقدمة"]},
                "disclaimer_ar": "نبّاه يفحص البيانات ولا يمنح شهادة امتثال ضريبي أو قانوني."}
    freq = st.get("filing_frequency")            # monthly | quarterly | None (غير محدد)
    incl = st.get("prices_include_vat")          # True | False | None
    months = sorted({str(r["date"])[:7] for r in sales} | {str(i.get("issue_date") or "")[:7] for i in invoices if i.get("issue_date")})
    cur_m = period if period in months else months[-1]
    pfreq = freq or "monthly"
    cur = _period_of(cur_m, pfreq)
    in_cur = lambda d: d and _period_of(str(d)[:7], pfreq) == cur

    # ── 1) ضريبة المخرجات من المبيعات
    sc = [r for r in sales if in_cur(r["date"])]
    net = lambda r: _f(r.get("net_sales")) if _f(r.get("net_sales")) is not None else ((_f(r.get("gross_sales")) or 0) - (_f(r.get("discounts")) or 0) - (_f(r.get("returns")) or 0))
    sales_net = sum(net(r) for r in sc)
    with_vat = [r for r in sc if _f(r.get("vat")) is not None]
    recorded_out = sum(_f(r["vat"]) for r in with_vat) if with_vat else None
    vat_cov = _pct(len(with_vat), len(sc)) if sc else None
    if incl is True:
        taxable = sales_net / (1 + rate)
        expected_out = sales_net - taxable
        basis = f"المبيعات شاملة الضريبة: الضريبة = المبلغ × {rate:.2%} ÷ (1 + {rate:.0%})"
    elif incl is False:
        taxable, expected_out = sales_net, sales_net * rate
        basis = f"المبيعات قبل الضريبة: الضريبة = المبلغ × {rate:.0%}"
    else:
        taxable, expected_out, basis = None, None, "حدد في الإعدادات: هل مبالغ المبيعات شاملة الضريبة؟ — لا نفترض"
    # ── 2) ضريبة المدخلات من المشتريات
    pc = [p for p in purchases if in_cur(p.get("date"))]
    p_with = [p for p in pc if _f(p.get("vat")) is not None]
    input_vat = sum(_f(p["vat"]) for p in p_with) if p_with else None
    # ── 3) سجل الفواتير: التصنيف والفحص
    inv = [dict(i, _t=_inv_type(i.get("invoice_type"))) for i in invoices]
    ic = [i for i in inv if in_cur(i.get("issue_date"))]
    numbers = {}
    for i in inv:
        numbers.setdefault(str(i.get("invoice_number") or "").strip(), []).append(i)
    req = reg["required_invoice_fields"]["value"]
    seller_vat = st.get("vat_number")
    checked = []
    for i in ic:
        issues, sev = [], "valid"
        tx, va, to = _f(i.get("taxable_amount")), _f(i.get("vat_amount")), _f(i.get("total_amount"))
        for fld in req:
            if fld == "seller_vat":
                if not seller_vat:
                    issues.append(("warning", "الرقم الضريبي للبائع غير مُعدّ في الإعدادات"))
                continue
            if i.get(fld) in (None, ""):
                issues.append(("error" if fld in ("invoice_number", "issue_date", "vat_amount") else "warning", f"حقل ناقص: {fld}"))
        if len(numbers.get(str(i.get("invoice_number") or "").strip(), [])) > 1:
            issues.append(("duplicate", "رقم فاتورة مكرر"))
        if tx is not None and va is not None and to is not None and abs(tx + va - to) > TOL:
            issues.append(("error", f"الخاضع + الضريبة ≠ الإجمالي (فرق {tx + va - to:,.2f})"))
        if tx is not None and va is not None and i["_t"] in ("tax", "simplified") and tx > 0 and abs(va - tx * rate) > max(TOL, tx * 0.005):
            issues.append(("needs_review", f"الضريبة {va:,.2f} لا تساوي {rate:.0%} من الخاضع ({tx * rate:,.2f}) — قد تكون معفاة/صفرية"))
        if i["_t"] == "tax" and reg["b2b_buyer_vat_required"]["value"] and not i.get("buyer_vat"):
            issues.append(("warning", "فاتورة ضريبية بلا رقم ضريبي للمشتري"))
        if i["_t"] in ("credit", "debit"):
            if not i.get("original_invoice"):
                issues.append(("error", "إشعار بلا مرجع للفاتورة الأصلية"))
            elif str(i["original_invoice"]).strip() not in numbers:
                issues.append(("warning", "الفاتورة الأصلية غير موجودة في السجل"))
        if i["_t"] in (None, "unknown"):
            issues.append(("warning", "نوع الفاتورة غير محدد"))
        if i.get("currency") and _n(i["currency"]) not in ("sar", "ريال", "ر.س", "sr"):
            issues.append(("needs_review", f"عملة غير الريال: {i['currency']}"))
        order = ["duplicate", "error", "needs_review", "warning"]
        for o in order:
            if any(x[0] == o for x in issues):
                sev = o
                break
        checked.append({"invoice_number": i.get("invoice_number"), "issue_date": str(i.get("issue_date") or "")[:10], "type": i["_t"],
                        "branch": i.get("branch_name"), "buyer": i.get("buyer_name"), "taxable": tx, "vat": va, "total": to,
                        "zatca_status": i.get("zatca_status"), "status": sev, "issues": [x[1] for x in issues]})
    health = {k: sum(1 for c in checked if c["status"] == k) for k in ("valid", "warning", "needs_review", "error", "duplicate")}
    by_type = {k: sum(1 for i in ic if i["_t"] == k) for k in ("tax", "simplified", "credit", "debit", "unknown")}
    statuses = {}
    for i in ic:
        if i.get("zatca_status"):
            statuses[i["zatca_status"]] = statuses.get(i["zatca_status"], 0) + 1
    inv_vat = sum((_f(i.get("vat_amount")) or 0) * (-1 if i["_t"] == "credit" else 1) for i in ic) if ic else None

    # ── 4) المطابقة: المبيعات ↔ الفواتير ↔ الضريبة
    recon = {"available": bool(ic) and bool(sc)}
    if recon["available"]:
        s_by = {}
        for r in sc:
            k = str(r.get("reference") or "").strip()
            if k:
                e = s_by.setdefault(k, {"net": 0.0, "date": str(r["date"])[:10], "branch": r.get("branch_name"), "returns": 0.0})
                e["net"] += net(r)
                e["returns"] += _f(r.get("returns")) or 0
        i_by = {}
        for i in ic:
            if i["_t"] in ("credit", "debit"):
                continue
            i_by.setdefault(str(i.get("invoice_number") or "").strip(), []).append(i)
        mism, incl_hits, excl_hits = [], 0, 0
        for k, e in s_by.items():
            if k not in i_by:
                continue
            i = i_by[k][0]
            tx, to = _f(i.get("taxable_amount")), _f(i.get("total_amount"))
            m_incl = to is not None and abs(e["net"] - to) <= TOL
            m_excl = tx is not None and abs(e["net"] - tx) <= TOL
            incl_hits += m_incl
            excl_hits += m_excl
            if not (m_incl or m_excl):
                mism.append({"invoice_number": k, "sales_amount": _r(e["net"]), "invoice_total": to, "invoice_taxable": tx, "kind": "amount",
                             "difference": _r(e["net"] - (to if incl is not False and to is not None else (tx or 0)))})
            if str(i.get("issue_date") or "")[:10] != e["date"]:
                mism.append({"invoice_number": k, "kind": "date", "sales_date": e["date"], "invoice_date": str(i.get("issue_date") or "")[:10]})
            if i.get("branch_name") and e["branch"] and i["branch_name"] != e["branch"]:
                mism.append({"invoice_number": k, "kind": "branch", "sales_branch": e["branch"], "invoice_branch": i["branch_name"]})
        only_sales = [{"reference": k, "amount": _r(e["net"]), "date": e["date"], "branch": e["branch"]} for k, e in s_by.items() if k not in i_by]
        only_inv = [{"invoice_number": k, "total": _f(v[0].get("total_amount")), "date": str(v[0].get("issue_date") or "")[:10]} for k, v in i_by.items() if k not in s_by]
        inferred = None
        if incl_hits + excl_hits >= 5:
            inferred = ("شاملة الضريبة" if incl_hits >= excl_hits else "قبل الضريبة")
        inv_total = sum((_f(i.get("total_amount")) or 0) for i in ic if i["_t"] not in ("credit", "debit"))
        credits = sum((_f(i.get("total_amount")) or 0) for i in ic if i["_t"] == "credit")
        returns_ = sum(_f(r.get("returns")) or 0 for r in sc)
        unmatched_s = sum(x["amount"] for x in only_sales)
        unmatched_i = sum(x["total"] or 0 for x in only_inv)
        amount_diff = sum(x.get("difference") or 0 for x in mism if x["kind"] == "amount")
        diff = sales_net - (inv_total - credits)
        explained = unmatched_s - unmatched_i + amount_diff
        recon.update({"sales_total": _r(sales_net), "invoiced_total": _r(inv_total), "credit_notes": _r(credits), "difference": _r(diff),
                      "waterfall": [{"label": "مبيعات بلا فاتورة في السجل", "amount": _r(unmatched_s), "count": len(only_sales)},
                                    {"label": "فواتير بلا مبيعات مقابلة", "amount": _r(-unmatched_i), "count": len(only_inv)},
                                    {"label": "فروق مبالغ في فواتير مطابقة", "amount": _r(amount_diff), "count": sum(1 for x in mism if x["kind"] == "amount")},
                                    {"label": "إشعارات دائنة", "amount": _r(credits), "count": by_type["credit"]},
                                    {"label": "غير مُفسَّر", "amount": _r(diff - explained - credits), "count": None}],
                      "returns_in_sales": _r(returns_), "mismatches": mism[:200], "sales_without_invoice": only_sales[:200],
                      "invoices_without_sales": only_inv[:200], "match_rate_pct": _pct(len(s_by) - len(only_sales), len(s_by)),
                      "inferred_basis_ar": f"من المطابقة: مبالغ المبيعات تبدو {inferred}" if inferred else None,
                      "status": "متطابقة" if abs(diff) <= TOL and not mism else "تحتاج مراجعة",
                      "note_ar": "فرق مطابقة في البيانات — أسبابه المحتملة: سجلات ناقصة، فرق توقيت، تصنيف، مرتجعات/إشعارات، أو بيانات مصدر غير مكتملة. ليس حكماً ضريبياً."})
    vat = {"rate_pct": rate * 100, "rate_source": reg["vat_standard_rate"]["source"], "sales_net": _r(sales_net), "taxable_sales": _r(taxable),
           "output_vat_recorded": _r(recorded_out), "output_vat_coverage_pct": vat_cov, "output_vat_expected": _r(expected_out), "expected_basis_ar": basis,
           "output_vat_invoices": _r(inv_vat), "input_vat": _r(input_vat), "input_vat_coverage_pct": _pct(len(p_with), len(pc)) if pc else None,
           "net_position": _r((recorded_out if recorded_out is not None else (inv_vat if inv_vat is not None else expected_out)) - input_vat)
           if input_vat is not None and (recorded_out is not None or inv_vat is not None or expected_out is not None) else None,
           "net_basis_ar": "المخرجات (المسجلة، وإلا من سجل الفواتير، وإلا المتوقعة) − المدخلات المسجلة",
           "input_reason_ar": None if input_vat is not None else "ضريبة المدخلات غير متاحة — أضف عمود «الضريبة» في ملف المشتريات"}
    # حسب الفرع
    branches = []
    for b in sorted({r.get("branch_name") for r in sc if r.get("branch_name")}):
        rs = [r for r in sc if r.get("branch_name") == b]
        bn = sum(net(r) for r in rs)
        bv = [r for r in rs if _f(r.get("vat")) is not None]
        bi = [i for i in ic if i.get("branch_name") == b]
        exp_b = (bn - bn / (1 + rate)) if incl is True else (bn * rate if incl is False else None)
        iv_b = sum((_f(i.get("vat_amount")) or 0) * (-1 if i["_t"] == "credit" else 1) for i in bi) if bi else None
        cov = _pct(len(bv), len(rs))
        branches.append({"branch": b, "sales": _r(bn), "vat_recorded": _r(sum(_f(r["vat"]) for r in bv)) if bv else None, "vat_expected": _r(exp_b),
                         "vat_invoices": _r(iv_b), "invoices": len(bi), "invoice_errors": sum(1 for c in checked if c["branch"] == b and c["status"] in ("error", "duplicate")),
                         "data_status": "مكتملة" if (cov == 100 or bi) and not any(c["branch"] == b and c["status"] == "error" for c in checked) else ("جزئية" if bv or bi else "ناقصة")})

    # ── 5) التقويم والاستحقاقات
    filed = st.get("filed_periods") or {}
    cal = []
    if freq:
        for p_ in sorted({_period_of(m, freq) for m in months}):
            due = _due_date(p_, reg["vat_return_due"]["value"])
            f_ = filed.get(p_)
            days = (due - today).days if due else None
            # فترة قديمة غير مسجّلة لا تُعد «متأخرة»: قد تكون قُدمت قبل استخدام نبّاه — نقول «غير مسجّل» لا نجزم
            status = ("filed" if f_ else "unrecorded" if days is not None and days < -60 else
                      "overdue" if days is not None and days < 0 else "due_soon" if days is not None and days <= 15 else "upcoming")
            cal.append({"period": p_, "type": "إقرار ضريبة القيمة المضافة", "due_date": due.isoformat() if due else None, "days_remaining": days,
                        "status": status, "filed_on": (f_ or {}).get("date"), "responsible": st.get("responsible"), "preparation": (f_ or {}).get("note")})
    calendar_block = {"available": bool(freq), "items": cal[-12:], "rule": reg["vat_return_due"],
                      "reason_ar": None if freq else "حدد دورية الإقرار (شهري/ربع سنوي) في الإعدادات — لا نفترض مواعيد"}

    # ── 6) الزكاة: جاهزية البيانات فقط
    bi_ = balance_inputs or {}
    zk = [("cash", "النقد", "أصول"), ("receivables", "الذمم المدينة", "أصول"), ("inventory", "المخزون", "أصول"), ("fixed_assets", "الأصول الثابتة", "أصول"),
          ("other_assets", "أصول أخرى", "أصول"), ("payables", "الذمم الدائنة", "التزامات"), ("accrued", "المستحقات", "التزامات"), ("loans", "التمويل/القروض", "التزامات"),
          ("other_liabilities", "التزامات أخرى", "التزامات"), ("paid_in_capital", "رأس المال", "حقوق ملكية"), ("retained_earnings", "الأرباح المبقاة", "حقوق ملكية"),
          ("net_profit_ytd", "صافي الربح منذ بداية السنة", "النتائج")]
    zitems = [{"key": k, "label": l, "group": g, "value": bi_.get(k), "available": bi_.get(k) is not None, "source": (bi_.get("_sources") or {}).get(k)} for k, l, g in zk]
    zakat = {"readiness_pct": _pct(sum(1 for z in zitems if z["available"]), len(zitems), 0), "items": zitems,
             "missing": [z["label"] for z in zitems if not z["available"]],
             "calculation": {"enabled": False, "reason_ar": "لا يوجد نموذج حساب زكاة معتمد مُعدّ — نبّاه لا يستخدم «صافي الربح × 2.5%» كقاعدة عامة. الحساب يعتمد على الوعاء والقواعد والحالة."},
             "note_ar": "تجهيز بيانات الوعاء لمراجعتها مع المختص أو لنموذج حساب معتمد يُضاف لاحقاً"}

    # ── 7) قائمة التحقق وحالة الالتزام بالبيانات
    def pc_(n_, d_):
        return None if not d_ else round(n_ / d_ * 100)
    chk = [{"item": "الرقم الضريبي للمنشأة", "status": "complete" if seller_vat else "missing", "detail": None if seller_vat else "أدخله من الإعدادات"},
           {"item": "رقم الفاتورة", "status": "complete" if ic and all(c["invoice_number"] for c in checked) else ("missing" if not ic else "partial"), "detail": None},
           {"item": "تاريخ الفاتورة", "status": "complete" if ic and all(c["issue_date"] for c in checked) else ("missing" if not ic else "partial"), "detail": None},
           {"item": "مبلغ الضريبة", "status": "missing" if not ic and recorded_out is None else ("complete" if (ic and all(c["vat"] is not None for c in checked)) or vat_cov == 100 else "partial"),
            "detail": (f"{100 - pc_(sum(1 for c in checked if c['vat'] is not None), len(checked))}% من الفواتير ناقص" if ic and not all(c["vat"] is not None for c in checked) else None)},
           {"item": "الرقم الضريبي للمشتري (فواتير المنشآت)", "status": "missing" if not by_type["tax"] else ("complete" if all(i.get("buyer_vat") for i in ic if i["_t"] == "tax") else "partial"),
            "detail": f"{sum(1 for i in ic if i['_t'] == 'tax' and not i.get('buyer_vat'))} فاتورة ضريبية بلا رقم المشتري" if by_type["tax"] else "لا فواتير ضريبية بين منشآت"},
           {"item": "الإشعارات الدائنة", "status": "complete" if by_type["credit"] else "missing", "detail": None if by_type["credit"] else "لا توجد بيانات"},
           {"item": "الإشعارات المدينة", "status": "complete" if by_type["debit"] else "missing", "detail": None if by_type["debit"] else "لا توجد بيانات"},
           {"item": "ضريبة المدخلات (المشتريات)", "status": "complete" if input_vat is not None and vat["input_vat_coverage_pct"] == 100 else ("partial" if input_vat is not None else "missing"), "detail": vat["input_reason_ar"]},
           {"item": "دورية الإقرار", "status": "complete" if freq else "missing", "detail": None if freq else "حددها من الإعدادات"}]
    integration = {"status": "not_connected", "label_ar": "غير متصل", "environment": None, "last_sync": None, "failed": None, "pending": None,
                   "note_ar": "نبّاه لا يملك تكاملاً فعلياً مع منصة فاتورة حالياً — تُعرض حالات الفواتير كما وردت في سجلك فقط، ولا يُدّعى اعتماد."}

    # ── 8) جودة البيانات (نفس مبادئ طبقة الثقة)
    latest = max([str(r["date"])[:10] for r in sales] + [str(i.get("issue_date") or "")[:10] for i in invoices] or [""])
    fresh_days = (today - date.fromisoformat(latest)).days if latest else None
    quality = [{"dimension": "الاكتمال", "score": round((sum(1 for c in chk if c["status"] == "complete") / len(chk)) * 100), "detail": f"{sum(1 for c in chk if c['status'] == 'complete')} من {len(chk)} متطلبات مكتملة"},
               {"dimension": "الصحة", "score": _pct(health["valid"] + health["warning"], len(checked), 0) if checked else None, "detail": f"{health['error']} أخطاء · {health['needs_review']} تحتاج مراجعة"},
               {"dimension": "الاتساق (المطابقة)", "score": recon.get("match_rate_pct"), "detail": recon.get("status") or "لا يوجد سجل فواتير للمطابقة"},
               {"dimension": "الحداثة", "score": None if fresh_days is None else max(0, 100 - max(0, fresh_days - 35) * 2), "detail": f"آخر بيانات {latest} ({fresh_days} يوماً)" if latest else None},
               {"dimension": "التكرار", "score": 100 - min(100, health["duplicate"] * 10) if checked else None, "detail": f"{health['duplicate']} فواتير مكررة"}]

    # ── 9) التعرض والمخاطر: سجلات متأثرة + أساس؛ الغرامة فقط من قاعدة مُعدّة بمصدر
    pen = reg["penalties"]["value"] or []
    err_vat = sum((c["vat"] or 0) for c in checked if c["status"] in ("error", "duplicate"))
    exposures = []
    if checked and (health["error"] or health["duplicate"]):
        exposures.append({"key": "invoice_errors", "label": "فواتير بأخطاء أو مكررة", "records": health["error"] + health["duplicate"], "tax_amount": _r(err_vat),
                          "basis_ar": "مجموع ضريبة الفواتير المتأثرة", "rule": next((p for p in pen if p.get("applies_to") == "invoice_errors"), None),
                          "confidence": "medium"})
    if recon.get("available") and abs(recon["difference"] or 0) > TOL:
        dv = abs(recon["waterfall"][-1]["amount"] or 0)
        exposures.append({"key": "reconciliation", "label": "فرق مطابقة غير مُفسَّر", "records": len(recon["sales_without_invoice"]) + len(recon["invoices_without_sales"]),
                          "tax_amount": _r(dv * rate / (1 + rate) if incl is not False else dv * rate), "basis_ar": "ضريبة الجزء غير المُفسَّر من الفرق (تقدير)",
                          "rule": next((p for p in pen if p.get("applies_to") == "reconciliation"), None), "confidence": "low"})
    for c in cal:
        if c["status"] == "overdue":
            exposures.append({"key": "late_filing", "label": f"إقرار متأخر — {c['period']}", "records": 1, "tax_amount": None,
                              "basis_ar": "تجاوز تاريخ الاستحقاق دون تسجيل التقديم", "rule": next((p for p in pen if p.get("applies_to") == "late_filing"), None), "confidence": "medium"})
    for e in exposures:
        r_ = e.get("rule")
        e["penalty_estimate"] = None
        if r_ and r_.get("percent") not in (None, "") and e.get("tax_amount"):
            e["penalty_estimate"] = _r(e["tax_amount"] * float(r_["percent"]) / 100)
        e["penalty_note_ar"] = "لا قاعدة غرامة مُعدّة بمصدر — يُعرض الأثر الضريبي فقط" if not r_ else f"وفق القاعدة المُعدّة ({r_.get('source') or 'بلا مصدر'})"

    # ── 10) الإشارات
    sig = []

    def add(kind, code, ar, sev, evidence, action, impact=None, dim=None):
        sig.append({"id": _sid(code, dim, cur), "type": kind, "code": code, "source_module": "tax", "name_ar": ar, "severity": sev, "dimension": dim,
                    "period": cur, "evidence": [e for e in evidence if e], "suggested_action_ar": action,
                    "estimated_impact": {"value": impact, "value_decimal": str(impact)} if impact is not None else None, "metric_id": "tax_" + code,
                    "method": "rule-based (tax-v1.0)"})
    if health["error"] or health["duplicate"]:
        add("risk", "invoice_validation", f"{health['error'] + health['duplicate']} فاتورة تحتاج تصحيحاً", "high" if health["error"] + health["duplicate"] >= 10 else "medium",
            [f"أخطاء: {health['error']} · مكررة: {health['duplicate']}"] + [f"{c['invoice_number']}: {c['issues'][0]}" for c in checked if c["status"] in ("error", "duplicate")][:3],
            "راجع الفواتير المتأثرة وصحّحها في نظام الفوترة", _r(err_vat))
    if health["warning"] or health["needs_review"]:
        add("risk", "invoice_warnings", f"{health['warning'] + health['needs_review']} فاتورة ناقصة أو تحتاج مراجعة", "low",
            [f"تحذيرات: {health['warning']} · مراجعة: {health['needs_review']}"], "أكمل الحقول الناقصة (مثل رقم المشتري الضريبي)")
    if recon.get("available") and recon["status"] != "متطابقة":
        add("risk", "reconciliation_difference", "فرق مطابقة بين المبيعات وسجل الفواتير", "medium",
            [f"المبيعات {recon['sales_total']:,.0f} مقابل الفواتير {recon['invoiced_total']:,.0f}", f"مبيعات بلا فاتورة: {len(recon['sales_without_invoice'])}",
             f"غير مُفسَّر: {recon['waterfall'][-1]['amount']:,.0f}"], "حقق في البنود غير المطابقة حسب الفرع والتاريخ", recon["waterfall"][-1]["amount"])
    for c in cal:
        if c["status"] == "overdue":
            add("risk", "filing_overdue", f"إقرار {c['period']} متأخر", "high", [f"الاستحقاق {c['due_date']} · {abs(c['days_remaining'])} يوماً"], "قدّم الإقرار أو سجّل تاريخ تقديمه", dim=c["period"])
        elif c["status"] == "due_soon":
            add("risk", "filing_due_soon", f"إقرار {c['period']} مستحق خلال {c['days_remaining']} يوماً", "medium", [f"الاستحقاق {c['due_date']}"], "جهّز الإقرار وراجع المطابقة", dim=c["period"])
    for ix, c in enumerate(chk):
        if c["status"] == "missing" and c["item"] in ("الرقم الضريبي للمنشأة", "دورية الإقرار", "مبلغ الضريبة"):
            add("data_quality", f"missing_{ix}", f"{c['item']} غير متاح", "low", [c["detail"] or "لا توجد بيانات"], "أكمل البيانات من الإعدادات أو مركز البيانات", dim=c["item"])
    if input_vat is None:
        add("data_quality", "no_input_vat", "ضريبة المدخلات غير متاحة", "low", [vat["input_reason_ar"]], "أضف عمود الضريبة في ملف المشتريات")
    if zakat["readiness_pct"] is not None and zakat["readiness_pct"] < 100:
        add("data_quality", "zakat_readiness", f"جاهزية بيانات الزكاة {zakat['readiness_pct']}%", "low", [f"ناقص: {'، '.join(zakat['missing'][:5])}"], "أكمل بنود الميزانية في الوحدة المالية")
    sevo = {"high": 0, "medium": 1, "low": 2}
    sig.sort(key=lambda x: ({"risk": 0, "opportunity": 1, "data_quality": 2}[x["type"]], sevo.get(x["severity"], 3)))
    open_issues = sum(1 for x in sig if x["type"] == "risk")
    nxt = next((c for c in cal if c["status"] in ("due_soon", "upcoming", "overdue")), None)
    return {"has_data": True, "version": f"tax-v{TAX_VERSION}", "period": cur, "frequency": freq, "periods": sorted({_period_of(m, pfreq) for m in months}),
            "currency": currency, "regulatory": reg,
            "overview": {"tax_data_coverage_pct": quality[0]["score"], "vat_completeness_pct": vat_cov if vat_cov else (_pct(sum(1 for c in checked if c["vat"] is not None), len(checked)) if checked else vat_cov),
                         "einvoice_coverage_pct": recon.get("match_rate_pct"), "open_issues": open_issues,
                         "next_deadline": nxt, "potential_exposure": _r(sum((e["tax_amount"] or 0) for e in exposures)) if exposures else None,
                         "exposure_note_ar": "أثر ضريبي على سجلات تحتاج مراجعة — ليس غرامة"},
            "checklist": chk, "vat": vat, "reconciliation": recon,
            "einvoice": {"available": bool(ic), "total": len(ic), "by_type": by_type, "zatca_statuses": statuses, "health": health,
                         "phases": {"phase1": reg["einvoice_phase1"], "phase2": reg["einvoice_phase2"]},
                         "reason_ar": None if ic else "لا يوجد سجل فواتير — ارفع تصدير الفواتير من نظام الفوترة"},
            "invoices": sorted(checked, key=lambda c: ["error", "duplicate", "needs_review", "warning", "valid"].index(c["status"]))[:500],
            "integration": integration, "branches": branches, "calendar": calendar_block, "zakat": zakat, "quality": quality,
            "exposures": exposures, "signals": sig, "sector_tax": sector_tax,
            "disclaimer_ar": "فحص بيانات وليس شهادة امتثال ضريبي أو قانوني — القواعد التنظيمية من إعدادات قابلة للتحديث بمصدرها."}
