"""
NABBAH 2.5 — Sales Intelligence engine (pure: no DB, no AI, Decimal).
Consumes canonical `sale` rows from Phase 2.4 and the metric registry as the
single source of truth. Never fabricates: missing dimension => "unavailable".
"""
import os, sys
from decimal import Decimal
_H = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21", "../phase22", "../phase23", "../phase24"):
    sys.path.insert(0, os.path.join(_H, _d))
from nabbah_finance import to_decimal, round_money, round_pct, safe_divide
from period_model import period_key, previous_key, same_period_last_year, GRAINS
from metric_registry import get_metric
from period_aggregation import parse_period
from forecast_engine import forecast_metric

SALES_VERSION = "1.1"
D0 = Decimal(0)


def _d(v):
    return to_decimal(v) or D0


def _money(v):
    return None if v is None else {"value": float(round_money(v)), "value_decimal": str(round_money(v))}


def _pct(n, d):
    r = safe_divide(to_decimal(n), to_decimal(d))
    return None if r is None else float(round_pct(r * 100))


def _agg(rows):
    a = {"gross": D0, "disc": D0, "ret": D0, "net": D0, "vat": D0, "qty": D0, "tx": set(), "rows": 0}
    for r in rows:
        a["gross"] += _d(r.get("gross_sales")); a["disc"] += _d(r.get("discounts"))
        a["ret"] += _d(r.get("returns")); a["vat"] += _d(r.get("vat")); a["qty"] += _d(r.get("quantity"))
        n = to_decimal(r.get("net_sales"))
        a["net"] += n if n is not None else (_d(r.get("gross_sales")) - _d(r.get("discounts")) - _d(r.get("returns")))
        if r.get("reference"):
            a["tx"].add(str(r["reference"]))
        a["rows"] += 1
    return a


def _overview(rows, currency, period):
    a = _agg(rows)
    tx = len(a["tx"]) or None
    aov = safe_divide(a["net"], Decimal(tx)) if tx else None
    m = lambda code, val, unit="currency": {
        "metric_code": code, "name_ar": (get_metric(code) or {}).get("name_ar", code),
        "name_en": (get_metric(code) or {}).get("name_en", code),
        "formula": (get_metric(code) or {}).get("formula", ""), "unit": unit,
        "currency": currency if unit == "currency" else None, "period": period,
        "source": "companysale", "has_data": val is not None,
        **( _money(val) if unit == "currency" and val is not None else {"value": val, "value_decimal": str(val) if val is not None else None})}
    return {
        "gross_sales": m("gross_sales", a["gross"]), "discounts": m("discounts", a["disc"]),
        "returns": m("returns", a["ret"]), "net_sales": m("net_sales", a["net"]),
        "vat": m("vat", a["vat"]),
        "transactions": m("transactions", tx, "count"),
        "quantity": m("quantity", float(a["qty"]) if a["qty"] else None, "count"),
        "aov": m("aov", aov),
        "discount_rate": m("discount_pct", _pct(a["disc"], a["gross"]), "percent"),
        "return_rate": m("return_pct", _pct(a["ret"], a["gross"]), "percent"),
        "records": a["rows"],
    }


def _net(rows):
    return _agg(rows)["net"]


def _group(rows, key, currency, prev_rows=None, limit=None, total=None):
    """تجميع عام حسب بُعد. القيم الناقصة تُبلَّغ ولا تُحوَّل لصفر."""
    cur, prev, missing = {}, {}, 0
    for r in rows:
        k = r.get(key)
        if k in (None, ""):
            missing += 1
            continue
        cur.setdefault(str(k), []).append(r)
    for r in prev_rows or []:
        k = r.get(key)
        if k not in (None, ""):
            prev.setdefault(str(k), []).append(r)
    total = total if total is not None else sum(_net(v) for v in cur.values())
    items = []
    for k, rs in cur.items():
        a = _agg(rs)
        p = _net(prev[k]) if k in prev else None
        items.append({"key": k, "net_sales": _money(a["net"]), "quantity": float(a["qty"]) if a["qty"] else None,
                      "transactions": len(a["tx"]) or None,
                      "aov": _money(safe_divide(a["net"], Decimal(len(a["tx"])))) if a["tx"] else None,
                      "returns": _money(a["ret"]), "discounts": _money(a["disc"]),
                      "growth_pct": _pct(a["net"] - p, p) if p and p != 0 else None,
                      "contribution_pct": _pct(a["net"], total) if total else None,
                      "has_comparison": bool(p)})
    items.sort(key=lambda x: -(x["net_sales"]["value"] or 0))
    out = {"items": items[:limit] if limit else items, "count": len(items),
           "missing_records": missing, "currency": currency,
           "available": bool(items)}
    if missing:
        _lbl = {"branch_name": "فرع", "channel": "قناة بيع", "product_sku": "منتج", "category": "فئة"}.get(key, key)
        out["note_ar"] = f"{missing} سجل بلا {_lbl} — التحليل قد يكون ناقصاً"
        out["note_en"] = f"{missing} records without {key} — analysis may be incomplete"
    if not items:
        out["unavailable_reason_ar"] = "لا توجد بيانات لهذا البُعد"
        out["unavailable_reason_en"] = "No data for this dimension"
    return out


def _rank_returns(rows, key, currency, limit=5):
    """ترتيب حسب قيمة المرتجعات نفسها — لا حسب المبيعات."""
    g = {}
    for r in rows:
        k = r.get(key)
        if k in (None, ""):
            continue
        g.setdefault(str(k), []).append(r)
    items = []
    for k, rs in g.items():
        a = _agg(rs)
        if a["ret"] <= 0:
            continue
        items.append({"key": k, "returns": _money(a["ret"]), "net_sales": _money(a["net"]),
                      "return_rate_pct": _pct(a["ret"], a["gross"]), "currency": currency})
    items.sort(key=lambda x: -x["returns"]["value"])
    return items[:limit]


def _rank_discounts(rows, key, currency, limit=5):
    """ترتيب حسب قيمة الخصم نفسها، مع نسبة الخصم من إجمالي مبيعات نفس البُعد."""
    g = {}
    for r in rows:
        k = r.get(key)
        if k in (None, ""):
            continue
        g.setdefault(str(k), []).append(r)
    items = []
    for k, rs in g.items():
        a = _agg(rs)
        if a["disc"] <= 0:
            continue
        items.append({"key": k, "discounts": _money(a["disc"]), "gross_sales": _money(a["gross"]),
                      "discount_rate_pct": _pct(a["disc"], a["gross"]), "currency": currency})
    items.sort(key=lambda x: -x["discounts"]["value"])
    return items[:limit]


def _series_by_period(rows, grain, field, currency):
    """سلسلة زمنية لقيمة (خصم/مرتجع) ونسبتها من الإجمالي."""
    buckets = {}
    for r in rows:
        k = period_key(r.get("date"), grain)
        if k:
            buckets.setdefault(k, []).append(r)
    out = []
    for k, v in sorted(buckets.items()):
        a = _agg(v)
        val = a["disc"] if field == "discounts" else a["ret"]
        out.append({"period": k, field: _money(val), "rate_pct": _pct(val, a["gross"])})
    return out


KPI_ORDER = ("net_sales", "gross_sales", "transactions", "aov", "quantity",
             "discounts", "discount_rate", "returns", "return_rate")


def _kpis(overview, prev_overview):
    """كل مؤشر: الحالي ← المقارنة ← التغيّر. بلا فترة سابقة = غير متاح (لا صفر)."""
    out = {}
    for code in KPI_ORDER:
        cur = overview.get(code) or {}
        prv = (prev_overview or {}).get(code) or {}
        cv, pv = cur.get("value"), prv.get("value")
        item = {"metric_code": cur.get("metric_code", code), "name_ar": cur.get("name_ar"),
                "name_en": cur.get("name_en"), "unit": cur.get("unit"), "currency": cur.get("currency"),
                "current": cv, "previous": pv, "change": None, "change_pct": None,
                "comparison_available": pv is not None and cv is not None}
        if item["comparison_available"]:
            dc, dp = to_decimal(cv), to_decimal(pv)
            item["change"] = float(round_money(dc - dp)) if cur.get("unit") == "currency" else float(round_pct(dc - dp))
            if cur.get("unit") == "percent":
                item["change_pct"] = None          # للنِّسب نعرض الفرق بالنقاط لا نسبة من نسبة
                item["change_unit"] = "points"
            else:
                item["change_pct"] = _pct(dc - dp, dp) if dp != 0 else None
        else:
            item["reason_ar"] = "لا توجد بيانات للفترة السابقة"
            item["reason_en"] = "No data for the previous period"
        out[code] = item
    return out


def _product_insights(rows, prev_rows, currency, total):
    full = _group(rows, "product_sku", currency, prev_rows, total=total)
    items = full["items"]
    comparable = [i for i in items if i["growth_pct"] is not None]
    growing = sorted([i for i in comparable if i["growth_pct"] > 0], key=lambda x: -x["growth_pct"])[:5]
    declining = sorted([i for i in comparable if i["growth_pct"] < 0], key=lambda x: x["growth_pct"])[:5]
    out = {"available": full["available"], "top5": items[:5], "growing": growing, "declining": declining,
           "products_count": full["count"], "missing_records": full["missing_records"],
           "comparison_available": bool(comparable), "currency": currency}
    if full["available"] and not comparable:
        out["note_ar"] = "لا توجد مبيعات للمنتجات في الفترة السابقة — لا يمكن تحديد النامي والمتراجع"
        out["note_en"] = "No previous-period product sales — growing/declining cannot be determined"
    return out


def _completeness(rows):
    """نسبة السجلات الناقصة لكل بُعد — تدخل في الثقة ولا تُحوَّل لصفر."""
    n = len(rows)
    out = {}
    for field in ("date", "branch_name", "reference", "channel", "category", "product_sku"):
        miss = sum(1 for r in rows if r.get(field) in (None, ""))
        out[field] = {"missing": miss, "missing_pct": _pct(Decimal(miss), Decimal(n)) if n else None}
    return {"records": n, "fields": out}


def _forecast(all_rows, currency):
    """توقع الشهر القادم لصافي المبيعات بنفس محرك التوقع المعتمد (2.3) — لا منطق جديد."""
    series = {}
    for r in all_rows:
        ym = parse_period(period_key(r.get("date"), "month"))
        if ym is None:
            continue
        cell = series.setdefault(ym, {"sales": D0})
        cell["sales"] += _net([r])
    fc = forecast_metric(series, "sales", currency=currency)
    fc["actual_series"] = [{"period": f"{y:04d}-{m:02d}", "net_sales": _money(v["sales"])}
                           for (y, m), v in sorted(series.items())]
    fc["grain"] = "month"
    return fc


def _day(v):
    k = period_key(v, "day")
    return k if k else None


def _promotions(rows, all_rows, currency):
    """قبل/أثناء/بعد لكل حملة بنوافذ أيام متساوية. علاقة لا سببية."""
    promo_rows = [r for r in rows if r.get("promotion") not in (None, "")]
    if not promo_rows:
        return {"available": False,
                "reason_ar": "لا توجد بيانات حملات — أضف عمود «الحملة» في ملف المبيعات لتفعيل هذا التحليل",
                "reason_en": "No promotion data — add a 'promotion' column to the sales file to enable this"}
    from datetime import date, timedelta
    daily = {}
    for r in all_rows:
        d = _day(r.get("date"))
        if d and r.get("promotion") in (None, ""):
            daily[d] = daily.get(d, D0) + _net([r])
    out = []
    groups = {}
    for r in promo_rows:
        groups.setdefault(str(r["promotion"]), []).append(r)
    for name, rs in groups.items():
        days = sorted({_day(r.get("date")) for r in rs if _day(r.get("date"))})
        a = _agg(rs)
        item = {"promotion": name, "days": len(days), "during": _money(a["net"]),
                "discount_cost": _money(a["disc"]), "start": days[0] if days else None,
                "end": days[-1] if days else None, "currency": currency}
        if days:
            s0, e0, n = date.fromisoformat(days[0]), date.fromisoformat(days[-1]), len(days)
            span = (e0 - s0).days + 1
            before = [(s0 - timedelta(days=i)).isoformat() for i in range(1, span + 1)]
            after = [(e0 + timedelta(days=i)).isoformat() for i in range(1, span + 1)]
            for label, win in (("before", before), ("after", after)):
                have = [daily[x] for x in win if x in daily]
                item[label] = _money(sum(have, D0)) if have else None
                item[label + "_days_with_data"] = len(have)
            item["during_daily_avg"] = _money(safe_divide(a["net"], Decimal(n)))
        out.append(item)
    out.sort(key=lambda x: -(x["during"]["value"] or 0))
    return {"available": True, "items": out,
            "note_ar": "المقارنة قبل/أثناء/بعد تُظهر العلاقة فقط — لا تثبت أن الحملة سبب التغيّر.",
            "note_en": "Before/during/after shows the relationship only — it does not prove the promotion caused it."}


def _trend(rows, grain, currency):
    buckets = {}
    undated = 0
    for r in rows:
        k = period_key(r.get("date"), grain)
        if not k:
            undated += 1
            continue
        buckets.setdefault(k, []).append(r)
    series = [{"period": k, "net_sales": _money(_net(v)), "transactions": len(_agg(v)["tx"]) or None}
              for k, v in sorted(buckets.items())]
    return {"grain": grain, "series": series, "undated_records": undated, "currency": currency,
            "available": bool(series)}


def _comparison(all_rows, current, grain, currency):
    """المقارنة بالفترة السابقة وبنفس الفترة من العام الماضي — فقط إن وُجدت البيانات."""
    by = {}
    for r in all_rows:
        k = period_key(r.get("date"), grain)
        if k:
            by.setdefault(k, []).append(r)
    prev_k, ly_k = previous_key(current, grain), same_period_last_year(current, grain)
    cur_net = _net(by.get(current, []))
    out = {"current_period": current, "current": _money(cur_net), "currency": currency}
    for label, k in (("previous", prev_k), ("same_period_last_year", ly_k)):
        if k in by:
            p = _net(by[k])
            out[label] = {"period": k, **_money(p), "growth_pct": _pct(cur_net - p, p) if p else None}
        else:
            out[label] = {"period": k, "available": False,
                          "reason_ar": f"لا توجد بيانات للفترة {k}", "reason_en": f"No data for {k}"}
    return out


def _signals(overview, branches, products, channels, comparison, period, currency, categories=None):
    """إشارات بنفس عقد Phase 2.3 ليستهلكها الذكاء التنفيذي."""
    import hashlib
    from datetime import datetime, timezone
    sigs = []

    def add(kind, code, ar, en, *, severity, metric, current, reference, ref_type, method,
            evidence, impact=None, action_ar="", action_en="", dim=None):
        sigs.append({
            "id": hashlib.sha1(f"sales|{code}|{dim}|{period}".encode()).hexdigest()[:12],
            "type": kind, "code": code, "source_module": "sales",
            "name_ar": ar, "name_en": en, "severity": severity, "metric_id": metric,
            "current_value": current, "reference_value": reference, "reference_type": ref_type,
            "method": method, "evidence": evidence, "dimension": dim,
            "estimated_impact": (None if impact is None else {**_money(impact), "currency": currency,
                                                              "is_estimate": True,
                                                              "formula": "current − previous net sales"}),
            "period": period, "source": "companysale", "status": "open",
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "suggested_action_ar": action_ar, "suggested_action_en": action_en,
            "confidence": "high" if comparison.get("previous", {}).get("value") else "low"})

    prev = comparison.get("previous", {})
    if prev.get("value") and prev.get("growth_pct") is not None:
        g = prev["growth_pct"]
        if g <= -10:
            add("risk", "sales_decline", "تراجع المبيعات", "Sales decline", severity="high" if g <= -20 else "medium",
                metric="net_sales", current=comparison["current"]["value"], reference=prev["value"],
                ref_type="previous_period", method="نمو صافي المبيعات ≤ −10%",
                evidence=[f"صافي المبيعات: {prev['value']} ← {comparison['current']['value']}"],
                impact=to_decimal(comparison["current"]["value"]) - to_decimal(prev["value"]),
                action_ar="افحص الفروع والمنتجات الأكثر تراجعاً", action_en="Inspect the most declining branches and products")
        elif g >= 10:
            add("opportunity", "sales_growth", "نمو المبيعات", "Sales growth", severity="low",
                metric="net_sales", current=comparison["current"]["value"], reference=prev["value"],
                ref_type="previous_period", method="نمو صافي المبيعات ≥ +10%",
                evidence=[f"نمو {g}% عن الفترة السابقة"],
                impact=to_decimal(comparison["current"]["value"]) - to_decimal(prev["value"]),
                action_ar="وسّع ما يقود النمو", action_en="Scale what drives the growth")
    for b in branches.get("items", []):
        if b.get("growth_pct") is not None and b["growth_pct"] <= -15:
            add("risk", "branch_sales_decline", "تراجع مبيعات فرع", "Branch sales decline", severity="medium",
                metric="net_sales", current=b["net_sales"]["value"], reference=None, ref_type="previous_period",
                method="تراجع مبيعات الفرع ≥ 15%", dim=b["key"],
                evidence=[f"{b['key']}: تغيّر {b['growth_pct']}%",
                          f"الفواتير: {b.get('transactions') or '—'}"],
                action_ar=f"راجع تشغيل {b['key']} هذا الأسبوع", action_en=f"Review {b['key']} operations this week")
    for p in products.get("items", [])[:10]:
        if p.get("growth_pct") is not None and p["growth_pct"] >= 25:
            add("opportunity", "product_growth", "منتج ينمو بقوة", "Fast-growing product", severity="low",
                metric="net_sales", current=p["net_sales"]["value"], reference=None, ref_type="previous_period",
                method="نمو مبيعات المنتج ≥ 25%", dim=p["key"],
                evidence=[f"{p['key']}: نمو {p['growth_pct']}%"],
                action_ar="تأكد من توفر المخزون وادعم المنتج", action_en="Secure stock and push this product")
        elif p.get("growth_pct") is not None and p["growth_pct"] <= -20:
            add("risk", "product_decline", "تراجع منتج", "Declining product", severity="medium",
                metric="net_sales", current=p["net_sales"]["value"], reference=None, ref_type="previous_period",
                method="تراجع مبيعات المنتج ≥ 20%", dim=p["key"],
                evidence=[f"{p['key']}: تغيّر {p['growth_pct']}%"],
                action_ar="راجع السعر والتوفر والعرض", action_en="Review price, availability and placement")
    dr = overview["discount_rate"].get("value")
    if dr is not None and dr >= 10:
        add("risk", "high_discounts", "ارتفاع الخصومات", "High discounts", severity="medium",
            metric="discount_pct", current=dr, reference=10, ref_type="threshold",
            method="نسبة الخصم ≥ 10% من إجمالي المبيعات",
            evidence=[f"الخصومات {overview['discounts'].get('value')} من إجمالي {overview['gross_sales'].get('value')}"],
            action_ar="راجع سياسة الخصم وأثرها على الهامش", action_en="Review the discount policy and its margin impact")
    rr = overview["return_rate"].get("value")
    if rr is not None and rr >= 5:
        add("risk", "high_returns", "ارتفاع المرتجعات", "High returns", severity="medium",
            metric="return_pct", current=rr, reference=5, ref_type="threshold",
            method="نسبة المرتجعات ≥ 5%",
            evidence=[f"المرتجعات {overview['returns'].get('value')}"],
            action_ar="حدد المنتجات والفروع الأكثر إرجاعاً", action_en="Identify the most-returned products and branches")
    recs = overview.get("records") or 0
    for dim_data, code, ar, en, col_ar, col_en in (
            (channels, "missing_channel_data", "بيانات القناة ناقصة", "Missing channel data", "قناة البيع", "sales channel"),
            (categories or {}, "missing_category_data", "بيانات الفئة ناقصة", "Missing category data", "الفئة", "category")):
        miss = dim_data.get("missing_records")
        if miss:
            share = _pct(Decimal(miss), Decimal(recs)) if recs else None
            add("data_quality", code, ar, en, severity="low",
                metric="data_quality", current=miss, reference=0, ref_type="expected_complete",
                method=f"سجلات بلا {col_ar}",
                evidence=[f"{share}% من سجلات المبيعات لا تحتوي {col_ar} ({miss} من {recs})" if share is not None
                          else dim_data.get("note_ar", "")],
                action_ar=f"أضف عمود {col_ar} في ملف المبيعات", action_en=f"Add a {col_en} column to the sales file")
    order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    sigs.sort(key=lambda x: -order.get(x["severity"], 0))
    return sigs


def analyze_sales(rows, all_rows=None, *, period=None, grain="month", currency="SAR",
                  prev_rows=None, quality=None):
    """المدخل الرئيسي. rows = سجلات الفترة الحالية (مفلترة مسبقاً بالشركة والصلاحية)."""
    if grain not in GRAINS:
        grain = "month"
    all_rows = all_rows if all_rows is not None else list(rows)
    if not rows:
        return {"has_data": False, "period": period, "currency": currency,
                "message_ar": "لا توجد بيانات مبيعات تفصيلية لهذه الفترة. ارفع ملف المبيعات من مركز البيانات.",
                "message_en": "No detailed sales data for this period. Upload a sales file from the Data Center.",
                "version": f"sales-v{SALES_VERSION}"}
    period = period or (sorted({period_key(r.get("date"), grain) for r in rows if period_key(r.get("date"), grain)}) or [None])[-1]
    if prev_rows is None:
        pk = previous_key(period, grain)
        prev_rows = [r for r in all_rows if period_key(r.get("date"), grain) == pk]
    overview = _overview(rows, currency, period)
    total_net = to_decimal(overview["net_sales"]["value_decimal"])
    branches = _group(rows, "branch_name", currency, prev_rows, total=total_net)
    channels = _group(rows, "channel", currency, prev_rows, total=total_net)
    products = _group(rows, "product_sku", currency, prev_rows, limit=20, total=total_net)
    categories = _group(rows, "category", currency, prev_rows, total=total_net)
    comparison = _comparison(all_rows, period, grain, currency)
    trend = _trend(all_rows, grain, currency)
    a = _agg(rows)
    discounted = [r for r in rows if _d(r.get("discounts")) > 0]
    returns_rows = [r for r in rows if _d(r.get("returns")) > 0]
    discounts = {"gross_sales": _money(a["gross"]), "discounts": _money(a["disc"]),
                 "discount_rate_pct": overview["discount_rate"]["value"],
                 "sales_with_discount": _money(_net(discounted)),
                 "sales_without_discount": _money(_net([r for r in rows if _d(r.get("discounts")) == 0])),
                 "records_with_discount": len(discounted), "currency": currency,
                 "note_ar": "العلاقة معروضة كما هي — ارتفاع المبيعات مع الخصم لا يثبت أن الخصم هو السبب.",
                 "note_en": "The relationship is shown as-is — higher sales with discounts does not prove causation."}
    discounts["by_branch"] = _rank_discounts(rows, "branch_name", currency)
    discounts["by_product"] = _rank_discounts(rows, "product_sku", currency)
    discounts["trend"] = _series_by_period(all_rows, grain, "discounts", currency)
    returns = {"return_value": _money(a["ret"]), "return_rate_pct": overview["return_rate"]["value"],
               "returned_records": len(returns_rows), "currency": currency,
               "by_branch": _rank_returns(rows, "branch_name", currency),
               "by_product": _rank_returns(rows, "product_sku", currency),
               "trend": _series_by_period(all_rows, grain, "returns", currency)}
    prev_overview = _overview(prev_rows, currency, previous_key(period, grain)) if prev_rows else None
    kpis = _kpis(overview, prev_overview)
    product_insights = _product_insights(rows, prev_rows, currency, total_net)
    forecast = _forecast(all_rows, currency)
    promotions = _promotions(rows, all_rows, currency)
    completeness = _completeness(rows)
    signals = _signals(overview, branches, products, channels, comparison, period, currency, categories)
    return {"has_data": True, "version": f"sales-v{SALES_VERSION}", "period": period, "grain": grain,
            "currency": currency, "overview": overview, "trend": trend, "comparison": comparison,
            "branches": branches, "channels": channels, "products": products, "categories": categories,
            "discounts": discounts, "returns": returns, "signals": signals,
            "kpis": kpis, "product_insights": product_insights, "forecast": forecast,
            "promotions": promotions, "completeness": completeness,
            "data_quality": quality or {"status": "unknown"},
            "source": {"table": "companysale", "records": overview["records"]}}
