"""
NABBAH 2.7 — Purchases Intelligence engine (pure, deterministic; AI only explains).
Principles: no number without data (missing ≠ 0) · relationships, not causation · thresholds are
explicit, published and overridable (RULES) · one source of truth: purchases are read from the same
canonical rows the Data Center stored; sales (2.5) and inventory (2.6) are joined, never copied.
"""
import os, sys, hashlib
from datetime import date
from decimal import Decimal

_here = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21", "../phase22", "../phase23", "../phase24", "../phase25", "../phase26"):
    _p = os.path.join(_here, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from nabbah_finance import to_decimal, round_money, round_pct, safe_divide
from period_model import period_key, previous_key, same_period_last_year

PURCHASES_VERSION = "1.0"
D0 = Decimal("0")
RULES = {"concentration_pct": 40, "price_variance_pct": 10, "late_pct": 15, "cycle_factor": Decimal("1.5"),
         "purchase_sales_gap_pts": 15, "min_lines_for_score": 3}
OPEN_STATUSES = {"open", "مفتوح", "draft", "مسودة", "pending", "بانتظار الموافقة", "pending approval", "approved",
                 "معتمد", "sent", "مرسل", "partially received", "مستلم جزئياً", "استلام جزئي", "قيد التنفيذ"}
CLOSED_STATUSES = {"received", "مستلم", "closed", "مغلق", "cancelled", "ملغي", "ملغى", "complete", "مكتمل"}


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


def _sid(*p):
    return "pur-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


def _spend(r):
    t = _d(r.get("total_cost"))
    if t is not None:
        return t
    q, u = _d(r.get("quantity")), _d(r.get("unit_cost"))
    return q * u if q is not None and u is not None else None


def _ucost(r):
    u = _d(r.get("unit_cost"))
    if u is not None:
        return u
    t, q = _d(r.get("total_cost")), _d(r.get("quantity"))
    return safe_divide(t, q) if t is not None and q else None


def _norm_status(s):
    s = str(s or "").strip().lower()
    return "" if not s else ("open" if s in OPEN_STATUSES else ("closed" if s in CLOSED_STATUSES else "other"))


def _group(rows, key, total):
    g = {}
    for r in rows:
        k = r.get(key) or None
        if k is None:
            continue
        e = g.setdefault(k, {"spend": D0, "lines": 0, "pos": set()})
        sp = _spend(r)
        if sp is not None:
            e["spend"] += sp
        e["lines"] += 1
        if r.get("reference"):
            e["pos"].add(r["reference"])
    out = [{"key": k, "spend": _money(v["spend"]), "share_pct": _pct(v["spend"], total) if total else None,
            "lines": v["lines"], "pos": len(v["pos"]) or None} for k, v in g.items()]
    out.sort(key=lambda x: -x["spend"]["value"])
    return out


def _delivery(rows):
    """الالتزام بالتسليم: فقط للأسطر التي فيها تاريخ متوقع وتاريخ استلام فعلي."""
    ev = [(r, _date(r.get("expected_date")), _date(r.get("received_date"))) for r in rows]
    ev = [(r, e, a) for r, e, a in ev if e and a]
    if not ev:
        return None
    late = [(a - e).days for _, e, a in ev if a > e]
    return {"evaluated_lines": len(ev), "on_time_pct": _pct(Decimal(len(ev) - len(late)), Decimal(len(ev))),
            "late_pct": _pct(Decimal(len(late)), Decimal(len(ev))),
            "avg_delay_days": _num(safe_divide(Decimal(sum(late)), Decimal(len(late))), 1) if late else 0.0}


def _quality(rows):
    """القبول = 1 − المرفوض ÷ المستلم. بلا بيانات رفض/استلام = غير متاح (لا 100% افتراضية)."""
    rec = rej = D0
    n = 0
    for r in rows:
        rq, xq = _d(r.get("received_qty")), _d(r.get("rejected_qty"))
        if xq is None:
            continue
        base = rq if rq is not None else _d(r.get("quantity"))
        if base:
            rec += base; rej += xq; n += 1
    if not n or not rec:
        return None
    return {"evaluated_lines": n, "acceptance_pct": _pct(rec - rej, rec), "rejected_qty": _num(rej)}


def _cycle(rows):
    """دورة أمر الشراء = تاريخ الاستلام − تاريخ الطلب (بالأيام)."""
    c = [(_date(r.get("received_date")) - _date(r.get("date"))).days for r in rows
         if _date(r.get("received_date")) and _date(r.get("date")) and _date(r.get("received_date")) >= _date(r.get("date"))]
    return None if not c else {"avg_days": _num(safe_divide(Decimal(sum(c)), Decimal(len(c))), 1), "lines": len(c)}


def analyze_purchases(all_rows, *, period=None, grain="month", sales_rows=None, inventory_snaps=None,
                      reorder_needs=None, currency="SAR", rules=None):
    R = dict(RULES, **(rules or {}))
    sales_rows, inventory_snaps, reorder_needs = sales_rows or [], inventory_snaps or [], reorder_needs or []
    rows_all = [r for r in all_rows if r.get("date")]
    if not rows_all:
        return {"has_data": False, "version": f"purchases-v{PURCHASES_VERSION}",
                "message_ar": "لا توجد بيانات مشتريات بعد. ارفع ملف المشتريات من مركز البيانات.",
                "message_en": "No purchase data yet. Upload a purchases file from the Data Center.",
                "required": {"required": ["التاريخ", "الفرع", "الإجمالي (أو الكمية × سعر الوحدة)"],
                             "recommended": ["المورد", "رقم أمر الشراء", "الصنف", "الفئة", "الكمية", "سعر الوحدة"],
                             "optional": ["تاريخ التسليم المتوقع", "تاريخ الاستلام", "الكمية المستلمة", "الكمية المرفوضة", "الحالة"]}}
    keys = sorted({period_key(r["date"], grain) for r in rows_all if period_key(r["date"], grain)})
    cur = period if period in keys else keys[-1]
    prev = previous_key(cur, grain)
    yoy = same_period_last_year(cur, grain)
    pk = lambda r: period_key(r["date"], grain)
    rows = [r for r in rows_all if pk(r) == cur]
    prows = [r for r in rows_all if pk(r) == prev]
    yrows = [r for r in rows_all if yoy and pk(r) == yoy]
    tot = sum((s for s in (_spend(r) for r in rows) if s is not None), D0)
    ptot = sum((s for s in (_spend(r) for r in prows) if s is not None), D0) if prows else None
    ytot = sum((s for s in (_spend(r) for r in yrows) if s is not None), D0) if yrows else None
    pos = {r["reference"] for r in rows if r.get("reference")}
    ppos = {r["reference"] for r in prows if r.get("reference")}
    sups = {r["supplier_name"] for r in rows if r.get("supplier_name")}
    has_status = any(r.get("status") for r in rows_all)
    open_lines = [r for r in rows_all if _norm_status(r.get("status")) == "open"]
    open_pos = {r.get("reference") or f"line-{i}" for i, r in enumerate(open_lines)}

    def k(code, ar, cur_v, prev_v, unit, **x):
        it = {"metric_code": code, "name_ar": ar, "unit": unit, "current": cur_v, "previous": prev_v,
              "comparison_available": cur_v is not None and prev_v is not None, "change_pct": None, **x}
        if it["comparison_available"] and prev_v:
            it["change_pct"] = _pct(Decimal(str(cur_v)) - Decimal(str(prev_v)), Decimal(str(prev_v)))
        return it

    avg_po = safe_divide(tot, Decimal(len(pos))) if pos else None
    pavg_po = safe_divide(ptot, Decimal(len(ppos))) if ppos and ptot is not None else None
    kpis = {
        "spend": k("purchase_spend", "إجمالي المشتريات", float(round_money(tot)),
                   float(round_money(ptot)) if ptot is not None else None, "currency",
                   yoy_pct=_pct(tot - ytot, ytot) if ytot else None,
                   missing_cost_lines=sum(1 for r in rows if _spend(r) is None)),
        "po_count": k("po_count", "أوامر الشراء", len(pos) if pos else None, len(ppos) if ppos else None, "count",
                      reason_ar=None if pos else "غير متاح — لا يوجد رقم أمر شراء في البيانات"),
        "avg_po": k("avg_po_value", "متوسط أمر الشراء", _num(avg_po), _num(pavg_po), "currency",
                    reason_ar=None if avg_po is not None else "غير متاح — يحتاج رقم أمر الشراء"),
        "suppliers": k("active_suppliers", "الموردون النشطون", len(sups) if sups else None,
                       len({r["supplier_name"] for r in prows if r.get("supplier_name")}) or None, "count",
                       reason_ar=None if sups else "غير متاح — لا يوجد عمود المورد"),
        "open_pos": {"metric_code": "open_pos", "name_ar": "أوامر مفتوحة", "unit": "count",
                     "current": len(open_pos) if has_status else None,
                     "open_value": _money(sum((s for s in (_spend(r) for r in open_lines) if s is not None), D0)) if has_status else None,
                     "reason_ar": None if has_status else "غير متاح — لا يوجد عمود «الحالة» لأوامر الشراء"},
    }
    kpis["growth"] = {"metric_code": "purchase_growth", "name_ar": "نمو المشتريات", "unit": "percent",
                      "current": kpis["spend"]["change_pct"], "yoy_pct": kpis["spend"]["yoy_pct"],
                      "reason_ar": None if kpis["spend"]["change_pct"] is not None else "لا توجد فترة سابقة للمقارنة"}

    trend = []
    for key in keys[-24:]:
        rs = [r for r in rows_all if pk(r) == key]
        trend.append({"period": key, "spend": _money(sum((s for s in (_spend(r) for r in rs) if s is not None), D0)),
                      "pos": len({r["reference"] for r in rs if r.get("reference")}) or None})

    by_cat, by_br, by_sup = _group(rows, "category", tot), _group(rows, "branch_name", tot), _group(rows, "supplier_name", tot)

    # ── تقييم الموردين: السعر / الجودة / التسليم — كل بُعد بأدلته أو «غير متاح»
    lowest = {}
    for r in rows:
        u, sku = _ucost(r), r.get("product_sku")
        if u is not None and sku and r.get("supplier_name"):
            lowest.setdefault(sku, {}).setdefault(r["supplier_name"], []).append(u)
    sup_avg = {sku: {s: sum(v, D0) / len(v) for s, v in d.items()} for sku, d in lowest.items()}
    suppliers = []
    for s in sorted(sups):
        srs = [r for r in rows if r.get("supplier_name") == s]
        ratios = [sup_avg[sku][s] / min(sup_avg[sku].values()) for sku in sup_avg
                  if s in sup_avg[sku] and len(sup_avg[sku]) >= 2 and min(sup_avg[sku].values()) > 0]
        price = None if not ratios else {"score": _num(Decimal(100) / (sum(ratios, D0) / len(ratios)), 0),
                                         "comparable_products": len(ratios),
                                         "basis_ar": "متوسط سعره ÷ أقل سعر لنفس الصنف عند موردين آخرين"}
        dl, ql, cy = _delivery(srs), _quality(srs), _cycle(srs)
        hist = {}
        for r in rows_all:
            if r.get("supplier_name") == s:
                sp = _spend(r)
                if sp is not None:
                    hist[pk(r)] = hist.get(pk(r), D0) + sp
        sp = sum((x for x in (_spend(r) for r in srs) if x is not None), D0)
        suppliers.append({
            "supplier": s, "spend": _money(sp), "share_pct": _pct(sp, tot) if tot else None,
            "pos": len({r["reference"] for r in srs if r.get("reference")}) or None, "lines": len(srs),
            "branches": len({r.get("branch_name") for r in srs if r.get("branch_name")}),
            "categories": len({r.get("category") for r in srs if r.get("category")}),
            "products": sorted({r.get("product_sku") for r in srs if r.get("product_sku")})[:30],
            "price": price, "delivery": dl, "quality": ql, "cycle": cy,
            "spend_trend": [{"period": k2, "spend": _money(v)} for k2, v in sorted(hist.items())][-12:],
            "enough_data": len(srs) >= R["min_lines_for_score"]})
    suppliers.sort(key=lambda x: -x["spend"]["value"])
    top_share = suppliers[0]["share_pct"] if suppliers else None
    concentration = {"top_supplier": suppliers[0]["supplier"] if suppliers else None, "top_share_pct": top_share,
                     "top3_share_pct": _pct(sum((Decimal(str(x["spend"]["value"])) for x in suppliers[:3]), D0), tot) if tot and suppliers else None,
                     "threshold_pct": R["concentration_pct"],
                     "rule_ar": f"تركّز مرتفع إذا تجاوزت حصة مورد واحد {R['concentration_pct']}% من الإنفاق",
                     "at_risk": bool(top_share is not None and top_share >= R["concentration_pct"])}

    # ── السعر والتكلفة: الحالي مقابل خط الأساس التاريخي لنفس الصنف
    products = []
    for sku in sorted({r.get("product_sku") for r in rows if r.get("product_sku")}):
        cur_u = [(_ucost(r), _d(r.get("quantity"))) for r in rows if r.get("product_sku") == sku and _ucost(r) is not None]
        hist_u = [_ucost(r) for r in rows_all if r.get("product_sku") == sku and pk(r) < cur and _ucost(r) is not None]
        qty = sum((q for _, q in cur_u if q), D0)
        cur_avg = safe_divide(sum((u * (q or 1) for u, q in cur_u), D0), sum(((q or 1) for _, q in cur_u), D0)) if cur_u else None
        base = safe_divide(sum(hist_u, D0), Decimal(len(hist_u))) if hist_u else None
        var = _pct(cur_avg - base, base) if cur_avg is not None and base else None
        saving = (base - cur_avg) * qty if base is not None and cur_avg is not None and cur_avg < base and qty else None
        s_by = sup_avg.get(sku, {})
        best = min(s_by.items(), key=lambda x: x[1]) if len(s_by) >= 2 else None
        potential = None
        if best:
            potential = sum(((u - best[1]) * (q or D0) for r in rows if r.get("product_sku") == sku
                             for u, q in [(_ucost(r), _d(r.get("quantity")))] if u is not None and u > best[1]), D0)
        trend_p = {}
        for r in rows_all:
            if r.get("product_sku") == sku and _ucost(r) is not None:
                trend_p.setdefault(pk(r), []).append(_ucost(r))
        products.append({"product_sku": sku, "category": next((r.get("category") for r in rows if r.get("product_sku") == sku and r.get("category")), None),
                         "spend": _money(sum((x for x in (_spend(r) for r in rows if r.get("product_sku") == sku) if x is not None), D0)),
                         "qty": _num(qty), "avg_unit_cost": _num(cur_avg), "baseline_unit_cost": _num(base),
                         "variance_pct": var, "realized_saving": _money(saving),
                         "cheapest_supplier": best[0] if best else None, "cheapest_unit_cost": _num(best[1]) if best else None,
                         "potential_saving": _money(potential) if potential else None,
                         "suppliers": sorted(s_by), "cost_trend": [{"period": p, "avg_unit_cost": _num(sum(v, D0) / len(v))}
                                                                    for p, v in sorted(trend_p.items())][-12:]})
    products.sort(key=lambda x: -x["spend"]["value"])
    realized = sum((Decimal(str(p["realized_saving"]["value"])) for p in products if p["realized_saving"]), D0)
    potential = sum((Decimal(str(p["potential_saving"]["value"])) for p in products if p["potential_saving"]), D0)
    has_hist = any(p["baseline_unit_cost"] is not None for p in products)
    savings = {"available": has_hist, "realized": _money(realized) if has_hist else None,
               "potential": _money(potential) if potential else None,
               "basis_ar": "الوفر = (متوسط سعر الصنف في الفترات السابقة − سعره الحالي) × الكمية المشتراة الآن",
               "reason_ar": None if has_hist else "لا يمكن حساب الوفر — لا يوجد سعر تاريخي لنفس الأصناف (خط أساس)"}

    # ── دورة أمر الشراء وحالاته
    cyc = _cycle(rows)
    statuses = {}
    for r in rows:
        if r.get("status"):
            statuses[str(r["status"]).strip()] = statuses.get(str(r["status"]).strip(), 0) + 1
    po_list = {}
    for r in rows:
        ref = r.get("reference")
        if not ref:
            continue
        e = po_list.setdefault(ref, {"po": ref, "supplier": r.get("supplier_name"), "branch": r.get("branch_name"),
                                     "date": r.get("date"), "expected_date": r.get("expected_date"),
                                     "received_date": r.get("received_date"), "status": r.get("status"),
                                     "total": D0, "lines": []})
        sp = _spend(r)
        if sp is not None:
            e["total"] += sp
        e["lines"].append({"product_sku": r.get("product_sku"), "qty": _num(_d(r.get("quantity"))),
                           "unit_cost": _num(_ucost(r)), "total": _money(sp),
                           "received_qty": _num(_d(r.get("received_qty"))), "rejected_qty": _num(_d(r.get("rejected_qty")))})
    pos_out = []
    for e in sorted(po_list.values(), key=lambda x: str(x["date"]), reverse=True)[:200]:
        ex, ac = _date(e["expected_date"]), _date(e["received_date"])
        e["delay_days"] = (ac - ex).days if ex and ac else None
        e["total"] = _money(e["total"])
        pos_out.append(e)

    # ── الربط: مشتريات ↔ مخزون ↔ مبيعات (علاقة لا سببية)
    def sales_sum(filt, key):
        return sum((d for d in (_d(r.get("net_sales")) if r.get("net_sales") is not None else _d(r.get("gross_sales"))
                                for r in sales_rows if period_key(r.get("date"), grain) == key and filt(r)) if d is not None), D0)

    def inv_val(filt, key):
        vals = [_d(s.get("closing_value")) for s in inventory_snaps if s.get("period") == key and filt(s)]
        vals = [v for v in vals if v is not None]
        return sum(vals, D0) if vals else None

    def chg(a, b):
        return _pct(a - b, b) if a is not None and b else None

    cross_cat, cross_br = [], []
    cat_of_sku = {r.get("product_sku"): r.get("category") for r in rows_all + sales_rows if r.get("product_sku") and r.get("category")}
    for key, field, out in (("category", "category", cross_cat), ("branch", "branch_name", cross_br)):
        vals = sorted({r.get(field) for r in rows if r.get(field)})
        for v in vals:
            f_p = lambda r, v=v: r.get(field) == v
            f_s = f_p if field == "branch_name" else (lambda r, v=v: (r.get("category") or cat_of_sku.get(r.get("product_sku"))) == v)
            f_i = (lambda s, v=v: s.get("branch_name") == v) if field == "branch_name" else (lambda s, v=v: cat_of_sku.get(s.get("product_sku")) == v)
            pc = sum((x for x in (_spend(r) for r in rows if f_p(r)) if x is not None), D0)
            pp = sum((x for x in (_spend(r) for r in prows if f_p(r)) if x is not None), D0) if prows else None
            sc, sp = (sales_sum(f_s, cur), sales_sum(f_s, prev)) if sales_rows else (None, None)
            ic, ip = inv_val(f_i, cur), inv_val(f_i, prev)
            item = {"key": v, "purchases": _money(pc), "purchases_change_pct": chg(pc, pp),
                    "sales": _money(sc) if sales_rows else None, "sales_change_pct": chg(sc, sp) if sales_rows else None,
                    "inventory_value": _money(ic), "inventory_change_pct": chg(ic, ip)}
            if item["purchases_change_pct"] is not None and item["sales_change_pct"] is not None:
                item["gap_pts"] = round(item["purchases_change_pct"] - item["sales_change_pct"], 2)
            out.append(item)
    reorder_options = []
    for need in reorder_needs[:30]:
        sku = need.get("product_sku")
        opts = [x for x in suppliers if sku in x["products"]]
        reorder_options.append({"product_sku": sku, "branch": need.get("branch"), "inventory_status": need.get("status"),
                                "options": [{"supplier": o["supplier"],
                                             "avg_unit_cost": _num(sup_avg.get(sku, {}).get(o["supplier"])),
                                             "on_time_pct": (o["delivery"] or {}).get("on_time_pct"),
                                             "acceptance_pct": (o["quality"] or {}).get("acceptance_pct")} for o in opts]})

    # ── جودة البيانات
    n = len(rows) or 1
    dq = {"lines": len(rows),
          "missing_supplier_pct": _pct(Decimal(sum(1 for r in rows if not r.get("supplier_name"))), Decimal(n)),
          "missing_cost_pct": _pct(Decimal(sum(1 for r in rows if _ucost(r) is None)), Decimal(n)),
          "missing_po_pct": _pct(Decimal(sum(1 for r in rows if not r.get("reference"))), Decimal(n)),
          "missing_product_pct": _pct(Decimal(sum(1 for r in rows if not r.get("product_sku"))), Decimal(n)),
          "delivery_dates_pct": _pct(Decimal(sum(1 for r in rows if r.get("expected_date") and r.get("received_date"))), Decimal(n))}
    availability = {
        "suppliers": {"available": bool(sups), "how_ar": "أضف عمود «المورد» في ملف المشتريات"},
        "po": {"available": bool(pos), "how_ar": "أضف عمود «رقم أمر الشراء»"},
        "price": {"available": any(x["price"] for x in suppliers), "how_ar": "يحتاج نفس الصنف من موردَين على الأقل مع سعر الوحدة"},
        "delivery": {"available": any(x["delivery"] for x in suppliers), "how_ar": "أضف «تاريخ التسليم المتوقع» و«تاريخ الاستلام»"},
        "quality": {"available": any(x["quality"] for x in suppliers), "how_ar": "أضف «الكمية المستلمة» و«الكمية المرفوضة»"},
        "cycle": {"available": cyc is not None, "how_ar": "أضف «تاريخ الاستلام» بجانب تاريخ الطلب"},
        "status": {"available": has_status, "how_ar": "أضف عمود «الحالة» (مفتوح، مستلم، ملغي…)"},
        "savings": {"available": has_hist, "how_ar": "يحتاج مشتريات لنفس الأصناف في فترات سابقة"},
        "cross_module": {"available": bool(sales_rows or inventory_snaps), "how_ar": "ارفع ملفات المبيعات والمخزون"},
    }
    signals = _signals(concentration, suppliers, products, cyc, cross_cat, dq, R, cur, currency, reorder_options)
    return {"has_data": True, "version": f"purchases-v{PURCHASES_VERSION}", "period": cur, "previous_period": prev,
            "grain": grain, "periods": keys, "currency": currency, "rules": R, "kpis": kpis, "trend": trend,
            "by_category": by_cat, "by_branch": by_br, "by_supplier": by_sup[:50], "suppliers": suppliers[:100],
            "top5": suppliers[:5], "concentration": concentration, "products": products[:200], "savings": savings,
            "po": {"cycle": cyc, "statuses": statuses, "open_count": kpis["open_pos"]["current"],
                   "open_value": kpis["open_pos"]["open_value"], "list": pos_out},
            "cross": {"by_category": cross_cat, "by_branch": cross_br,
                      "note_ar": "المقارنة تُظهر اتجاهات متزامنة فقط ولا تثبت سبباً"},
            "reorder_options": reorder_options, "data_quality": dq, "availability": availability, "signals": signals}


def _signals(conc, suppliers, products, cyc, cross_cat, dq, R, period, currency, reorder_options):
    out = []

    def add(kind, code, ar, sev, dim, evidence, action, impact=None, metric="purchase_spend"):
        out.append({"id": _sid(code, dim, period), "type": kind, "code": code, "source_module": "purchases",
                    "name_ar": ar, "severity": sev, "dimension": dim, "period": period, "evidence": evidence,
                    "suggested_action_ar": action, "estimated_impact": impact, "metric_id": metric,
                    "method": "rule-based (purchases-v1.0)"})

    if conc["at_risk"]:
        add("risk", "supplier_concentration", "تركّز الموردين", "high" if conc["top_share_pct"] >= 60 else "medium",
            conc["top_supplier"], [f"{conc['top_supplier']} يمثل {conc['top_share_pct']}% من إنفاق المشتريات",
                                   conc["rule_ar"]], "قيّم موردين بديلين للأصناف الأساسية لتقليل الاعتماد")
    for p in sorted([p for p in products if p["variance_pct"] is not None and p["variance_pct"] >= R["price_variance_pct"]],
                    key=lambda x: -x["variance_pct"])[:5]:
        add("risk", "cost_increase", "ارتفاع تكلفة الشراء", "medium", p["product_sku"],
            [f"متوسط السعر الحالي {p['avg_unit_cost']} مقابل {p['baseline_unit_cost']} تاريخياً ({p['variance_pct']:+}%)"],
            "راجع الأسعار مع المورد أو قارن بموردين آخرين", metric="unit_cost")
    for s in suppliers:
        d = s["delivery"]
        if d and d["late_pct"] is not None and d["late_pct"] >= R["late_pct"] and d["evaluated_lines"] >= R["min_lines_for_score"]:
            add("risk", "delivery_risk", "تأخر المورد في التسليم", "medium", s["supplier"],
                [f"{d['late_pct']}% من التسليمات متأخرة بمتوسط {d['avg_delay_days']} يوم ({d['evaluated_lines']} تسليم)"],
                "ناقش الالتزام بالمواعيد مع المورد أو زد مدة التوريد في إعدادات إعادة الطلب", metric="on_time_pct")
        q = s["quality"]
        if q and q["acceptance_pct"] is not None and q["acceptance_pct"] < 95 and q["evaluated_lines"] >= R["min_lines_for_score"]:
            add("risk", "quality_risk", "انخفاض جودة التوريد", "medium", s["supplier"],
                [f"نسبة القبول {q['acceptance_pct']}% (مرفوض {q['rejected_qty']} وحدة)"],
                "راجع مواصفات الاستلام مع المورد", metric="acceptance_pct")
    if cyc:
        for s in suppliers:
            c = s["cycle"]
            if c and c["avg_days"] >= float(Decimal(str(cyc["avg_days"])) * R["cycle_factor"]) and c["avg_days"] >= 3:
                add("risk", "po_cycle_delay", "دورة شراء أطول", "low", s["supplier"],
                    [f"متوسط دورة أمر الشراء {c['avg_days']} يوم مقابل {cyc['avg_days']} للشركة"],
                    "راجع خطوات الاعتماد والتوريد لهذا المورد", metric="po_cycle_days")
    for p in sorted([p for p in products if p["potential_saving"]], key=lambda x: -x["potential_saving"]["value"])[:3]:
        add("opportunity", "potential_saving", "فرصة توفير", "low", p["product_sku"],
            [f"أقل سعر مماثل لدى {p['cheapest_supplier']}: {p['cheapest_unit_cost']}",
             f"الفرق على كميات الفترة ≈ {p['potential_saving']['value']:,.2f} {currency}"],
            "فاوض المورد الحالي أو حوّل جزءاً من الطلب للمورد الأقل سعراً", impact=p["potential_saving"], metric="unit_cost")
    for c in cross_cat:
        if c.get("gap_pts") is not None and c["gap_pts"] >= R["purchase_sales_gap_pts"]:
            add("risk", "purchase_sales_gap", "مشتريات أسرع من المبيعات", "medium", c["key"],
                [f"المشتريات {c['purchases_change_pct']:+}% مقابل المبيعات {c['sales_change_pct']:+}%"]
                + ([f"قيمة المخزون {c['inventory_change_pct']:+}%"] if c.get("inventory_change_pct") is not None else []),
                "تحقق أن الشراء يتناسب مع الطلب قبل الدفعات القادمة")
    need = [r for r in reorder_options if r["options"]]
    if need:
        add("opportunity", "reorder_supplier_options", "أصناف تحتاج شراء — موردون متاحون", "medium", None,
            [f"{len(need)} صنفاً منخفضاً/نافداً في المخزون لها موردون سابقون مع السعر والتسليم"],
            "اختر المورد الأنسب من قائمة الخيارات في الصفحة")
    if dq["missing_supplier_pct"]:
        add("data_quality", "supplier_missing", "بيانات المورد ناقصة", "low", None,
            [f"{dq['missing_supplier_pct']}% من أسطر المشتريات بلا مورد"], "أضف عمود «المورد» في ملف المشتريات")
    if dq["missing_cost_pct"]:
        add("data_quality", "cost_missing", "تكلفة الوحدة ناقصة", "low", None,
            [f"{dq['missing_cost_pct']}% من الأسطر بلا سعر وحدة — تحليل الأسعار ناقص"], "أضف «سعر الوحدة» أو «الكمية»")
    return out
