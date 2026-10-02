"""
NABBAH 3.1 — Revenue Leakage Intelligence engine (pure, deterministic; AI only explains).
Leakage → Evidence → Financial Impact → Possible Cause → Recovery Action → Measured Result.
Every amount states its rule (actual − baseline); a baseline is either configured or derived from the company's own
history and labelled as such. No data ≠ no leakage: unavailable types are reported with the reason, never as 0.
Attribution: profit leakage (excess OpEx + excess discounts + excess returns) is summed because the three are disjoint
money; receivables at risk are a balance-sheet exposure and are reported separately, never added to the total.
"""
import os, sys, hashlib
from datetime import date
from decimal import Decimal
from statistics import median

_here = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21",):
    _p = os.path.join(_here, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from nabbah_finance import to_decimal, round_money, safe_divide

LEAK_VERSION = "1.0"
D0 = Decimal("0")
RULES = {"baseline_months": 3, "ar_risk_days": 90, "heat_high_pct": 3.0, "heat_medium_pct": 1.0, "returns_rise_pct": 20.0,
         "discount_rise_pp": 2.0, "min_rows": 20}
TYPES = [("opex", "مصروفات تشغيلية زائدة"), ("discount", "خصومات فوق المعتاد"), ("returns", "مرتجعات فوق المعتاد"),
         ("ar", "ذمم متأخرة معرّضة للخطر")]
TLABEL = dict(TYPES)
OPEX_LABEL = {"payroll": "الرواتب والأجور", "rent": "الإيجار", "marketing": "التسويق", "delivery": "التوصيل", "software": "البرمجيات",
              "maintenance": "الصيانة", "utilities": "المرافق", "other": "مصروفات أخرى"}


def _d(v):
    return to_decimal(v) if v not in (None, "") else None


def _m(v):
    return None if v is None else float(round_money(v))


def _pct(a, b, p=2):
    r = safe_divide(a, b)
    return None if r is None else round(float(r) * 100, p)


def _sid(*p):
    return "leak-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def _ym_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


def _bucket(ym, grain):
    return f"{ym[:4]}-Q{(int(ym[5:7]) - 1) // 3 + 1}" if grain == "quarter" else (ym[:4] if grain == "year" else ym)


def analyze_leakage(sales_rows, *, expense_lines=None, receivables=None, cost_map=None, settings=None, period=None,
                    grain="month", actions=None, as_of=None, restricted_categories=None, currency="SAR", rules=None):
    st = settings or {}
    R = dict(RULES, **{k: float(v) for k, v in (st.get("rules") or {}).items() if v not in (None, "")}, **(rules or {}))
    expense_lines, receivables, cost_map, actions = expense_lines or [], receivables or [], cost_map or {}, actions or []
    hidden = set(restricted_categories or [])
    rows = [r for r in sales_rows if r.get("date")]
    if not rows:
        return {"has_data": False, "version": f"leak-v{LEAK_VERSION}",
                "message_ar": "لا توجد مبيعات بعد — تحليل التسرب يُبنى على بيانات المبيعات والمصروفات والذمم.",
                "required": {"required": ["المبيعات بالخصومات والمرتجعات (2.5)"],
                             "recommended": ["المصروفات أو الكشف البنكي (2.8/2.11)", "الذمم المدينة (2.8)", "تكلفة المنتجات"],
                             "optional": ["حدود مقبولة: نسبة الخصم، نسبة المرتجعات، موازنة المصروفات"]}}
    for r in rows:
        r["_ym"] = str(r["date"])[:7]
        g, dsc, ret = _d(r.get("gross_sales")), _d(r.get("discount")) or D0, _d(r.get("returns")) or D0
        net = _d(r.get("net_sales"))
        r["_gross"] = g if g is not None else ((net or D0) + dsc + ret)
        r["_disc"], r["_ret"] = dsc, ret
        r["_net"] = net if net is not None else r["_gross"] - dsc - ret
        q, uc = _d(r.get("quantity")), cost_map.get(r.get("product_sku"))
        r["_cogs"] = q * _d(uc) if q is not None and uc not in (None, "") else None
    months = sorted({r["_ym"] for r in rows})
    cur = period if period in months else months[-1]
    prev = _ym_add(cur, -1)
    base_m = [m for m in months if m < cur][-int(R["baseline_months"]):]
    has_disc_col = any(r.get("discount") not in (None, "") for r in rows)
    has_ret_col = any(r.get("returns") not in (None, "") for r in rows)
    cfg_disc, cfg_ret = _d(st.get("max_discount_rate")), _d(st.get("acceptable_return_rate"))
    budget = {k: _d(v) for k, v in ((st.get("budget") or {}).get("opex_monthly") or {}).items() if v not in (None, "")}

    def sums(rs):
        return (sum((r["_gross"] for r in rs), D0), sum((r["_disc"] for r in rs), D0), sum((r["_ret"] for r in rs), D0), sum((r["_net"] for r in rs), D0))

    def rate_baseline(filt, field):
        """المعدل المرجعي: المُعدّ في الإعدادات، وإلا وسيط معدل نفس النطاق في الأشهر السابقة."""
        cfg = cfg_disc if field == "_disc" else cfg_ret
        if cfg is not None:
            return cfg / 100, "configured"
        rates = []
        for m in base_m:
            rs = [r for r in rows if r["_ym"] == m and filt(r)]
            g = sum((r["_gross"] for r in rs), D0)
            if g:
                rates.append(sum((r[field] for r in rs), D0) / g)
        return (Decimal(str(median([float(x) for x in rates]))), "historical") if rates else (None, None)

    def rev_leak(field, has_col, ym, filt=lambda r: True):
        if not has_col:
            return None
        rs = [r for r in rows if r["_ym"] == ym and filt(r)]
        if not rs:
            return None
        g = sum((r["_gross"] for r in rs), D0)
        actual = sum((r[field] for r in rs), D0)
        base, src = rate_baseline(filt, field)
        if base is None:
            return {"available": False, "actual": _m(actual), "rate": _pct(actual, g), "reason_ar": "لا يوجد معدل مرجعي — يلزم شهر سابق على الأقل أو حد مُعدّ في الإعدادات"}
        exp = base * g
        return {"available": True, "actual": _m(actual), "rate": _pct(actual, g), "baseline_rate": round(float(base) * 100, 2), "baseline_source": src,
                "expected": _m(exp), "leakage": _m(max(D0, actual - exp)), "gross": _m(g)}

    # ── 1) المصروفات الزائدة: الفعلي − (الموازنة المُعدّة، وإلا وسيط نفس البند في الأشهر السابقة)
    def opex_leak(ym, branch=None):
        cur_l = [x for x in expense_lines if x["ym"] == ym and x["cat"] in OPEX_LABEL and (branch is None or x.get("branch") == branch)]
        if not expense_lines or not any(x["ym"] == ym for x in expense_lines):
            return {"available": False, "reason_ar": "لا توجد بيانات مصروفات لهذه الفترة"}
        lines, total = [], D0
        for cat in OPEX_LABEL:
            a = sum((x["amount"] for x in cur_l if x["cat"] == cat), D0)
            hist = [sum((x["amount"] for x in expense_lines if x["ym"] == m and x["cat"] == cat and (branch is None or x.get("branch") == branch)), D0) for m in base_m]
            if budget.get(cat) is not None and branch is None:
                base, src = budget[cat], "configured"
            elif hist:
                base, src = Decimal(str(median([float(h) for h in hist]))), "historical"
            else:
                base, src = None, None
            if not a and not base:
                continue
            leak = max(D0, a - base) if base is not None else None
            item = {"key": cat, "label": OPEX_LABEL[cat], "actual": _m(a), "baseline": _m(base), "baseline_source": src,
                    "leakage": _m(leak), "change_pct": _pct(a - base, base) if base else None, "restricted": cat in hidden}
            if cat in hidden:
                item.update({"actual": None, "baseline": None, "leakage": None})
            lines.append(item)
            if leak is not None and cat not in hidden:
                total += leak
        has_base = any(l["baseline_source"] for l in lines)
        return {"available": has_base, "leakage": _m(total) if has_base else None, "lines": sorted(lines, key=lambda l: -(l["leakage"] or 0)),
                "reason_ar": None if has_base else "لا يوجد خط أساس — يلزم شهر سابق على الأقل أو موازنة مُدخلة في الوحدة المالية",
                "method_ar": "الفعلي − الموازنة المُعدّة (إن وُجدت) وإلا وسيط نفس البند لآخر " + str(len(base_m)) + " أشهر"}

    # ── 4) الذمم المعرّضة للخطر (لا تُضاف لإجمالي التسرب)
    today = as_of or date.fromisoformat(f"{cur}-28")
    def ar_block(filt=lambda a: True):
        rec = [a for a in receivables if filt(a)]
        if not rec:
            return {"available": False, "reason_ar": "لا توجد بيانات ذمم مدينة (ارفع ملف الذمم من مركز البيانات)"}
        buckets = {"current": D0, "1-30": D0, "31-60": D0, "61-90": D0, "90+": D0}
        risk, cust, total = D0, {}, D0
        for a in rec:
            amt, paid = _d(a.get("amount")) or D0, _d(a.get("paid_amount")) or D0
            open_ = amt - paid
            due = a.get("due_date") or a.get("invoice_date")
            if open_ <= 0 or not due:
                continue
            try:
                days = (today - date.fromisoformat(str(due)[:10])).days
            except ValueError:
                continue
            total += open_
            k = "current" if days <= 0 else "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
            buckets[k] += open_
            if days > R["ar_risk_days"]:
                risk += open_
                c = a.get("customer_name") or "غير محدد"
                cust[c] = cust.get(c, D0) + open_
        rate = _d(st.get("cost_of_capital_annual"))
        delay_cost = sum(((_d(a.get("amount")) or D0) - (_d(a.get("paid_amount")) or D0)) * rate / 100 *
                         max(0, (today - date.fromisoformat(str(a.get("due_date") or a.get("invoice_date"))[:10])).days) / 365
                         for a in rec if rate is not None and (a.get("due_date") or a.get("invoice_date"))
                         and ((_d(a.get("amount")) or D0) - (_d(a.get("paid_amount")) or D0)) > 0) if rate is not None else None
        top = sorted(cust.items(), key=lambda x: -x[1])
        return {"available": True, "total_open": _m(total), "at_risk": _m(risk), "at_risk_pct": _pct(risk, total),
                "aging": [{"bucket": k, "amount": _m(v)} for k, v in buckets.items()], "risk_days": int(R["ar_risk_days"]),
                "delay_cost": _m(delay_cost), "delay_cost_note_ar": None if rate is not None else "تكلفة التأخير تحتاج تكلفة التمويل السنوية (إعدادات)",
                "top_customers": [{"customer": c, "amount": _m(v), "share_pct": _pct(v, risk)} for c, v in top[:8]],
                "method_ar": f"ذمم مفتوحة تجاوزت تاريخ الاستحقاق بأكثر من {int(R['ar_risk_days'])} يوماً — مخاطرة تحصيل، لا خسارة مؤكدة"}

    def overall(ym, branch=None):
        f = (lambda r: r.get("branch_name") == branch) if branch else (lambda r: True)
        net = sum((r["_net"] for r in rows if r["_ym"] == ym and f(r)), D0)
        d, rt, ox = rev_leak("_disc", has_disc_col, ym, f), rev_leak("_ret", has_ret_col, ym, f), opex_leak(ym, branch)
        parts = {"discount": d, "returns": rt, "opex": ox}
        total = sum((Decimal(str(p["leakage"])) for p in parts.values() if p and p.get("available") and p.get("leakage") is not None), D0)
        avail = [k for k, p in parts.items() if p and p.get("available")]
        return {"net_revenue": _m(net), "total": _m(total) if avail else None, "rate_pct": _pct(total, net) if avail else None,
                "parts": parts, "available_types": avail}
    O = overall(cur)
    Op = overall(prev) if prev in months else None
    ar = ar_block()
    types = []
    for key, lab in TYPES:
        p = ar if key == "ar" else O["parts"][key]
        val = (p or {}).get("at_risk" if key == "ar" else "leakage") if p and p.get("available") else None
        types.append({"key": key, "label": lab, "available": bool(p and p.get("available")), "amount": val,
                      "share_pct": _pct(Decimal(str(val)), Decimal(str(O["total"]))) if key != "ar" and val is not None and O["total"] else None,
                      "in_total": key != "ar", "reason_ar": (p or {}).get("reason_ar") if not (p and p.get("available")) else None,
                      "previous": ((Op["parts"][key] or {}).get("leakage") if Op and key != "ar" and Op["parts"][key] and Op["parts"][key].get("available") else None)})
    if not has_disc_col:
        next(t for t in types if t["key"] == "discount")["reason_ar"] = "عمود الخصم غير موجود في بيانات المبيعات"
    if not has_ret_col:
        next(t for t in types if t["key"] == "returns")["reason_ar"] = "عمود المرتجعات غير موجود في بيانات المبيعات"

    # ── الخصومات: حسب الفرع/الفئة/القناة/الحملة + الفعالية حين تسمح البيانات
    def by_dim(field, key):
        out = []
        for v in sorted({r.get(key) for r in rows if r["_ym"] == cur and r.get(key)}):
            x = rev_leak(field, True, cur, lambda r, v=v: r.get(key) == v)
            if x:
                rs = [r for r in rows if r["_ym"] == cur and r.get(key) == v]
                cc = [r for r in rs if r["_cogs"] is not None]
                out.append({"key": v, **x, "net": _m(sum((r["_net"] for r in rs), D0)),
                            "margin_after": _pct(sum((r["_net"] for r in cc), D0) - sum((r["_cogs"] for r in cc), D0), sum((r["_net"] for r in cc), D0)) if cc else None})
        return sorted(out, key=lambda x: -(x.get("leakage") or 0))
    eff = None
    promo_rows = [r for r in rows if r.get("promotion") and r["_ym"] in (base_m + [cur])]
    if promo_rows:
        eff = []
        for pname in sorted({r["promotion"] for r in promo_rows}):
            pr = [r for r in rows if r.get("promotion") == pname]
            days_p = {str(r["date"])[:10] for r in pr}
            months_p = {r["_ym"] for r in pr}
            nonp = [r for r in rows if r["_ym"] in months_p and str(r["date"])[:10] not in days_p]
            days_n = {str(r["date"])[:10] for r in nonp}
            if not days_n:
                eff.append({"campaign": pname, "determined": False, "reason_ar": "لا توجد أيام خارج الحملة للمقارنة"})
                continue
            daily_p = sum((r["_net"] for r in rows if str(r["date"])[:10] in days_p), D0) / len(days_p)
            daily_n = sum((r["_net"] for r in nonp), D0) / len(days_n)
            disc_cost = sum((r["_disc"] for r in pr), D0)
            uplift = (daily_p - daily_n) * len(days_p)
            eff.append({"campaign": pname, "determined": True, "days": len(days_p), "discount_cost": _m(disc_cost),
                        "daily_net_campaign": _m(daily_p), "daily_net_other": _m(daily_n), "uplift_revenue": _m(uplift),
                        "verdict_ar": ("ارتفعت المبيعات اليومية خلال الحملة بأكثر من تكلفة الخصم" if uplift > disc_cost else
                                       "ارتفعت المبيعات اليومية لكن أقل من تكلفة الخصم" if uplift > 0 else "لم يظهر ارتفاع في المبيعات اليومية خلال الحملة"),
                        "note_ar": "مقارنة زمنية (أيام الحملة مقابل باقي أيام نفس الأشهر) — ليست إثباتاً سببياً"})
    discount = {**(O["parts"]["discount"] or {"available": False}), "by_branch": by_dim("_disc", "branch_name") if has_disc_col else [],
                "by_category": by_dim("_disc", "category") if has_disc_col else [], "by_channel": by_dim("_disc", "channel") if has_disc_col else [],
                "by_product": by_dim("_disc", "product_sku")[:15] if has_disc_col else [],
                "frequency_pct": _pct(Decimal(sum(1 for r in rows if r["_ym"] == cur and r["_disc"] > 0)), Decimal(sum(1 for r in rows if r["_ym"] == cur))),
                "effectiveness": eff, "effectiveness_note_ar": None if eff else "لا يمكن تحديد فعالية الخصم — لا توجد بيانات حملات لقياس الأثر",
                "method_ar": "الخصم الفعلي − (المعدل المرجعي × إجمالي المبيعات). المعدل المرجعي: الحد المُعدّ أو وسيط الأشهر السابقة لنفس النطاق"}
    returns = {**(O["parts"]["returns"] or {"available": False}), "by_branch": by_dim("_ret", "branch_name") if has_ret_col else [],
               "by_category": by_dim("_ret", "category") if has_ret_col else [], "by_product": by_dim("_ret", "product_sku")[:15] if has_ret_col else [],
               "reason_ar_note": "سبب الإرجاع غير متاح في البيانات" if not any(r.get("return_reason") for r in rows) else None,
               "method_ar": "المرتجعات الفعلية − (المعدل المرجعي × إجمالي المبيعات)"}

    # ── الفروع والخريطة الحرارية
    branches, heat = [], []
    for b in sorted({r.get("branch_name") for r in rows if r.get("branch_name")}):
        ob, obp = overall(cur, b), (overall(prev, b) if prev in months else None)
        arb = ar_block(lambda a, b=b: a.get("branch_name") == b)
        cells = {}
        for key, _ in TYPES:
            p = arb if key == "ar" else ob["parts"][key]
            amt = (p or {}).get("at_risk" if key == "ar" else "leakage") if p and p.get("available") else None
            pct_ = _pct(Decimal(str(amt)), Decimal(str(ob["net_revenue"]))) if amt is not None and ob["net_revenue"] else None
            lvl = "unavailable" if amt is None else ("high" if pct_ >= R["heat_high_pct"] else "medium" if pct_ >= R["heat_medium_pct"] else "low" if amt > 0 else "none")
            cells[key] = {"amount": amt, "pct_of_revenue": pct_, "level": lvl}
        top = max(((k, c["amount"]) for k, c in cells.items() if k != "ar" and c["amount"]), key=lambda x: x[1], default=None)
        branches.append({"key": b, "revenue": ob["net_revenue"], "leakage": ob["total"], "rate_pct": ob["rate_pct"],
                         "previous_leakage": obp["total"] if obp else None, "top_source": TLABEL[top[0]] if top else None,
                         "ar_at_risk": cells["ar"]["amount"], "cells": cells,
                         "open_actions": sum(1 for a in actions if a.get("dimension") == b and a.get("status") not in ("done", "cancelled")),
                         "trend": [{"period": m, "leakage": overall(m, b)["total"]} for m in months[-12:]]})
        heat.append({"branch": b, "cells": cells})
    heat_rule = f"مرتفع ≥ {R['heat_high_pct']:g}% من إيراد الفرع · متوسط ≥ {R['heat_medium_pct']:g}% · منخفض أقل من ذلك · رمادي = لا بيانات"

    # ── المنتجات والفئات: مبيعات كثيرة وهامش ضعيف بسبب الخصم/المرتجعات
    def prod_tbl(key):
        out = []
        for v in sorted({r.get(key) for r in rows if r["_ym"] == cur and r.get(key)}):
            rs = [r for r in rows if r["_ym"] == cur and r.get(key) == v]
            g, d, rt, n = sums(rs)
            cc = [r for r in rs if r["_cogs"] is not None]
            gm = _pct(sum((r["_net"] for r in cc), D0) - sum((r["_cogs"] for r in cc), D0), sum((r["_net"] for r in cc), D0)) if cc else None
            dl, rl = rev_leak("_disc", has_disc_col, cur, lambda r, v=v: r.get(key) == v), rev_leak("_ret", has_ret_col, cur, lambda r, v=v: r.get(key) == v)
            leak = sum((Decimal(str(x["leakage"])) for x in (dl, rl) if x and x.get("available")), D0)
            out.append({"key": v, "sales": _m(n), "gross_margin": gm, "discount_pct": _pct(d, g), "returns_pct": _pct(rt, g),
                        "leakage": _m(leak), "leakage_pct": _pct(leak, n)})
        tot = sum((Decimal(str(x["sales"])) for x in out), D0)
        avg_gm = [x["gross_margin"] for x in out if x["gross_margin"] is not None]
        mgm = median(avg_gm) if avg_gm else None
        for x in out:
            x["high_sales_low_margin"] = bool(mgm is not None and x["gross_margin"] is not None and tot and
                                              x["sales"] / float(tot) >= 1 / max(len(out), 1) and x["gross_margin"] < mgm and (x["leakage"] or 0) > 0)
        return sorted(out, key=lambda x: -(x["leakage"] or 0))
    products, categories = prod_tbl("product_sku")[:30], prod_tbl("category")

    # ── الاتجاهات
    trends = []
    for b in sorted({_bucket(m, grain) for m in months})[-24:]:
        ms = [m for m in months if _bucket(m, grain) == b]
        agg = {"discount": D0, "returns": D0, "opex": D0}
        net, av = D0, set()
        for m in ms:
            o = overall(m)
            net += Decimal(str(o["net_revenue"] or 0))
            for k in agg:
                p = o["parts"][k]
                if p and p.get("available") and p.get("leakage") is not None:
                    agg[k] += Decimal(str(p["leakage"]))
                    av.add(k)
        tot = sum((agg[k] for k in av), D0)
        trends.append({"period": b, "total": _m(tot) if av else None, "rate_pct": _pct(tot, net) if av else None,
                       **{k: (_m(agg[k]) if k in av else None) for k in agg}})

    # ── الأسباب المحتملة (أدلة لا أحكام)
    causes = []
    for key, field, lab in (("discount", "_disc", "الخصومات"), ("returns", "_ret", "المرتجعات")):
        p = O["parts"][key]
        if not (p and p.get("available") and p.get("leakage")):
            continue
        ev = []
        for dim, dl in (("product_sku", "المنتج"), ("branch_name", "الفرع"), ("category", "الفئة")):
            vals = {}
            for r in rows:
                if r["_ym"] == cur and r.get(dim):
                    vals[r[dim]] = vals.get(r[dim], D0) + r[field]
            tot = sum(vals.values(), D0)
            if tot:
                k_, v_ = max(vals.items(), key=lambda x: x[1])
                ev.append({"dimension": dl, "value": k_, "share_pct": _pct(v_, tot)})
        start = next((m for m in months if (rev_leak(field, True, m) or {}).get("leakage")), None)
        prevl = (Op["parts"][key] or {}).get("leakage") if Op and Op["parts"][key] else None
        causes.append({"type": key, "label": lab, "leakage": p["leakage"], "change_pct": _pct(Decimal(str(p["leakage"])) - Decimal(str(prevl)), Decimal(str(prevl))) if prevl else None,
                       "evidence": ev, "started": start,
                       "possible_ar": " / ".join(f"{e['dimension']} {e['value']}" for e in ev if (e["share_pct"] or 0) >= 40) or None,
                       "note_ar": "عوامل محتملة مرتبطة بالبيانات — ليست سبباً مؤكداً"})
    ox = O["parts"]["opex"]
    if ox and ox.get("available") and ox.get("leakage"):
        top = next((l for l in ox["lines"] if l["leakage"]), None)
        if top:
            causes.append({"type": "opex", "label": "المصروفات", "leakage": ox["leakage"], "change_pct": top["change_pct"], "started": cur,
                           "evidence": [{"dimension": "البند", "value": top["label"], "share_pct": _pct(Decimal(str(top["leakage"])), Decimal(str(ox["leakage"])))}],
                           "possible_ar": f"البند {top['label']}", "note_ar": "عوامل محتملة مرتبطة بالبيانات — ليست سبباً مؤكداً"})

    # ── خطة الاسترداد: قبل/بعد فقط حين تتوفر فترة لاحقة فعلية
    def source_leak(code, dim, ym):
        key = {"opex_excess": "opex", "discount_excess": "discount", "returns_excess": "returns"}.get(code)
        if not key:
            return None
        o = overall(ym, dim if dim in {b["key"] for b in branches} else None)
        p = o["parts"].get(key)
        if key == "opex" and p and p.get("available") and dim and dim not in {b["key"] for b in branches}:
            ln = next((l for l in p["lines"] if l["label"] == dim), None)
            return ln["leakage"] if ln else None
        return p.get("leakage") if p and p.get("available") else None
    plan = []
    for a in actions:
        before = source_leak(a.get("code"), a.get("dimension"), a.get("period")) if a.get("period") in months else None
        later = [m for m in months if a.get("period") and m > a["period"]]
        after = source_leak(a.get("code"), a.get("dimension"), later[-1]) if later else None
        overdue = a.get("status") not in ("done", "cancelled") and a.get("due_date") and str(a["due_date"]) < today.isoformat()
        plan.append({**a, "overdue": bool(overdue), "before": before, "after": after,
                     "improvement": _m(Decimal(str(before)) - Decimal(str(after))) if before is not None and after is not None else None,
                     "measured": after is not None, "measure_note_ar": None if after is not None else "لا توجد فترة لاحقة بعد القرار لقياس الأثر الفعلي"})
    recovery = {"open": sum(1 for p in plan if p.get("status") == "open"), "in_progress": sum(1 for p in plan if p.get("status") == "in_progress"),
                "completed": sum(1 for p in plan if p.get("status") == "done"), "overdue": sum(1 for p in plan if p["overdue"]),
                "expected_savings": _m(sum((Decimal(str(p.get("expected") or 0)) for p in plan), D0)),
                "measured_improvement": _m(sum((Decimal(str(p["improvement"])) for p in plan if p["improvement"] and p["improvement"] > 0), D0)),
                "note_ar": "الوفر المتوقع ≠ المسترد فعلياً: المسترد يُقاس من بيانات فترة لاحقة لنفس المصدر", "actions": plan}

    signals = _signals(R, cur, O, Op, types, ar, branches, discount, returns, causes, currency, has_disc_col, has_ret_col, expense_lines)
    sev = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda x: ({"risk": 0, "opportunity": 1, "data_quality": 2}[x["type"]], sev.get(x["severity"], 3), -((x.get("estimated_impact") or {}).get("value") or 0)))
    return {"has_data": True, "version": f"leak-v{LEAK_VERSION}", "period": cur, "previous_period": prev, "periods": months, "grain": grain,
            "currency": currency, "rules": R, "baseline_months": base_m,
            "overview": {"total": O["total"], "rate_pct": O["rate_pct"], "net_revenue": O["net_revenue"],
                         "previous_total": Op["total"] if Op else None, "available_types": O["available_types"],
                         "ar_at_risk": ar.get("at_risk") if ar.get("available") else None, "types": types,
                         "attribution_ar": "الإجمالي = المصروفات الزائدة + الخصومات فوق المعتاد + المرتجعات فوق المعتاد (أموال منفصلة لا تتداخل). "
                                           "الذمم المعرّضة للخطر تُعرض منفصلة ولا تُضاف، لأنها مخاطرة تحصيل وليست خسارة محققة."},
            "opex": O["parts"]["opex"], "discount": discount, "returns": returns, "ar": ar, "branches": branches,
            "heatmap": {"rows": heat, "rule_ar": heat_rule}, "products": products, "categories": categories, "trends": trends,
            "root_causes": causes, "recovery": recovery, "signals": signals,
            "data_quality": {"has_discount_column": has_disc_col, "has_returns_column": has_ret_col, "has_expenses": bool(expense_lines),
                             "has_receivables": bool(receivables), "baseline_months": len(base_m),
                             "baseline_note_ar": "خط الأساس من تاريخ الشركة نفسها ما لم تُدخل حدوداً في الإعدادات"}}


def _signals(R, period, O, Op, types, ar, branches, disc, ret, causes, cur_, has_d, has_r, exp):
    out = []

    def add(kind, code, ar_, sev, dim, evidence, impact, action, metric):
        out.append({"id": _sid(code, dim, period), "type": kind, "code": code, "source_module": "leakage", "name_ar": ar_, "severity": sev,
                    "dimension": dim, "period": period, "evidence": [e for e in evidence if e],
                    "estimated_impact": {"value": impact, "value_decimal": str(impact)} if impact is not None else None,
                    "suggested_action_ar": action, "metric_id": metric, "method": "rule-based (leak-v1.0)"})
    if ret.get("available") and ret.get("leakage"):
        c = next((x for x in causes if x["type"] == "returns"), {})
        sev = "high" if (c.get("change_pct") or 0) >= R["returns_rise_pct"] else "medium"
        add("risk", "returns_excess", "مرتجعات فوق المعتاد", sev, None,
            [f"معدل المرتجعات {ret['rate']}% مقابل مرجعي {ret['baseline_rate']}% ({'مُعدّ' if ret['baseline_source'] == 'configured' else 'تاريخي'})"]
            + ([f"التغير {c['change_pct']:+}% عن الشهر السابق"] if c.get("change_pct") is not None else [])
            + [f"{e['dimension']} {e['value']}: {e['share_pct']}% من المرتجعات" for e in c.get("evidence", [])[:2]],
            ret["leakage"], "راجع المنتجات والفروع الأعلى إرجاعاً وأسباب الإرجاع", "returns")
    if disc.get("available") and disc.get("leakage"):
        add("risk", "discount_excess", "خصومات فوق المعتاد", "medium", None,
            [f"معدل الخصم {disc['rate']}% مقابل مرجعي {disc['baseline_rate']}%", f"تكرار الخصم في {disc['frequency_pct']}% من المعاملات"]
            + [f"{b['key']}: {b['rate']}%" for b in disc["by_branch"][:2] if b.get("leakage")],
            disc["leakage"], "راجع سياسة الخصومات وحدود الصلاحية لكل فرع", "discount_rate")
    for b in disc.get("by_branch", []):
        if b.get("available") and b.get("rate") is not None and b.get("baseline_rate") is not None and b["rate"] - b["baseline_rate"] >= R["discount_rise_pp"]:
            add("risk", "discount_excess", "خصومات مرتفعة في فرع", "low", b["key"], [f"معدل الخصم {b['rate']}% مقابل {b['baseline_rate']}%"], b.get("leakage"),
                "راجع صلاحيات الخصم في الفرع", "discount_rate")
    ox = O["parts"]["opex"]
    if ox and ox.get("available"):
        for l in ox["lines"][:3]:
            if l["leakage"]:
                add("risk", "opex_excess", f"{l['label']} فوق خط الأساس", "medium" if l["change_pct"] and l["change_pct"] >= 25 else "low", l["label"],
                    [f"الفعلي {l['actual']:,.0f} مقابل {'الموازنة' if l['baseline_source'] == 'configured' else 'الوسيط التاريخي'} {(l['baseline'] or 0):,.0f}"
                     + (f" ({l['change_pct']:+}%)" if l["change_pct"] is not None else " — بند لم يُصرف عليه في فترة خط الأساس")],
                    l["leakage"], f"راجع بنود {l['label']} وضرورتها", "opex")
    if ar.get("available") and ar.get("at_risk"):
        add("risk", "ar_at_risk", "ذمم متأخرة معرّضة للخطر", "high" if (ar["at_risk_pct"] or 0) >= 20 else "medium", None,
            [f"{ar['at_risk']:,.0f} متأخرة أكثر من {ar['risk_days']} يوماً ({ar['at_risk_pct']}% من الذمم)"]
            + [f"{c['customer']}: {c['amount']:,.0f}" for c in ar["top_customers"][:3]], ar["at_risk"],
            "ابدأ التحصيل بأكبر العملاء المتأخرين وراجع شروط الائتمان", "ar")
    for b in branches:
        hi = [k for k, c in b["cells"].items() if c["level"] == "high"]
        if len(hi) >= 2:
            add("risk", "branch_hotspot", "فرع بتسرب مرتفع في أكثر من مصدر", "high", b["key"],
                [f"{TLABEL[k]}: {b['cells'][k]['pct_of_revenue']}% من الإيراد" for k in hi], b["leakage"], "افتح تحليل الفرع وحدد مسؤولاً للاسترداد", "leakage")
    if O["total"] is not None and Op and Op["total"] is not None and O["total"] < Op["total"]:
        add("opportunity", "leakage_down", "تراجع التسرب", "low", None, [f"{Op['total']:,.0f} ← {O['total']:,.0f}"], None,
            "ثبّت الإجراءات التي خفضت التسرب", "leakage")
    for t in types:
        if not t["available"]:
            add("data_quality", "type_unavailable", f"تحليل {t['label']} غير متاح", "low", t["label"], [t["reason_ar"]], None,
                "أكمل البيانات المطلوبة من مركز البيانات أو الإعدادات", "data_quality")
    return out
