"""
NABBAH 2.6 — Inventory Intelligence engine.
Pure & deterministic: no DB, no AI. AI only explains what this returns (principle 1).
Definitions come from metric_registry (principle 7):
  inventory_turnover = cogs ÷ average_inventory_value
  dio                = average_inventory_value ÷ (cogs ÷ days)
  stockout_rate      = (stockout_items ÷ items) × 100
Missing data is never zero (principle 2): each section says what is missing and how to add it.
Relationships, not causation (principle 3): "sales fell while out of stock", never "because".
"""
import os, sys, hashlib, calendar
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING

_here = os.path.dirname(os.path.abspath(__file__))
for _d in ("../phase21", "../phase22", "../phase23", "../phase24", "../phase25"):
    _p = os.path.join(_here, _d)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from nabbah_finance import to_decimal, round_money, round_pct, safe_divide
from metric_registry import get_metric

INVENTORY_VERSION = "1.0"
D0 = Decimal("0")
# قواعد التصنيف — ثابتة ومعلنة (تظهر للمستخدم) وليست ألواناً تجميلية
RULES = {"slow_days": 60, "obsolete_days": 180, "low_cover_days": 7, "watch_cover_days": 14,
         "watch_rop_factor": Decimal("1.5"), "window_months": 3, "demand_days": 90}
AGING = ((0, 30, "0–30"), (31, 60, "31–60"), (61, 90, "61–90"), (91, 180, "91–180"), (181, 10 ** 6, "180+"))
STATUS_ORDER = ("stockout", "obsolete", "slow", "low", "watch", "healthy")


def _d(v):
    return to_decimal(v) if v not in (None, "") else None


def _money(v):
    return None if v is None else {"value": float(round_money(v)), "value_decimal": str(round_money(v))}


def _num(v, places=2):
    return None if v is None else float(round_pct(v, places))


def _pct(n, d):
    r = safe_divide(n, d)
    return None if r is None else float(round_pct(r * 100))


def _month_end(p):
    y, m = int(p[:4]), int(p[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _days_in(p):
    return calendar.monthrange(int(p[:4]), int(p[5:7]))[1]


def _prev_periods(periods, cur, n):
    """آخر n فترات متاحة حتى cur (بما فيها)."""
    idx = periods.index(cur)
    return periods[max(0, idx - n + 1): idx + 1]


def _sid(*parts):
    return "inv-" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _metric(code):
    m = get_metric(code) or {}
    return {"metric_code": code, "name_ar": m.get("name_ar"), "name_en": m.get("name_en"),
            "formula": m.get("formula"), "unit": m.get("unit")}


def _unit_cost(snap, products):
    """تكلفة الوحدة: من قيمة المخزون ÷ الكمية في الملف، وإلا من تكلفة المنتج. غير ذلك None (لا تخمين)."""
    q, v = _d(snap.get("closing_qty")), _d(snap.get("closing_value"))
    if q and v is not None and q > 0:
        return v / q, "file"
    q, v = _d(snap.get("opening_qty")), _d(snap.get("opening_value"))
    if q and v is not None and q > 0:
        return v / q, "file"
    pr = products.get(snap.get("product_sku")) or {}
    c = _d(pr.get("cost"))
    if c is None:
        return None, None
    return c, ("derived" if pr.get("_derived") else "product_cost")


def _value(snap, products):
    v = _d(snap.get("closing_value"))
    if v is not None:
        return v
    q = _d(snap.get("closing_qty"))
    c, _ = _unit_cost(snap, products)
    return q * c if q is not None and c is not None else None


def analyze_inventory(snapshots, sales_rows=None, products=None, params=None, *, period=None,
                      status_filter="", currency="SAR"):
    """snapshots: [{period, branch_name, product_sku, opening_qty, opening_value, purchases_qty, sold_qty,
                    adjustments_qty, closing_qty, closing_value}]
       sales_rows: [{date, branch_name, product_sku, quantity, category}] — تربط المبيعات بالمخزون (2.5 → 2.6)
       products: {sku: {name, category, cost, unit}}
       params: {(sku, branch_name|None): {lead_time_days, safety_stock, min_order_qty, reorder_point}}"""
    products, params, sales_rows = products or {}, params or {}, sales_rows or []
    snaps = [s for s in snapshots if s.get("period") and s.get("product_sku")]
    if not snaps:
        return {"has_data": False, "version": f"inventory-v{INVENTORY_VERSION}",
                "message_ar": "لا توجد بيانات مخزون بعد. ارفع ملف المخزون من مركز البيانات، أو أدخل الأرصدة يدوياً.",
                "message_en": "No inventory data yet. Upload an inventory file or enter stock manually.",
                "required": _required_data()}
    # تكلفة مشتقة: متوسط (القيمة ÷ الكمية) لنفس الصنف من أرصدة أخرى — حتى لا يُسقط صنفٌ نافد حساب الدوران كله
    products = {k: dict(v) for k, v in products.items()}
    acc = {}
    for s in snaps:
        q, v = _d(s.get("closing_qty")), _d(s.get("closing_value"))
        if q and v is not None and q > 0:
            a = acc.setdefault(s["product_sku"], [D0, D0]); a[0] += v; a[1] += q
    for sku, (v, q) in acc.items():
        pr = products.setdefault(sku, {})
        if _d(pr.get("cost")) is None:
            pr["cost"], pr["_derived"] = v / q, True
    periods = sorted({s["period"] for s in snaps})
    cur = period if period in periods else periods[-1]
    prev = periods[periods.index(cur) - 1] if periods.index(cur) > 0 else None
    as_of = _month_end(cur)

    def cat_of(sku):
        c = (products.get(sku) or {}).get("category")
        if c:
            return c
        for r in sales_rows:
            if r.get("product_sku") == sku and r.get("category"):
                return r["category"]
        return "غير مصنّف"

    cats = {sku: cat_of(sku) for sku in {s["product_sku"] for s in snaps}}
    by_period = {}
    for s in snaps:
        by_period.setdefault(s["period"], []).append(s)

    # ── المبيعات كاستهلاك: آخر حركة ومتوسط الطلب لكل صنف/فرع
    last_sale, demand_qty = {}, {}
    dem_start = date.fromordinal(as_of.toordinal() - RULES["demand_days"] + 1)
    sales_by_key_month = {}
    for r in sales_rows:
        d = str(r.get("date") or "")[:10]
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        if dd > as_of:
            continue
        k = (r.get("product_sku"), r.get("branch_name"))
        q = _d(r.get("quantity")) or Decimal(1)
        if q > 0:
            last_sale[k] = max(last_sale.get(k, dd), dd)
        if dd >= dem_start:
            demand_qty[k] = demand_qty.get(k, D0) + q
        mk = (k, d[:7])
        sales_by_key_month[mk] = sales_by_key_month.get(mk, D0) + q
    have_sales = bool(sales_rows)

    def consumption(s):
        """الاستهلاك: الكمية المباعة في ملف المخزون، وإلا من المبيعات التفصيلية لنفس الصنف/الفرع/الشهر."""
        q = _d(s.get("sold_qty"))
        if q is not None:
            return q, "inventory_file"
        q = sales_by_key_month.get(((s["product_sku"], s.get("branch_name")), s["period"]))
        return (q, "sales_2_5") if q is not None else (None, None)

    # ── آخر حركة من ملف المخزون (شهرية) عند غياب المبيعات التفصيلية
    last_move_snap, first_seen = {}, {}
    for p in periods:
        if p > cur:
            break
        for s in by_period[p]:
            k = (s["product_sku"], s.get("branch_name"))
            first_seen.setdefault(k, date(int(p[:4]), int(p[5:7]), 1))
            c, _ = consumption(s)
            if c and c > 0:
                last_move_snap[k] = _month_end(p)

    # ── المراكز الحالية
    positions, missing_cost = [], set()
    for s in by_period[cur]:
        sku, br = s["product_sku"], s.get("branch_name")
        k = (sku, br)
        qty = _d(s.get("closing_qty"))
        uc, uc_src = _unit_cost(s, products)
        if uc is None:
            missing_cost.add(sku)
        val = _value(s, products)
        lm = last_sale.get(k) or last_move_snap.get(k)
        days_since = (as_of - lm).days if lm else ((as_of - first_seen[k]).days if k in first_seen else None)
        dq = demand_qty.get(k)
        if dq is None and not have_sales:
            win = _prev_periods(periods, cur, RULES["window_months"])
            vals = [consumption(x)[0] for p in win for x in by_period[p] if (x["product_sku"], x.get("branch_name")) == k]
            vals = [v for v in vals if v is not None]
            dq = sum(vals, D0) if vals else None
            days = sum(_days_in(p) for p in win)
        else:
            days = RULES["demand_days"]
        avg_daily = safe_divide(dq, Decimal(days)) if dq is not None else None
        cover = safe_divide(qty, avg_daily) if (qty is not None and avg_daily) else None
        prm = params.get((sku, br)) or params.get((sku, None)) or {}
        lt, ss = _d(prm.get("lead_time_days")), _d(prm.get("safety_stock"))
        moq, rop_given = _d(prm.get("min_order_qty")), _d(prm.get("reorder_point"))
        rop, rop_src, rop_reason = None, None, None
        if rop_given is not None:
            rop, rop_src = rop_given, "manual"
        elif lt is not None and avg_daily is not None:
            rop, rop_src = avg_daily * lt + (ss or D0), "calculated"
        else:
            rop_reason = ("لا توجد مدة توريد (Lead time) لهذا الصنف" if lt is None
                          else "لا يوجد طلب تاريخي كافٍ لحساب متوسط الاستهلاك")
        # الحالة — قواعد صريحة
        if qty is not None and qty <= 0:
            status = "stockout"
        elif qty and days_since is not None and days_since >= RULES["obsolete_days"]:
            status = "obsolete"
        elif qty and days_since is not None and days_since >= RULES["slow_days"]:
            status = "slow"
        elif rop is not None and qty is not None and qty <= rop:
            status = "low"
        elif rop is None and cover is not None and cover <= RULES["low_cover_days"]:
            status = "low"
        elif rop is not None and qty is not None and qty <= rop * RULES["watch_rop_factor"]:
            status = "watch"
        elif rop is None and cover is not None and cover <= RULES["watch_cover_days"]:
            status = "watch"
        else:
            status = "healthy"
        suggest, suggest_reason = None, None
        if status in ("low", "stockout"):
            need = [n for n, v in (("مدة التوريد", lt), ("مخزون الأمان", ss), ("الحد الأدنى للطلب", moq)) if v is None]
            if avg_daily is None:
                need.append("الطلب التاريخي")
            if need:
                suggest_reason = "لا يمكن حساب الكمية المقترحة — ينقص: " + "، ".join(need)
            else:
                raw = max(rop + avg_daily * lt - (qty or D0), D0)
                suggest = (raw / moq).to_integral_value(rounding=ROUND_CEILING) * moq if moq > 0 else raw
        positions.append({
            "product_sku": sku, "name": (products.get(sku) or {}).get("name") or sku, "branch": br,
            "category": cats.get(sku), "qty": _num(qty), "unit_cost": _money(uc), "cost_source": uc_src,
            "value": _money(val), "last_movement": lm.isoformat() if lm else None, "days_since_movement": days_since,
            "avg_daily_demand": _num(avg_daily, 3), "coverage_days": _num(cover, 1),
            "reorder_point": _num(rop), "reorder_point_source": rop_src, "reorder_point_reason": rop_reason,
            "lead_time_days": _num(lt), "safety_stock": _num(ss), "min_order_qty": _num(moq),
            "suggested_qty": _num(suggest), "suggested_reason": suggest_reason, "status": status,
            "_v": val or D0, "_q": qty})

    # ── التجميع العام
    def agg_turnover(filter_fn, end_period):
        win = _prev_periods(periods, end_period, RULES["window_months"])
        cogs, cogs_ok, values, days = D0, True, [], 0
        for p in win:
            pv = D0
            has_v = False
            for s in by_period[p]:
                if not filter_fn(s):
                    continue
                v = _value(s, products)
                if v is not None:
                    pv += v; has_v = True
                c, _ = consumption(s)
                uc, _ = _unit_cost(s, products)
                if c is not None and uc is not None:
                    cogs += c * uc
                elif c is not None:
                    cogs_ok = False
            if has_v:
                values.append(pv)
            days += _days_in(p)
        avg = safe_divide(sum(values, D0), Decimal(len(values))) if values else None
        turnover = safe_divide(cogs, avg) if cogs_ok and avg else None
        dio = safe_divide(avg, safe_divide(cogs, Decimal(days))) if cogs_ok and avg and cogs > 0 else None
        return {"turnover": turnover, "dio": dio, "cogs": cogs if cogs_ok else None, "avg_value": avg,
                "window": f"{win[0]} → {win[-1]}", "months": len(win)}

    everything = lambda s: True
    t_cur = agg_turnover(everything, cur)
    t_prev = agg_turnover(everything, prev) if prev else None

    def stock_totals(p, filt=everything):
        val, qty, items, outs, vmiss = D0, D0, 0, 0, 0
        for s in by_period.get(p, []):
            if not filt(s):
                continue
            items += 1
            q = _d(s.get("closing_qty"))
            if q is not None:
                qty += q
                if q <= 0:
                    outs += 1
            v = _value(s, products)
            if v is None:
                vmiss += 1
            else:
                val += v
        return {"value": val, "qty": qty, "items": items, "stockouts": outs, "value_missing": vmiss}

    tot, tot_prev = stock_totals(cur), (stock_totals(prev) if prev else None)
    slow_v = sum((p["_v"] for p in positions if p["status"] == "slow"), D0)
    obs_v = sum((p["_v"] for p in positions if p["status"] == "obsolete"), D0)

    def kpi(code, cur_v, prev_v, unit, **extra):
        item = dict(_metric(code) if get_metric(code) else {"metric_code": code}, unit=unit,
                    current=cur_v, previous=prev_v, comparison_available=cur_v is not None and prev_v is not None,
                    change=None, **extra)
        if item["comparison_available"]:
            item["change"] = round(cur_v - prev_v, 2)
        return item

    so_rate = _pct(Decimal(tot["stockouts"]), Decimal(tot["items"])) if tot["items"] else None
    so_prev = (_pct(Decimal(tot_prev["stockouts"]), Decimal(tot_prev["items"]))
               if tot_prev and tot_prev["items"] else None)
    kpis = {
        "inventory_value": kpi("inventory_value", float(round_money(tot["value"])),
                               float(round_money(tot_prev["value"])) if tot_prev else None, "currency",
                               period=cur, valuation=("قيمة ملف المخزون، وإلا الكمية × تكلفة المنتج"),
                               incomplete_items=tot["value_missing"]),
        "inventory_turnover": kpi("inventory_turnover", _num(t_cur["turnover"]),
                                  _num(t_prev["turnover"]) if t_prev else None, "ratio", window=t_cur["window"],
                                  reason_ar=None if t_cur["turnover"] is not None else _why_turnover(t_cur, missing_cost)),
        "dio": kpi("dio", _num(t_cur["dio"], 0), _num(t_prev["dio"], 0) if t_prev else None, "days",
                   window=t_cur["window"],
                   reason_ar=None if t_cur["dio"] is not None else _why_turnover(t_cur, missing_cost)),
        "stockout_rate": kpi("stockout_rate", so_rate, so_prev, "percent", stockout_items=tot["stockouts"],
                             items=tot["items"]),
        "slow_moving_value": kpi("slow_moving_value", float(round_money(slow_v)), None, "currency",
                                 rule=f"بلا حركة {RULES['slow_days']}–{RULES['obsolete_days'] - 1} يوماً"),
        "obsolete_value": kpi("obsolete_value", float(round_money(obs_v)), None, "currency",
                              rule=f"بلا حركة {RULES['obsolete_days']} يوماً فأكثر"),
        "inventory_accuracy": {"metric_code": "inventory_accuracy", "current": None, "available": False,
                               "reason_ar": "غير متاح — يحتاج بيانات جرد فعلي (الكمية المعدودة مقابل كمية النظام)",
                               "reason_en": "Not available — needs physical count data"},
    }

    # ── حسب الفرع والفئة
    def dim(key_fn, filt_of):
        keys = sorted({key_fn(s) for s in by_period[cur] if key_fn(s)})
        out = []
        for k in keys:
            f = filt_of(k)
            st = stock_totals(cur, f)
            tv = agg_turnover(f, cur)
            ps = [p for p in positions if (p["branch"] == k if key_fn is _br else p["category"] == k)]
            out.append({"key": k, "value": _money(st["value"]), "qty": _num(st["qty"]), "items": st["items"],
                        "share_pct": _pct(st["value"], tot["value"]) if tot["value"] else None,
                        "turnover": _num(tv["turnover"]), "dio": _num(tv["dio"], 0),
                        "stockout_rate": _pct(Decimal(st["stockouts"]), Decimal(st["items"])) if st["items"] else None,
                        "slow_value": _money(sum((p["_v"] for p in ps if p["status"] == "slow"), D0)),
                        "obsolete_value": _money(sum((p["_v"] for p in ps if p["status"] == "obsolete"), D0))})
        out.sort(key=lambda x: -(x["value"]["value"] if x["value"] else 0))
        return out

    by_branch = dim(_br, lambda k: (lambda s: s.get("branch_name") == k))
    by_category = dim(lambda s: cats.get(s["product_sku"]), lambda k: (lambda s: cats.get(s["product_sku"]) == k))

    # ── الحركة (الفترة الحالية): أول المدة + المشتريات − المبيعات ± التسويات = آخر المدة
    mv = {f: D0 for f in ("opening_qty", "purchases_qty", "sold_qty", "adjustments_qty", "closing_qty")}
    present = {f: False for f in mv}
    for s in by_period[cur]:
        for f in mv:
            v = _d(s.get(f))
            if f == "sold_qty" and v is None:
                v, _ = consumption(s)
            if v is not None:
                mv[f] += v; present[f] = True
    gap = None
    if all(present[f] for f in ("opening_qty", "purchases_qty", "sold_qty", "closing_qty")):
        gap = mv["opening_qty"] + mv["purchases_qty"] - mv["sold_qty"] + mv["adjustments_qty"] - mv["closing_qty"]
    movement = {"period": cur, **{f: (_num(mv[f]) if present[f] else None) for f in mv},
                "reconciliation_gap": _num(gap),
                "note_ar": None if gap is None else ("الحركة متوازنة" if gap == 0 else
                                                     "الحركة غير متوازنة: أول المدة + المشتريات − المبيعات ± التسويات ≠ آخر المدة")}
    timeline = [{"period": p, "value": _money(stock_totals(p)["value"]), "qty": _num(stock_totals(p)["qty"])}
                for p in periods]

    # ── التقادم (حسب أيام آخر حركة)
    aging = []
    for lo, hi, label in AGING:
        ps = [p for p in positions if p["_q"] and p["_q"] > 0 and p["days_since_movement"] is not None
              and lo <= p["days_since_movement"] <= hi]
        aging.append({"bucket": label, "value": _money(sum((p["_v"] for p in ps), D0)), "items": len(ps)})

    # ── النفاد: عدد المرات وآخر مرة + علاقته بالمبيعات
    so_hist = {}
    for p in periods:
        if p > cur:
            break
        for s in by_period[p]:
            q = _d(s.get("closing_qty"))
            if q is not None and q <= 0:
                k = (s["product_sku"], s.get("branch_name"))
                e = so_hist.setdefault(k, {"events": 0, "last": None})
                e["events"] += 1; e["last"] = p
    stockouts = []
    for p in positions:
        k = (p["product_sku"], p["branch"])
        if k not in so_hist:
            continue
        cur_s = sales_by_key_month.get((k, cur))
        prev_s = sales_by_key_month.get((k, prev)) if prev else None
        impact = None
        if p["status"] == "stockout" and cur_s is not None and prev_s:
            ch = _pct(cur_s - prev_s, prev_s)
            if ch is not None and ch < 0:
                impact = {"sales_qty_prev": _num(prev_s), "sales_qty_cur": _num(cur_s), "change_pct": ch,
                          "note_ar": "انخفضت مبيعات الصنف في نفس الفترة التي نفد فيها — علاقة زمنية لا تُثبت السبب"}
        stockouts.append({"product_sku": p["product_sku"], "name": p["name"], "branch": p["branch"],
                          "events": so_hist[k]["events"], "last_stockout": so_hist[k]["last"],
                          "out_now": p["status"] == "stockout", "sales_impact": impact})
    stockouts.sort(key=lambda x: (not x["out_now"], -x["events"]))

    status_counts = {st: sum(1 for p in positions if p["status"] == st) for st in STATUS_ORDER}
    status_values = {st: _money(sum((p["_v"] for p in positions if p["status"] == st), D0)) for st in STATUS_ORDER}
    reorder = [p for p in positions if p["status"] in ("low", "stockout", "watch")]
    reorder.sort(key=lambda p: (STATUS_ORDER.index(p["status"]), p["coverage_days"] if p["coverage_days"] is not None else 1e9))
    slow_list = sorted([p for p in positions if p["status"] in ("slow", "obsolete")], key=lambda p: -p["_v"])

    signals = _signals(positions, by_category, t_cur, tot, missing_cost, movement, stockouts, cur, currency)
    shown = [p for p in positions if not status_filter or p["status"] == status_filter]
    shown.sort(key=lambda p: -p["_v"])
    clean = lambda lst: [{k: v for k, v in p.items() if not k.startswith("_")} for p in lst]
    have_lt = sum(1 for p in positions if p["lead_time_days"] is not None or p["reorder_point_source"] == "manual")
    availability = {
        "valuation": {"available": not missing_cost, "missing_products": sorted(missing_cost)[:50],
                      "how_ar": "أضف «قيمة آخر المدة» في ملف المخزون، أو «التكلفة» في ملف المنتجات، أو أدخلها يدوياً"},
        "turnover": {"available": t_cur["turnover"] is not None,
                     "how_ar": "يحتاج الكمية المباعة (من ملف المخزون أو المبيعات) وتكلفة الوحدة"},
        "reorder": {"available": have_lt > 0, "products_with_lead_time": have_lt, "products": len(positions),
                    "how_ar": "أدخل مدة التوريد ومخزون الأمان والحد الأدنى للطلب لكل صنف (إدخال يدوي أو ملف)"},
        "accuracy": {"available": False, "how_ar": "يحتاج جرداً فعلياً (الكمية المعدودة)"},
        "sales_link": {"available": have_sales, "how_ar": "ارفع ملف المبيعات التفصيلية لربط النفاد بالمبيعات"},
    }
    return {"has_data": True, "version": f"inventory-v{INVENTORY_VERSION}", "period": cur, "previous_period": prev,
            "periods": periods, "currency": currency, "as_of": as_of.isoformat(), "rules": _rules_text(),
            "kpis": kpis, "positions": clean(shown[:500]), "positions_total": len(shown),
            "by_branch": by_branch, "by_category": by_category, "movement": movement, "timeline": timeline,
            "aging": aging, "aging_basis_ar": "حسب أيام آخر حركة للصنف في الفرع (بيع أو صرف)",
            "stockouts": stockouts[:100], "status_counts": status_counts, "status_values": status_values,
            "reorder": clean(reorder[:100]), "slow_obsolete": clean(slow_list[:100]),
            "signals": signals, "availability": availability,
            "turnover_detail": {"window": t_cur["window"], "cogs": _money(t_cur["cogs"]),
                                "avg_inventory_value": _money(t_cur["avg_value"])}}


def _br(s):
    return s.get("branch_name")


def _why_turnover(t, missing_cost):
    if t["avg_value"] is None:
        return "غير متاح — لا توجد قيمة للمخزون (أضف القيمة أو تكلفة الوحدة)"
    if t["cogs"] is None:
        return f"غير متاح — تكلفة الوحدة ناقصة لـ {len(missing_cost)} صنفاً"
    if t["cogs"] == 0:
        return "غير متاح — لا توجد كميات مباعة في الفترة (أضف «المباع» في ملف المخزون أو ارفع المبيعات)"
    return "غير متاح"


def _rules_text():
    return {"slow": f"بطيء الحركة: مخزون موجود بلا بيع أو صرف منذ {RULES['slow_days']} يوماً",
            "obsolete": f"متقادم: بلا حركة منذ {RULES['obsolete_days']} يوماً فأكثر",
            "low": f"منخفض: عند نقطة إعادة الطلب أو أقل، أو تغطية ≤ {RULES['low_cover_days']} أيام عند غيابها",
            "watch": f"مراقبة: حتى 1.5 × نقطة إعادة الطلب، أو تغطية ≤ {RULES['watch_cover_days']} يوماً",
            "stockout": "نافد: الكمية صفر أو أقل",
            "reorder_point": "نقطة إعادة الطلب = متوسط الطلب اليومي × مدة التوريد + مخزون الأمان",
            "demand": f"متوسط الطلب اليومي = الكمية المباعة آخر {RULES['demand_days']} يوماً ÷ {RULES['demand_days']}"}


def _required_data():
    return {"required": ["الفترة (الشهر)", "الفرع", "المنتج", "الكمية آخر المدة"],
            "recommended": ["قيمة آخر المدة أو تكلفة الوحدة", "الكمية المباعة/المصروفة", "المشتريات", "أول المدة"],
            "optional": ["مدة التوريد", "مخزون الأمان", "الحد الأدنى للطلب", "نقطة إعادة الطلب", "الجرد الفعلي"]}


def _signals(positions, by_category, t_cur, tot, missing_cost, movement, stockouts, period, currency):
    sig = []

    def add(kind, code, ar, en, sev, dimension, evidence, action_ar, impact=None):
        sig.append({"id": _sid(code, dimension, period), "type": kind, "code": code, "source_module": "inventory",
                    "name_ar": ar, "name_en": en, "severity": sev, "dimension": dimension, "period": period,
                    "evidence": evidence, "suggested_action_ar": action_ar,
                    "estimated_impact": impact, "method": "rule-based (inventory-v1.0)"})

    risky = [p for p in positions if p["status"] == "low" and p["coverage_days"] is not None]
    for p in sorted(risky, key=lambda x: x["coverage_days"])[:5]:
        add("risk", "stockout_risk", "خطر نفاد قريب", "High stockout risk", "high", f"{p['name']} — {p['branch']}",
            [f"المخزون الحالي {p['qty']}", f"متوسط الاستهلاك {p['avg_daily_demand']}/يوم",
             f"التغطية المتوقعة {p['coverage_days']} يوم"],
            "راجع إعادة الطلب لهذا الصنف في هذا الفرع")
    outs = [s for s in stockouts if s["out_now"]]
    if outs:
        imp = [s for s in outs if s["sales_impact"]]
        add("risk", "stockouts_now", "أصناف نافدة الآن", "Items out of stock", "high" if len(outs) >= 3 else "medium",
            None, [f"{len(outs)} صنفاً/فرعاً نافداً في {period}"] +
            [f"{s['name']} ({s['branch']}): المبيعات {s['sales_impact']['change_pct']}% مع النفاد" for s in imp[:3]],
            "تأكد من توفر الأصناف النافدة، خصوصاً التي تراجعت مبيعاتها مع النفاد")
    excess = sorted([p for p in positions if p["status"] in ("slow", "obsolete")], key=lambda p: -p["_v"])
    if excess and sum(p["_v"] for p in excess) > 0:
        v = sum((p["_v"] for p in excess), D0)
        add("risk", "excess_inventory", "مخزون مجمّد", "Excess inventory", "medium", None,
            [f"{float(round_money(v)):,.2f} {currency} في أصناف بطيئة أو متقادمة ({len(excess)} صنف/فرع)",
             f"أكبرها: {excess[0]['name']} في {excess[0]['branch']} ({excess[0]['days_since_movement']} يوماً بلا حركة)"],
            "راجع مستويات هذه الأصناف: تحويل لفرع آخر، عرض ترويجي، أو إيقاف الشراء", impact=_money(v))
    if t_cur["turnover"]:
        for c in by_category:
            if c["turnover"] and c["turnover"] >= float(t_cur["turnover"]) * 1.5:
                add("opportunity", "high_turnover_category", "فئة عالية الدوران", "High turnover category", "low",
                    c["key"], [f"دوران {c['turnover']}x مقابل {_num(t_cur['turnover'])}x للشركة"],
                    "فئة تتحرك بسرعة: تأكد أن مخزونها يكفي الطلب")
    if missing_cost:
        add("data_quality", "valuation_incomplete", "تقييم المخزون ناقص", "Inventory valuation incomplete", "low", None,
            [f"تكلفة الوحدة غير متوفرة لـ {len(missing_cost)} صنفاً — لا تدخل قيمتها في الإجمالي"],
            "أضف التكلفة في ملف المنتجات أو قيمة آخر المدة في ملف المخزون")
    if movement.get("reconciliation_gap") not in (None, 0, 0.0):
        add("data_quality", "movement_gap", "حركة المخزون غير متوازنة", "Stock movement does not reconcile", "medium",
            None, [f"الفرق {movement['reconciliation_gap']} وحدة في {period}"],
            "راجع أرصدة أول وآخر المدة والتسويات في ملف المخزون")
    no_lt = sum(1 for p in positions if p["reorder_point"] is None)
    if no_lt:
        add("data_quality", "missing_lead_time", "نقاط إعادة الطلب غير محسوبة", "Reorder points missing", "low", None,
            [f"{no_lt} صنف/فرع بلا مدة توريد — لا نحسب نقطة إعادة الطلب ولا الكمية المقترحة لها"],
            "أدخل مدة التوريد ومخزون الأمان يدوياً من صفحة ذكاء المخزون")
    return sig
