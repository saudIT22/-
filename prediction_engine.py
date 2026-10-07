"""
NABBAH — Phase 3.7 · Performance Prediction Intelligence (التنبؤ بالأداء)
محرك تنبؤ حتمي واحد فوق البيانات الموحّدة (المبيعات، الوحدة المالية، التدفق النقدي) + نتائج المخاطر والمسببات والقطاع.

المنهجية (موثّقة وقابلة للاختبار — predict-v1.0):
  1. سلاسل شهرية من البيانات الموحّدة. الشهر الأخير غير المكتمل يُستبعد ويُذكر. الأشهر المفقودة لا تُعامل كصفر:
     تُستخدم آخر سلسلة متصلة فقط ويُذكر الانقطاع.
  2. الموسمية (فقط مع 24 شهراً متصلة فأكثر): مؤشر موسمي ضربي لكل شهر = وسيط (القيمة ÷ المتوسط المتحرك المركزي 12 شهراً).
  3. الاتجاه: ميل Theil–Sen (وسيط الميول الزوجية — مقاوم للقيم الشاذة) على آخر 24 شهراً كحد أقصى بعد إزالة الموسمية.
     المستوى = الخط عند آخر شهر + وسيط بواقي آخر 3 أشهر (تحوّل مستمر يُلتقط، شهر استثنائي واحد يُتجاهل).
  4. التوقع للشهر h = (قيمة الخط عند آخر شهر + الميل × Σ φ^i ، φ = 0.9 تخميد يمنع الاتجاه من الانطلاق بلا حد) × المؤشر الموسمي.
  5. النطاق 80%: ± 1.28 × σ × √h ، σ = 1.4826 × الانحراف المطلق الوسيط للبواقي.
  6. الاختبار الرجعي: توقع كل شهر من آخر 6 أشهر من بيانات قبله فقط (1–3 أشهر للأمام) → MAPE.
  7. الثقة 0–100 من مكوّنات معلنة، وتنخفض 4% لكل شهر أبعد. لا ذكاء اصطناعي في أي رقم.
"""
import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from nabbah_finance import to_decimal
import risk_engine as RE

PRED_VERSION = "1.0"
MODEL = f"predict-v{PRED_VERSION}"
HORIZON = 6
MIN_MONTHS = 3
SEASON_MONTHS = 24
FIT_WINDOW = 24
DAMP = 0.9
Z80 = 1.2816
CONF_DECAY = 0.04
SUFF = [(24, "rich", "بيانات غنية (موسمية مطبّقة)"), (12, "adequate", "كافية (بلا موسمية — تحتاج 24 شهراً)"),
        (6, "limited", "محدودة"), (3, "insufficient", "بيانات تاريخية غير كافية")]
CONF_AR = {"high": "مرتفعة", "medium": "متوسطة", "low": "منخفضة"}
METHOD_AR = [
    "سلاسل شهرية من البيانات الموحّدة — الشهر غير المكتمل يُستبعد، والأشهر المفقودة لا تُعامل كصفر",
    "الموسمية: مؤشر موسمي لكل شهر (نسبة إلى المتوسط المتحرك 12 شهراً) — فقط مع 24 شهراً متصلة",
    "الاتجاه: ميل Theil–Sen المقاوم للقيم الشاذة على آخر 24 شهراً، والمستوى يُعدَّل بوسيط آخر 3 أشهر (يلتقط التحوّل المستمر ويتجاهل الشهر الاستثنائي)",
    f"تخميد الاتجاه φ = {DAMP} حتى لا يُمدّ النمو الحالي بلا حد",
    "نطاق 80%: ± 1.28 × الانحراف المعياري المقاوم للبواقي × √(عدد الأشهر للأمام)",
    "اختبار رجعي: توقع آخر 6 أشهر من بيانات قبلها فقط (MAPE)",
    "الربح = الإيراد المتوقع × الهامش المتوقع − المصروفات المتوقعة − البنود تحت EBITDA (وسيط آخر 6 أشهر)",
]
CONF_RULE_AR = ("الثقة = طول التاريخ (25) + اكتمال الأشهر (15) + حداثة البيانات (10) + استقرار السلسلة (20) + دقة الاختبار الرجعي (20) + "
                "توفر الموسمية (5) + قلة القيم الشاذة (5) — وتنخفض 4% لكل شهر أبعد · مرتفعة ≥ 75 · متوسطة ≥ 55")


# ═══════════════════════════════════════════════════════════
# 3.7.1 + 3.7.3 — عقد البيانات وتحضير السلاسل
# ═══════════════════════════════════════════════════════════
def _ym(d):
    return d.strftime("%Y-%m")


def _next_ym(ym, k=1):
    y, m = int(ym[:4]), int(ym[5:7])
    m += k
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def _month_end(ym):
    return date(int(ym[:4]), int(ym[5:7]), 1).replace(day=28) + timedelta(days=4) - timedelta(days=(date(int(ym[:4]), int(ym[5:7]), 1).replace(day=28) + timedelta(days=4)).day)


def sales_monthly(rows):
    """إيراد/طلبات/كميات شهرية للشركة ولكل فرع من صفوف المبيعات نفسها."""
    comp, br, end = {}, {}, None
    for r in rows or []:
        d = RE._parse_day(r.get("date"))
        if d is None:
            continue
        ns = to_decimal(r.get("net_sales"))
        if ns is None:
            g = to_decimal(r.get("gross_sales"))
            ns = None if g is None else g - (to_decimal(r.get("discounts")) or 0) - (to_decimal(r.get("returns")) or 0)
        if ns is None:
            continue
        end = d if end is None or d > end else end
        ym = _ym(d)
        for bucket in [comp] + ([br.setdefault(r["branch_name"], {})] if r.get("branch_name") else []):
            e = bucket.setdefault(ym, {"revenue": Decimal(0), "orders": set(), "rows": 0, "quantity": Decimal(0)})
            e["revenue"] += ns
            e["rows"] += 1
            if r.get("reference"):
                e["orders"].add(r["reference"])
            q = to_decimal(r.get("quantity"))
            if q is not None:
                e["quantity"] += q
    def fin(b):
        return {ym: {"revenue": float(v["revenue"]), "orders": len(v["orders"]) or v["rows"], "quantity": float(v["quantity"]) or None,
                     "aov": float(v["revenue"]) / (len(v["orders"]) or v["rows"])} for ym, v in b.items()}
    partial = None
    if end is not None and end < _month_end(_ym(end)):
        partial = _ym(end)
    return {"company": fin(comp), "branches": {k: fin(v) for k, v in br.items()}, "data_end": end, "partial_month": partial}


def series_of(monthly, field, exclude=None):
    return [(ym, v[field]) for ym, v in sorted(monthly.items()) if ym != exclude and v.get(field) is not None]


def consecutive_tail(points):
    """آخر سلسلة متصلة — الأشهر قبل أي انقطاع لا تُستخدم (لا نملأ الفجوة بصفر)."""
    if not points:
        return [], []
    pts = sorted(points)
    tail = [pts[-1]]
    for p in reversed(pts[:-1]):
        if _next_ym(p[0]) == tail[0][0]:
            tail.insert(0, p)
        else:
            break
    missing = []
    if len(tail) < len(pts):
        a = pts[0][0]
        have = {x[0] for x in pts}
        while a < tail[0][0]:
            if a not in have:
                missing.append(a)
            a = _next_ym(a)
    return tail, missing


# ═══════════════════════════════════════════════════════════
# 3.7.4 — محرك التنبؤ
# ═══════════════════════════════════════════════════════════
def _median(v):
    s = sorted(v)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def theil_sen(ys):
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    slopes = [(ys[j] - ys[i]) / (j - i) for i in range(n) for j in range(i + 1, n)]
    b = _median(slopes)
    a = _median([y - b * x for x, y in enumerate(ys)])
    return b, a


def seasonal_index(points):
    """مؤشر موسمي ضربي لكل شهر تقويمي — يتطلب 24 شهراً متصلة وقيماً موجبة."""
    vals = [v for _, v in points]
    if len(points) < SEASON_MONTHS or any(v <= 0 for v in vals):
        return None
    ratios = {}
    for i in range(6, len(vals) - 6):
        ma = (sum(vals[i - 6:i + 6]) + sum(vals[i - 5:i + 7])) / 24.0
        if ma > 0:
            ratios.setdefault(points[i][0][5:7], []).append(vals[i] / ma)
    if len(ratios) < 12:
        return None
    idx = {m: _median(r) for m, r in ratios.items()}
    mean = sum(idx.values()) / 12.0
    return {m: v / mean for m, v in idx.items()}


def _fit(points, season, nonneg):
    vals = [v for _, v in points][-FIT_WINDOW:]
    months = [m for m, _ in points][-FIT_WINDOW:]
    ds = [v / season[m[5:7]] for m, v in zip(months, vals)] if season else list(vals)
    b, a = theil_sen(ds)
    resid = [y - (a + b * x) for x, y in enumerate(ds)]
    med = _median(resid) or 0.0
    sigma = 1.4826 * (_median([abs(r - med) for r in resid]) or 0.0)
    if sigma == 0 and len(ds) > 1:
        sigma = (sum(r * r for r in resid) / (len(ds) - 1)) ** 0.5
    # المستوى = قيمة الخط عند آخر شهر + وسيط بواقي آخر 3 أشهر: يلتقط تحوّل المستوى المستمر (شهران من 3)
    # ويتجاهل الشهر الاستثنائي الواحد.
    shift = _median(resid[-3:]) if len(resid) >= 3 else 0.0
    level = a + b * (len(ds) - 1) + shift
    return {"slope": b, "level": level, "level_shift": shift, "sigma": sigma, "resid": resid, "months": months, "ds": ds}


def _project(fit, last_ym, h_max, season, nonneg):
    out = []
    for h in range(1, h_max + 1):
        trend = fit["slope"] * sum(DAMP ** i for i in range(1, h + 1))
        ym = _next_ym(last_ym, h)
        s = season[ym[5:7]] if season else 1.0
        v = (fit["level"] + trend) * s
        half = Z80 * fit["sigma"] * math.sqrt(h) * s
        lo, hi = v - half, v + half
        if nonneg:
            v, lo = max(0.0, v), max(0.0, lo)
        out.append({"period": ym, "h": h, "value": round(v, 2), "lower": round(lo, 2), "upper": round(hi, 2)})
    return out


def backtest(points, nonneg=True, use_season=True, k=6, steps=3):
    """توقع آخر k أشهر من بيانات قبلها فقط — أخطاء حقيقية لا افتراضية."""
    n = len(points)
    if n < MIN_MONTHS + 3:
        return None
    errs, by_h = [], {}
    for i in range(max(MIN_MONTHS + 1, n - k), n):
        train = points[:i]
        season = seasonal_index(train) if use_season else None
        f = _fit(train, season, nonneg)
        proj = _project(f, train[-1][0], steps, season, nonneg)
        for p in proj:
            j = i + p["h"] - 1
            if j < n and points[j][1]:
                e = abs(p["value"] - points[j][1]) / abs(points[j][1]) * 100
                errs.append(e)
                by_h.setdefault(p["h"], []).append(e)
    if not errs:
        return None
    return {"mape": round(sum(errs) / len(errs), 1), "n": len(errs), "by_horizon": {h: round(sum(v) / len(v), 1) for h, v in sorted(by_h.items())}}


def forecast_series(points, horizon=HORIZON, *, nonneg=True, use_season=True, today=None, data_end=None, outlier_k=3.0):
    """تنبؤ سلسلة شهرية واحدة مع النطاق والثقة والمنهجية — أو سبب عدم التنبؤ."""
    tail, missing = consecutive_tail(points)
    n = len(tail)
    base = {"model": MODEL, "months_available": len(points), "months_used": n, "missing_months": missing[-12:],
            "is_estimate": True}
    if n < MIN_MONTHS:
        return {**base, "status": "insufficient", "points": [], "sufficiency": "none",
                "sufficiency_ar": f"بيانات تاريخية غير كافية — متاح {n} أشهر متصلة، الحد الأدنى {MIN_MONTHS}",
                "confidence": {"score": 0, "label": "low", "label_ar": CONF_AR["low"]}}
    season = seasonal_index(tail) if use_season else None
    fit = _fit(tail, season, nonneg)
    proj = _project(fit, tail[-1][0], horizon, season, nonneg)
    med = _median(fit["resid"]) or 0.0
    lim = outlier_k * max(fit["sigma"], 1e-9)
    outliers = [fit["months"][i] for i, r in enumerate(fit["resid"]) if abs(r - med) > lim and fit["sigma"] > 0]
    bt = backtest(tail, nonneg, use_season)
    mean = sum(abs(v) for _, v in tail[-12:]) / min(12, n)
    cv = fit["sigma"] / mean if mean else 1.0
    span_missing = len([m for m in missing if m >= _next_ym(tail[-1][0], -FIT_WINDOW)])
    end = data_end or datetime.strptime(tail[-1][0] + "-28", "%Y-%m-%d").date()
    age = ((today or date.today()) - end).days
    comp = {"history": min(n / 24.0, 1.0), "completeness": max(0.0, 1 - span_missing / 12.0),
            "recency": 1.0 if age <= 40 else 0.6 if age <= 75 else 0.2, "stability": max(0.0, 1 - min(cv / 0.5, 1.0)),
            "backtest": max(0.0, 1 - bt["mape"] / 50.0) if bt else 0.5, "seasonality": 1.0 if season else 0.6,
            "outliers": max(0.0, 1 - len(outliers) / 3.0)}
    W = {"history": 25, "completeness": 15, "recency": 10, "stability": 20, "backtest": 20, "seasonality": 5, "outliers": 5}
    score = round(sum(W[k] * comp[k] for k in W), 0)
    suff = next((s for s in SUFF if n >= s[0]), SUFF[-1])
    cap = {"insufficient": 50, "limited": 70}.get(suff[1])
    if cap:
        score = min(score, cap)
    for p in proj:
        c = round(max(0.0, score * (1 - CONF_DECAY * (p["h"] - 1))), 0)
        p["confidence"] = c
        p["confidence_label"] = "high" if c >= 75 else "medium" if c >= 55 else "low"
    label = "high" if score >= 75 else "medium" if score >= 55 else "low"
    return {**base, "status": "ok", "points": proj, "last_actual": {"period": tail[-1][0], "value": round(tail[-1][1], 2)},
            "history": [{"period": m, "value": round(v, 2)} for m, v in tail[-24:]],
            "slope_per_month": round(fit["slope"], 2), "seasonality_applied": bool(season),
            "seasonality": {m: round(v, 3) for m, v in sorted(season.items())} if season else None,
            "seasonality_ar": "مطبّقة" if season else f"غير مطبّقة — تحتاج {SEASON_MONTHS} شهراً متصلة (متاح {n})",
            "sigma": round(fit["sigma"], 2), "outliers": outliers, "backtest": bt, "sufficiency": suff[1], "sufficiency_ar": suff[2],
            "confidence": {"score": score, "label": label, "label_ar": CONF_AR[label], "components": {k: round(v, 2) for k, v in comp.items()},
                           "weights": W, "rule_ar": CONF_RULE_AR, "data_age_days": age},
            "warnings": ([f"انقطاع في السلسلة — لم تُستخدم الأشهر قبل {tail[0][0]}"] if missing else []) +
                        ([f"قيم شاذة (أُبقيت، والميل المقاوم يحد من أثرها): {'، '.join(outliers)}"] if outliers else [])}


def _sum(pts, key="value"):
    return round(sum(p[key] for p in pts), 2) if pts else None


def _last_n(points, n):
    return [v for _, v in sorted(points)[-n:]]


# ═══════════════════════════════════════════════════════════
# 3.7.6 — توقع الربح (الإيراد × الهامش − المصروفات − ما تحت EBITDA)
# ═══════════════════════════════════════════════════════════
def profit_forecast(fin_trends, revenue_fc, horizon=HORIZON, today=None):
    tr = [t for t in fin_trends or [] if t.get("revenue")]
    if revenue_fc.get("status") != "ok":
        return {"status": "unavailable", "reason_ar": "توقع الإيراد غير متاح"}
    if len(tr) < MIN_MONTHS or any(t.get("gross_profit") is None or t.get("opex") is None for t in tr[-MIN_MONTHS:]):
        return {"status": "unavailable", "reason_ar": "يلزم تكلفة المنتجات والمصروفات لثلاثة أشهر على الأقل في الوحدة المالية"}
    tr = [t for t in tr if t.get("gross_profit") is not None and t.get("opex") is not None]
    gm_pts = [(t["period"], t["gross_profit"] / t["revenue"] * 100) for t in tr]
    opex_pts = [(t["period"], float(t["opex"])) for t in tr]
    below = _median([(t.get("ebitda") or 0) - (t.get("net_profit") or 0) for t in tr[-6:] if t.get("ebitda") is not None and t.get("net_profit") is not None]) or 0.0
    gm_fc = forecast_series(gm_pts, horizon, nonneg=False, use_season=False, today=today)
    ox_fc = forecast_series(opex_pts, horizon, nonneg=True, use_season=False, today=today)
    if gm_fc["status"] != "ok" or ox_fc["status"] != "ok":
        return {"status": "unavailable", "reason_ar": "سلاسل الهامش أو المصروفات قصيرة"}
    pts = []
    for r, g, o in zip(revenue_fc["points"], gm_fc["points"], ox_fc["points"]):
        gm = max(-100.0, min(100.0, g["value"]))
        gp = r["value"] * gm / 100
        net = gp - o["value"] - below
        lo = r["lower"] * max(-100.0, min(100.0, g["lower"])) / 100 - o["upper"] - below
        hi = r["upper"] * max(-100.0, min(100.0, g["upper"])) / 100 - o["lower"] - below
        pts.append({"period": r["period"], "h": r["h"], "revenue": r["value"], "gross_margin": round(gm, 2), "gross_profit": round(gp, 2),
                    "opex": o["value"], "net_profit": round(net, 2), "lower": round(min(lo, hi), 2), "upper": round(max(lo, hi), 2),
                    "confidence": round(min(r["confidence"], g["confidence"], o["confidence"]), 0)})
    last6 = tr[-6:]
    n6 = len(last6)
    rev_now = sum(t["revenue"] for t in last6) / n6
    gm_now = sum(t["gross_profit"] for t in last6) / sum(t["revenue"] for t in last6) * 100
    ox_now = sum(t["opex"] for t in last6) / n6
    net_now = sum((t.get("net_profit") or 0) for t in last6) / n6
    H = len(pts)
    rev_f = sum(p["revenue"] for p in pts) / H
    gm_f = sum(p["gross_profit"] for p in pts) / sum(p["revenue"] for p in pts) * 100 if sum(p["revenue"] for p in pts) else gm_now
    ox_f = sum(p["opex"] for p in pts) / H
    # تفكيك تغيّر صافي الربح الشهري المتوسط: أثر الإيراد + أثر الهامش − أثر المصروفات (مجموعها = التغيّر بالضبط مع ثبات ما تحت EBITDA)
    eff_rev = (rev_f - rev_now) * gm_now / 100
    eff_margin = rev_f * (gm_f - gm_now) / 100
    eff_opex = -(ox_f - ox_now)
    net_f = sum(p["net_profit"] for p in pts) / H
    return {"status": "ok", "points": pts, "below_ebitda": round(below, 2),
            "history": [{"period": t["period"], "value": round(float(t["net_profit"]), 2)} for t in tr[-12:] if t.get("net_profit") is not None],
            "now": {"revenue": round(rev_now, 2), "gross_margin": round(gm_now, 2), "opex": round(ox_now, 2), "net_profit": round(net_now, 2), "months": n6},
            "forecast_avg": {"revenue": round(rev_f, 2), "gross_margin": round(gm_f, 2), "opex": round(ox_f, 2), "net_profit": round(net_f, 2)},
            "total": {"revenue": round(sum(p["revenue"] for p in pts), 2), "gross_profit": round(sum(p["gross_profit"] for p in pts), 2),
                      "net_profit": round(sum(p["net_profit"] for p in pts), 2)},
            "revenue_growth_pct": RE._pct(rev_f - rev_now, rev_now), "profit_growth_pct": RE._pct(net_f - net_now, abs(net_now)) if net_now else None,
            "bridge": [{"key": "revenue", "ar": "أثر تغيّر الإيراد", "amount": round(eff_rev, 2)}, {"key": "margin", "ar": "أثر تغيّر الهامش الإجمالي", "amount": round(eff_margin, 2)},
                       {"key": "opex", "ar": "أثر تغيّر المصروفات", "amount": round(eff_opex, 2)}],
            "bridge_note_ar": "متوسط شهري للأشهر المتوقعة مقابل آخر 6 أشهر فعلية — المكوّنات الثلاثة تساوي تغيّر صافي الربح (مع ثبات البنود تحت EBITDA)",
            "margin_fc": {"slope_pp": gm_fc["slope_per_month"], "confidence": gm_fc["confidence"]}, "opex_fc": {"slope": ox_fc["slope_per_month"]},
            "warnings": [f"المصروفات: أشهر استثنائية لا يُفترض تكرارها ({'، '.join(ox_fc['outliers'])}) — إن كانت متكررة فالتوقع متفائل" ] * bool(ox_fc["outliers"])
                        + [f"الهامش تغيّر بشكل لافت في ({'، '.join(gm_fc['outliers'])}) — التوقع يعتمد المستوى الأخير إن استمر شهرين من آخر 3"] * bool(gm_fc["outliers"])}


# ═══════════════════════════════════════════════════════════
# 3.7 — توقع السيولة (الرصيد + صافي التدفق المتوقع)
# ═══════════════════════════════════════════════════════════
def cash_forecast(cf, horizon=HORIZON, today=None, min_cash=None):
    if not RE._avail(cf):
        return {"status": "unavailable", "reason_ar": "لا توجد بيانات تدفق نقدي"}
    mv = [(m["period"], RE._n(m.get("net"))) for m in cf.get("movement") or [] if RE._n(m.get("net")) is not None]
    bal = RE._n(RE._g(cf, "position", "balance"))
    if bal is None:
        return {"status": "unavailable", "reason_ar": "الرصيد غير متاح — أضف عمود الرصيد أو الرصيد الافتتاحي"}
    f = forecast_series(mv, horizon, nonneg=False, use_season=True, today=today)
    if f["status"] != "ok":
        return {"status": "unavailable", "reason_ar": f["sufficiency_ar"]}
    path, b, blo, bhi = [], bal, bal, bal
    first_neg, first_min = None, None
    for p in f["points"]:
        b += p["value"]; blo += p["lower"]; bhi += p["upper"]
        path.append({"period": p["period"], "h": p["h"], "net": p["value"], "balance": round(b, 2), "lower": round(blo, 2), "upper": round(bhi, 2),
                     "confidence": p["confidence"]})
        if b < 0 and first_neg is None:
            first_neg = p["period"]
        if min_cash and b < float(min_cash) and first_min is None:
            first_min = p["period"]
    return {"status": "ok", "start_balance": round(bal, 2), "points": path, "net_forecast": f, "first_negative": first_neg, "first_below_min": first_min,
            "min_cash": min_cash, "end_balance": path[-1]["balance"] if path else None}


# ═══════════════════════════════════════════════════════════
# 3.7.7 + 3.7.8 — الفروع وتفكيك النمو
# ═══════════════════════════════════════════════════════════
def branch_forecasts(sm, horizon=HORIZON, today=None):
    out = []
    for b, mon in sorted(sm["branches"].items()):
        pts = series_of(mon, "revenue", exclude=sm.get("partial_month"))
        f = forecast_series(pts, horizon, today=today, data_end=sm.get("data_end"))
        if f["status"] != "ok":
            out.append({"branch": b, "status": "insufficient", "reason_ar": f["sufficiency_ar"]})
            continue
        cur = sum(_last_n(pts, 3)) / min(3, len(pts))
        f3 = f["points"][min(2, len(f["points"]) - 1)]
        ch = RE._pct(f3["value"] - cur, cur)
        direction = "up" if (ch or 0) >= 2 else "down" if (ch or 0) <= -2 else "flat"
        base6 = sum(_last_n(pts, 6))
        out.append({"branch": b, "status": "ok", "current_avg": round(cur, 2), "forecast_m3": f3["value"], "index_now": 100, "index_m3": round(100 * f3["value"] / cur, 1) if cur else None,
                    "change_pct": ch, "direction": direction, "arrow": {"up": "↑", "down": "↓", "flat": "→"}[direction],
                    "confidence": f3["confidence"], "confidence_label": f3["confidence_label"], "horizon_total": _sum(f["points"]),
                    "last6_total": round(base6, 2), "forecast": f})
    return out


def growth_decomposition(company_rev_fc, orders_fc, branches, sm):
    """النمو المتوقع = حجم الطلبات + متوسط قيمة الطلب (تفكيك لوغاريتمي يجمع بالضبط) + مساهمة كل فرع."""
    if company_rev_fc.get("status") != "ok":
        return None
    pts = series_of(sm["company"], "revenue", exclude=sm.get("partial_month"))
    H = len(company_rev_fc["points"])
    base = sum(_last_n(pts, H))
    fut = _sum(company_rev_fc["points"])
    if not base:
        return None
    g = (fut - base) / base * 100
    out = {"growth_pct": round(g, 2), "base_total": round(base, 2), "forecast_total": fut, "window_ar": f"مجموع {H} أشهر متوقعة مقابل آخر {H} أشهر فعلية",
           "components": [], "branches": []}
    if orders_fc and orders_fc.get("status") == "ok":
        opts = series_of(sm["company"], "orders", exclude=sm.get("partial_month"))
        ob, of = sum(_last_n(opts, H)), _sum(orders_fc["points"])
        if ob and of:
            a_b, a_f = base / ob, fut / of
            lo, la = math.log(of / ob), math.log(a_f / a_b)
            tot = lo + la
            if abs(tot) > 1e-9:
                out["components"] = [{"key": "volume", "ar": "حجم الطلبات", "pct": round(g * lo / tot, 2), "detail_ar": f"الطلبات {ob:,.0f} ← {of:,.0f}"},
                                     {"key": "aov", "ar": "متوسط قيمة الطلب", "pct": round(g * la / tot, 2), "detail_ar": f"متوسط الطلب {a_b:,.1f} ← {a_f:,.1f}"}]
    s = 0.0
    for b in branches:
        if b["status"] != "ok":
            continue
        bb = b["last6_total"] if H == 6 else None
        d = (b["horizon_total"] - (bb if bb is not None else 0)) / base * 100
        s += d
        out["branches"].append({"branch": b["branch"], "pct": round(d, 2), "arrow": b["arrow"]})
    out["branches"].sort(key=lambda x: -x["pct"])
    if out["branches"]:
        out["branch_residual_pct"] = round(g - s, 2)
        out["branch_note_ar"] = "مساهمة كل فرع = (توقعه − فعله) ÷ إيراد الشركة الأساسي. الفرق عن نمو الشركة = تقريب/مبيعات غير مسندة لفرع (التوقع الكلي يُحسب مستقلاً)."
    return out


# ═══════════════════════════════════════════════════════════
# 3.7.12 — فجوة الهدف + ما المطلوب لتقليلها
# ═══════════════════════════════════════════════════════════
def fiscal_months(today_ym, fy_start=1):
    y, m = int(today_ym[:4]), int(today_ym[5:7])
    sy = y if m >= fy_start else y - 1
    start = f"{sy:04d}-{fy_start:02d}"
    return start, _next_ym(start, 11)


def target_gap(sm, rev_fc_long, profit_fc, targets, fy_start=1, recovery=None):
    """إسقاط نهاية السنة المالية = الفعلي منذ بدايتها + التوقع للأشهر المتبقية — مقابل الهدف."""
    pts = series_of(sm["company"], "revenue", exclude=sm.get("partial_month"))
    if not pts or rev_fc_long.get("status") != "ok":
        return {"status": "unavailable", "reason_ar": "توقع الإيراد غير متاح"}
    last = pts[-1][0]
    fy_s, fy_e = fiscal_months(_next_ym(last), fy_start)
    ytd = sum(v for ym, v in pts if fy_s <= ym <= last)
    rem = [p for p in rev_fc_long["points"] if p["period"] <= fy_e]
    proj = ytd + sum(p["value"] for p in rem)
    tgt = to_decimal((targets or {}).get("annual_revenue"))
    out = {"status": "ok", "fiscal_year": f"{fy_s} → {fy_e}", "ytd_actual": round(ytd, 2), "remaining_months": len(rem),
           "remaining_forecast": round(sum(p["value"] for p in rem), 2), "projection": round(proj, 2),
           "projection_range": [round(ytd + sum(p["lower"] for p in rem), 2), round(ytd + sum(p["upper"] for p in rem), 2)],
           "beyond_6_note_ar": "الأشهر بعد السادس أقل ثقة" if len(rem) > HORIZON else None, "target_source_ar": (targets or {}).get("source_ar")}
    if tgt is None or tgt <= 0:
        out.update({"target": None, "reason_ar": "لا يوجد هدف سنوي للإيراد — حدده في إعدادات التنبؤ أو الموازنة في الوحدة المالية"})
        return out
    gap = float(tgt) - proj
    out.update({"target": float(tgt), "gap": round(gap, 2), "gap_pct": round(gap / float(tgt) * 100, 1),
                "status_ar": "متوقع تجاوز الهدف" if gap <= 0 else "متوقع أقل من الهدف",
                "sentence_ar": (f"إذا استمرت الشركة بنفس المسار، يُتوقع أن تنتهي السنة {'أعلى' if gap <= 0 else 'أقل'} من الهدف بـ {abs(gap):,.0f}.")})
    if gap > 0:
        recs, used = [], 0.0
        groups = {}
        for r in sorted(recovery or [], key=lambda r: -(r.get("amount") or 0)):
            if not r.get("amount") or r["amount"] <= 0:
                continue
            g = r.get("group", r["key"])
            if g in groups:
                continue
            groups[g] = True
            recs.append(r)
            used += r["amount"]
        out["recovery"] = {"items": recs, "total": round(used, 2), "remaining_gap": round(max(0.0, gap - used), 2),
                           "note_ar": "فرص تقديرية من بياناتك (مجموعات متداخلة يُحتسب أكبرها فقط) — ليست مضمونة"}
    return out


# ═══════════════════════════════════════════════════════════
# 3.7.11 + 3.7.13 + 3.7.14 — مسببات التوقع، المخاطر والفرص المستقبلية
# ═══════════════════════════════════════════════════════════
DRIVER_EFFECT = {
    "stockout_items": ("sales", "قد يؤثر استمرار نفاد الأصناف على مبيعات الأشهر القادمة"),
    "cost_increase_pct": ("margin", "ارتفاع تكلفة الشراء يضغط على الهامش المتوقع"),
    "supplier_late_pct": ("sales", "تأخر الموردين قد يسبب نفاداً لاحقاً"),
    "leakage_pct": ("recovery", "تسرب قابل للاسترداد يحسّن الربح المتوقع إن عولج"),
    "x_discount_leakage": ("margin", "الخصومات فوق المعتاد تخفض الهامش المتوقع"),
    "x_excess_opex": ("profit", "المصروفات فوق خط الأساس تخفض الربح المتوقع"),
    "lost_customer_revenue_pct": ("sales", "توقف عملاء سابقين قد يستمر في خفض الإيراد"),
    "revenue_decline_pct": ("sales", "تراجع الإيراد الأخير ينعكس على الاتجاه المتوقع"),
    "dso_days": ("cash", "بطء التحصيل يؤخر النقد المتوقع"), "overdue_ar_pct": ("cash", "ذمم متأخرة تضغط على السيولة القادمة"),
    "runway_months": ("cash", "مدة السيولة القصيرة خطر على الأشهر القادمة"),
    "on_time_gap_pp": ("sales", "التأخر في التسليم قد يؤثر على عودة العملاء"),
    "turnover_pct": ("capacity", "دوران الموظفين قد يحد من الطاقة القادمة"),
    "utilization_pct": ("capacity", "الطاقة قريبة من الحد — النمو قد يتعطل"),
}
EFFECT_AR = {"sales": "المبيعات", "margin": "الهامش", "profit": "الربح", "cash": "السيولة", "capacity": "الطاقة", "recovery": "فرصة استرداد"}


def forecast_drivers(rev_fc, branches, profit, drivers):
    out = []
    if rev_fc.get("status") == "ok":
        sl = rev_fc["slope_per_month"]
        out.append({"key": "trend", "ar": "اتجاه الإيراد", "effect": "sales", "direction": "up" if sl > 0 else "down" if sl < 0 else "flat",
                    "evidence_ar": f"ميل {sl:+,.0f} شهرياً (Theil–Sen)", "source_ar": "المبيعات", "kind": "model"})
        if rev_fc.get("seasonality_applied"):
            nxt = rev_fc["points"][0]["period"][5:7]
            out.append({"key": "season", "ar": "الموسمية", "effect": "sales", "direction": "up" if (rev_fc["seasonality"] or {}).get(nxt, 1) >= 1 else "down",
                        "evidence_ar": f"مؤشر الشهر القادم {(rev_fc['seasonality'] or {}).get(nxt)}", "source_ar": "المبيعات", "kind": "model"})
    for b in sorted([b for b in branches if b["status"] == "ok" and abs(b["change_pct"] or 0) >= 5], key=lambda b: b["change_pct"]):
        out.append({"key": f"branch:{b['branch']}", "ar": f"الفرع {b['branch']}", "effect": "sales", "direction": b["direction"],
                    "evidence_ar": f"متوقع {b['change_pct']:+}% بعد 3 أشهر (ثقة {b['confidence']:.0f})", "source_ar": "توقع الفروع", "kind": "branch"})
    if profit.get("status") == "ok":
        for x in profit["bridge"]:
            if abs(x["amount"]) > 0.01 * max(abs(profit["now"]["net_profit"]), 1):
                out.append({"key": f"bridge:{x['key']}", "ar": x["ar"], "effect": "profit", "direction": "up" if x["amount"] > 0 else "down",
                            "evidence_ar": f"{x['amount']:+,.0f} شهرياً على صافي الربح", "source_ar": "الوحدة المالية", "kind": "model"})
    for d in (drivers or {}).get("drivers") or []:
        if d.get("level") in RE.ELEVATED and d["key"] in DRIVER_EFFECT:
            eff, txt = DRIVER_EFFECT[d["key"]]
            tr = (d.get("trend") or {}).get("direction")
            out.append({"key": d["key"], "ar": d["name_ar"], "effect": eff, "direction": "down" if eff != "recovery" else "up",
                        "evidence_ar": (d.get("evidence") or [""])[0], "source_ar": d.get("source_ar"), "kind": "risk_driver", "level": d["level"],
                        "note_ar": txt, "trend_ar": (d.get("trend") or {}).get("ar"), "worsening": tr in ("worsening", "accelerating"),
                        "link": f"company-risk-drivers.html#driver/{d['key']}",
                        "recovery": next((i["amount"] for i in d.get("impacts") or [] if i["type"] == "recovery"), None)})
    rank = {"risk_driver": 0, "branch": 1, "model": 2}
    out.sort(key=lambda x: (rank[x["kind"]], {"critical": 0, "high": 1, "medium": 2}.get(x.get("level"), 3)))
    return out


def future_risks(rev_fc, profit, cash, branches, drivers, sm):
    R_ = []
    def add(code, ar, level, horizon_ar, evidence, impact=None, links=None):
        R_.append({"code": code, "ar": ar, "level": level, "level_ar": RE.LEVEL_AR[level], "horizon_ar": horizon_ar, "evidence": [e for e in evidence if e],
                   "impact": impact, "links": links or []})
    if profit.get("status") == "ok":
        pts = profit["points"][:3]
        f3 = sum(p["net_profit"] for p in pts) / len(pts)
        now = profit["now"]["net_profit"]
        if now and f3 < now:
            dec = (now - f3) / abs(now) * 100
            if dec >= 5:
                add("profit_decline", "انخفاض الربح خلال 90 يوماً", "high" if dec >= 10 else "medium", "90 يوماً",
                    [f"صافي الربح الشهري المتوقع {f3:,.0f} مقابل {now:,.0f} فعلياً (−{dec:.1f}%)"] + [f"{x['ar']}: {x['amount']:+,.0f}" for x in profit["bridge"] if x["amount"] < 0],
                    {"amount": round((now - f3) * len(pts), 0), "type_ar": "انخفاض ربح متوقع"})
        gm_now, gm3 = profit["now"]["gross_margin"], profit["points"][min(2, len(profit["points"]) - 1)]["gross_margin"]
        if gm_now - gm3 >= 1:
            add("margin_decline", "تراجع الهامش الإجمالي", "high" if gm_now - gm3 >= 3 else "medium", "90 يوماً",
                [f"الهامش {gm_now:.1f}% ← {gm3:.1f}% متوقع"])
    if cash.get("status") == "ok":
        if cash.get("first_negative"):
            h = next(p["h"] for p in cash["points"] if p["period"] == cash["first_negative"])
            if cash["start_balance"] < 0:
                add("cash_shortfall", "عجز نقدي قائم ومستمر", "critical", "الآن وخلال 60 يوماً",
                    [f"الرصيد الحالي سالب {cash['start_balance']:,.0f}", f"الرصيد المتوقع بعد {len(cash['points'])} أشهر {cash['end_balance']:,.0f}"])
            else:
                add("cash_shortfall", "عجز نقدي متوقع", "critical" if h <= 2 else "high", "60 يوماً" if h <= 2 else f"{h} أشهر",
                    [f"الرصيد المتوقع يصبح سالباً في {cash['first_negative']}", f"الرصيد الحالي {cash['start_balance']:,.0f}"])
        elif cash.get("first_below_min"):
            add("cash_below_min", "الرصيد دون الحد الأدنى", "medium", "خلال 6 أشهر", [f"دون الحد الأدنى في {cash['first_below_min']}"])
    if rev_fc.get("status") == "ok":
        pts = series_of(sm["company"], "revenue", exclude=sm.get("partial_month"))
        b3 = sum(_last_n(pts, 3)) / min(3, len(pts))
        f3 = sum(p["value"] for p in rev_fc["points"][:3]) / 3
        if b3 and (f3 - b3) / b3 * 100 <= -3:
            dec = (b3 - f3) / b3 * 100
            add("revenue_decline", "انخفاض الإيراد", "high" if dec >= 10 else "medium", "90 يوماً", [f"متوسط شهري متوقع {f3:,.0f} مقابل {b3:,.0f} (−{dec:.1f}%)"],
                {"amount": round((b3 - f3) * 3, 0), "type_ar": "إيراد متوقع أقل"})
    for b in branches:
        if b["status"] == "ok" and (b["change_pct"] or 0) <= -5:
            add(f"branch_decline:{b['branch']}", f"تراجع الفرع {b['branch']}", "high" if b["change_pct"] <= -10 else "medium", "90 يوماً",
                [f"{b['current_avg']:,.0f} ← {b['forecast_m3']:,.0f} ({b['change_pct']:+}%) · ثقة {b['confidence']:.0f}"],
                {"amount": round(max(0.0, b["last6_total"] - b["horizon_total"]), 0), "type_ar": "خسارة إيراد محتملة"})
    for d in (drivers or {}).get("drivers") or []:
        if d.get("level") in ("high", "critical") and (d.get("trend") or {}).get("direction") in ("worsening", "accelerating"):
            add(f"driver:{d['key']}", f"{d['name_ar']} مستمر ويسوء", "high", "60 يوماً", [(d.get("evidence") or [""])[0], (d.get("trend") or {}).get("ar")],
                links=[f"company-risk-drivers.html#driver/{d['key']}"])
    R_.sort(key=lambda r: ["critical", "high", "medium", "low"].index(r["level"]))
    return R_


def future_opportunities(branches, drivers, bench, ops):
    out = []
    util = {b["key"]: b.get("utilization") for b in (ops or {}).get("branches") or []} if RE._avail(ops) else {}
    for b in branches:
        if b["status"] == "ok" and (b["change_pct"] or 0) >= 3 and b["confidence"] >= 55:
            f = b["forecast"]["points"]
            run = b["last6_total"] / 6 * len(f) if b["last6_total"] else None
            lo = max(0.0, sum(p["lower"] for p in f) - run) if run else None
            hi = max(0.0, sum(p["upper"] for p in f) - run) if run else None
            u = util.get(b["branch"])
            cap = "طاقة متاحة" if u is not None and u < 80 else ("الطاقة قريبة من الحد" if u is not None else "الطاقة غير معروفة (حددها في العمليات)")
            out.append({"code": f"branch_growth:{b['branch']}", "ar": f"نمو الفرع {b['branch']}", "evidence": [f"اتجاه نمو {b['change_pct']:+}% خلال 3 أشهر", f"استخدام الطاقة: {u}%" if u is not None else cap],
                        "range": [round(lo, 0), round(hi, 0)] if lo is not None else None, "confidence": b["confidence"], "capacity_ar": cap,
                        "action_ar": "زيادة الطاقة البيعية في الفرع" if (u is None or u < 80) else "وسّع الطاقة قبل أن تحد من النمو"})
    for d in (drivers or {}).get("drivers") or []:
        rec = next((i for i in d.get("impacts") or [] if i["type"] == "recovery"), None)
        if rec and d.get("level") in RE.ELEVATED:
            out.append({"code": f"recovery:{d['key']}", "ar": f"استرداد: {d['name_ar']}", "evidence": [(d.get("evidence") or [""])[0]],
                        "range": [0, round(rec["amount"] * HORIZON, 0)], "confidence": (d.get("confidence") or {}).get("pct"),
                        "action_ar": (d.get("recommendation") or {}).get("text_ar"), "note_ar": "الحد الأعلى = مبلغ الشهر × 6 إن استمر بنفس المستوى"})
    for o in (bench or {}).get("opportunities") or []:
        if (o.get("impact") or {}).get("amount"):
            out.append({"code": f"sector:{o['id']}", "ar": f"سد فجوة قطاعية: {o['ar']}", "evidence": [f"{g['name_ar']}: {g['company']} مقابل {g['benchmark']}" for g in o["gaps"][:2]],
                        "range": [0, round(o["impact"]["amount"] / 2, 0)], "confidence": None, "action_ar": (o.get("actions") or [None])[0],
                        "note_ar": "نصف سنة من الفرصة التوضيحية السنوية — ليست مضمونة"})
    return out


# ═══════════════════════════════════════════════════════════
# 3.7.15 + 3.7.16 — محاكاة «ماذا لو» (نسخة حسابية مؤقتة — لا تعدّل أي بيانات)
# ═══════════════════════════════════════════════════════════
ASSUMPTIONS = {"volume_pct": ("حجم المبيعات %", -90, 200), "price_pct": ("الأسعار/متوسط الطلب %", -50, 100),
               "purchase_cost_pct": ("تكلفة المشتريات %", -50, 100), "opex_pct": ("المصروفات التشغيلية %", -50, 100),
               "leakage_recovery": ("استرداد تسرب (مبلغ إجمالي)", 0, None)}


def validate_assumptions(a):
    errs, out = [], {}
    for k, v in (a or {}).items():
        if k == "branches":
            if isinstance(v, dict):
                out["branches"] = {}
                for b, p in v.items():
                    x = to_decimal(p)
                    if x is None or not (-90 <= x <= 200):
                        errs.append(f"نسبة الفرع {b} خارج النطاق")
                    else:
                        out["branches"][str(b)] = float(x)
            continue
        if k not in ASSUMPTIONS or v in (None, ""):
            continue
        x = to_decimal(v)
        lo, hi = ASSUMPTIONS[k][1], ASSUMPTIONS[k][2]
        if x is None or x < lo or (hi is not None and x > hi):
            errs.append(f"{ASSUMPTIONS[k][0]}: خارج النطاق")
        else:
            out[k] = float(x)
    return out, errs


def scenario_baseline(rev_fc, profit, cash, branches, ops=None, inventory_value=None):
    if profit.get("status") != "ok":
        return None
    return {"months": [p["period"] for p in profit["points"]], "revenue": [p["revenue"] for p in profit["points"]],
            "gm": [p["gross_margin"] for p in profit["points"]], "opex": [p["opex"] for p in profit["points"]], "below": profit["below_ebitda"],
            "cash_start": cash.get("start_balance") if cash.get("status") == "ok" else None,
            "cash_net": [p["net"] for p in cash["points"]] if cash.get("status") == "ok" else None,
            "branches": {b["branch"]: [p["value"] for p in b["forecast"]["points"]] for b in branches if b["status"] == "ok"},
            "utilization": RE._g(ops, "kpis", "utilization", "value") if RE._avail(ops) else None, "inventory_value": inventory_value}


def simulate(base, assumptions, name="سيناريو"):
    """الأساس → الافتراض → المحاكاة → النتيجة. الحجم يحرّك الإيراد والتكلفة معاً؛ السعر يحرّك الإيراد فقط."""
    a = assumptions or {}
    vol, price, cost, opx = (1 + a.get("volume_pct", 0) / 100), (1 + a.get("price_pct", 0) / 100), (1 + a.get("purchase_cost_pct", 0) / 100), (1 + a.get("opex_pct", 0) / 100)
    H = len(base["months"])
    rec_m = (a.get("leakage_recovery") or 0) / H
    rows, rev_t, gp_t, net_t, cash = [], 0.0, 0.0, 0.0, base.get("cash_start")
    min_cash, cash_path = None, []
    for i in range(H):
        r0 = base["revenue"][i]
        extra_b = sum(v[i] * p / 100 for b, v in base["branches"].items() for bb, p in (a.get("branches") or {}).items() if bb == b and i < len(v))
        units_rev = (r0 + extra_b) * vol
        rev = units_rev * price
        cogs = units_rev * (1 - base["gm"][i] / 100) * cost
        gp = rev - cogs + rec_m
        net = gp - base["opex"][i] * opx - base["below"]
        r_base = r0
        net_base = r0 * base["gm"][i] / 100 - base["opex"][i] - base["below"]
        if cash is not None and base.get("cash_net"):
            cash += base["cash_net"][i] + (net - net_base)
            cash_path.append(round(cash, 2))
            min_cash = cash if min_cash is None else min(min_cash, cash)
        rev_t += rev; gp_t += gp; net_t += net
        rows.append({"period": base["months"][i], "revenue": round(rev, 2), "net_profit": round(net, 2)})
    u = base.get("utilization")
    return {"name": name, "assumptions": a, "months": rows, "revenue": round(rev_t, 2), "gross_profit": round(gp_t, 2), "net_profit": round(net_t, 2),
            "margin_pct": round(net_t / rev_t * 100, 2) if rev_t else None, "cash_end": cash_path[-1] if cash_path else None,
            "cash_min": round(min_cash, 2) if min_cash is not None else None, "cash_negative": bool(min_cash is not None and min_cash < 0),
            "inventory_need": round(base["inventory_value"] * vol * cost, 2) if base.get("inventory_value") else None,
            "utilization": round(u * vol, 1) if u is not None else None, "capacity_warning": bool(u is not None and u * vol > 100),
            "is_simulation": True}


def preset_scenarios(base, leakage_recovery=None):
    if not base:
        return []
    sc = [("base", "استمرار الوضع الحالي", {}), ("growth", "نمو المبيعات +10%", {"volume_pct": 10}),
          ("cost", "خفض تكلفة المشتريات −5%", {"purchase_cost_pct": -5})]
    if leakage_recovery:
        sc.append(("leakage", f"استرداد التسرب {leakage_recovery:,.0f}", {"leakage_recovery": leakage_recovery}))
    comb = {"volume_pct": 10, "purchase_cost_pct": -5}
    if leakage_recovery:
        comb["leakage_recovery"] = leakage_recovery
    sc.append(("combined", "مجمّع", comb))
    out = []
    b0 = simulate(base, {}, "الأساس")
    for code, ar, a in sc:
        r = simulate(base, a, ar)
        r.update({"code": code, "delta_revenue": round(r["revenue"] - b0["revenue"], 2), "delta_profit": round(r["net_profit"] - b0["net_profit"], 2),
                  "delta_cash": round((r["cash_end"] or 0) - (b0["cash_end"] or 0), 2) if r["cash_end"] is not None else None})
        out.append(r)
    return out


# ═══════════════════════════════════════════════════════════
# 3.7.17 + 3.7.18 — دقة التوقعات وسجلها (من توقعات محفوظة قبل حدوث الفعلي)
# ═══════════════════════════════════════════════════════════
def accuracy(stored, actuals):
    """stored: [{metric, scope, period, h, value, lower, upper, made_on}] · actuals: {(metric, scope): {period: value}}.
    الدقة = 100 − MAPE · الانحياز = متوسط الخطأ الموقّع % (موجب = التوقع أعلى من الفعلي)."""
    rows = []
    for p in stored or []:
        act = (actuals.get((p["metric"], p.get("scope", "company"))) or {}).get(p["period"])
        if act is None:
            continue
        err = p["value"] - act
        pe = abs(err) / abs(act) * 100 if act else None
        rows.append({**p, "actual": round(act, 2), "error": round(err, 2), "ape": round(pe, 1) if pe is not None else None,
                     "signed_pct": round(err / abs(act) * 100, 1) if act else None,
                     "in_range": p.get("lower") is not None and p["lower"] <= act <= p["upper"]})
    def agg(xs):
        ap = [x["ape"] for x in xs if x["ape"] is not None]
        if not ap:
            return None
        mape = sum(ap) / len(ap)
        return {"n": len(xs), "mae": round(sum(abs(x["error"]) for x in xs) / len(xs), 2), "mape": round(mape, 1), "accuracy": round(max(0.0, 100 - mape), 1),
                "bias_pct": round(sum(x["signed_pct"] for x in xs if x["signed_pct"] is not None) / len(ap), 1),
                "in_range_pct": round(100 * sum(1 for x in xs if x["in_range"]) / len(xs), 0)}
    by = lambda key: {k: agg([x for x in rows if key(x) == k]) for k in sorted({key(x) for x in rows})}
    table = sorted([x for x in rows if x["metric"] == "revenue" and x.get("scope", "company") == "company" and x["h"] == 1], key=lambda x: x["period"])
    return {"evaluated": len(rows), "overall": agg(rows), "by_metric": by(lambda x: x["metric"]), "by_horizon": by(lambda x: x["h"]),
            "by_branch": {k: v for k, v in by(lambda x: x.get("scope", "company")).items() if k != "company"},
            "table": [{"period": x["period"], "forecast": x["value"], "actual": x["actual"], "variance_pct": x["signed_pct"] and -x["signed_pct"],
                       "in_range": x["in_range"]} for x in table][-12:],
            "note_ar": None if rows else "الدقة تُحسب عندما يحدث شهر سبق أن توقعه نبّاه — تُبنى تلقائياً مع كل شهر جديد",
            "rule_ar": "الدقة = 100 − متوسط الخطأ المطلق النسبي (MAPE) · الانحياز موجب = التوقع أعلى من الفعلي"}


def measure_decisions(decisions, stored, actuals):
    """قبل/بعد: الفعلي بعد القرار مقابل التوقع الأساسي المحفوظ قبله لنفس الأشهر — قياس وليس إثبات سببية."""
    out = []
    for d in decisions or []:
        made = str(d.get("created_on") or "")[:10]
        metric = d.get("metric") or "revenue"
        pre = [p for p in stored or [] if p["metric"] == metric and p.get("scope", "company") == "company" and str(p.get("made_on") or "") <= made]
        if not pre:
            out.append({**d, "status": "no_baseline", "status_ar": "لا يوجد توقع محفوظ قبل القرار للمقارنة"})
            continue
        latest_run = max(str(p.get("made_on")) for p in pre)
        base = {p["period"]: p["value"] for p in pre if str(p.get("made_on")) == latest_run and p["period"] > made[:7]}
        act = actuals.get((metric, "company")) or {}
        pairs = [(m, base[m], act[m]) for m in sorted(base) if m in act]
        if not pairs:
            out.append({**d, "status": "pending", "status_ar": "بانتظار أشهر فعلية بعد القرار", "baseline_total": round(sum(base.values()), 2)})
            continue
        diff = sum(a - b for _, b, a in pairs)
        out.append({**d, "status": "measured", "months": [m for m, _, _ in pairs], "baseline_forecast": round(sum(b for _, b, _ in pairs), 2),
                    "actual": round(sum(a for _, _, a in pairs), 2), "difference": round(diff, 2),
                    "sentence_ar": f"الفعلي {'أعلى' if diff >= 0 else 'أقل'} من التوقع الأساسي قبل القرار بـ {abs(diff):,.0f} خلال {len(pairs)} أشهر",
                    "note_ar": "مقارنة بخط أساس التوقع — لا تُثبت أن القرار وحده هو السبب"})
    return out


# ═══════════════════════════════════════════════════════════
# 3.7.21 — إعداد القطاعات (محرك واحد، مسميات ومؤشرات مختلفة)
# ═══════════════════════════════════════════════════════════
SECTOR_PRED = {
    "fnb": {"orders": "الطلبات", "aov": "متوسط الفاتورة", "kpis": ["food_cost_pct", "labor_pct", "delivery_commission_pct"], "needs": []},
    "retail": {"orders": "الفواتير", "aov": "سلة الشراء", "units": True, "kpis": ["cogs_pct", "shrinkage_pct"], "needs": [("نسبة البيع Sell-through", "المستلم مقابل المباع لكل موسم")]},
    "ecommerce": {"orders": "الطلبات", "aov": "متوسط الطلب", "kpis": ["returns_pct", "discount_pct"], "needs": [("الجلسات والتحويل", "بيانات زيارات المتجر")]},
    "manufacturing": {"orders": "أوامر البيع", "aov": "متوسط الأمر", "units": True, "kpis": ["cogs_pct", "labor_pct"], "needs": [("كفاءة المعدات", "ساعات التشغيل والتوقف")]},
    "contracting": {"orders": "الفواتير/المستخلصات", "aov": "متوسط المستخلص", "collections": True, "kpis": ["cogs_pct"],
                    "needs": [("المشاريع وهامشها", "إيراد وتكلفة كل مشروع"), ("جدول المشاريع", "مراحل بتواريخ مخططة وفعلية")]},
    "distribution": {"orders": "الطلبات", "aov": "متوسط الطلب", "units": True, "collections": True, "kpis": ["cogs_pct"], "needs": []},
    "services": {"orders": "الطلبات/العقود", "aov": "متوسط العقد", "kpis": ["labor_pct"], "needs": []},
    "clinics": {"orders": "الزيارات", "aov": "الإيراد لكل زيارة", "kpis": ["labor_pct"], "needs": [("نسبة الإشغال", "المواعيد المتاحة والمحجوزة")]},
    "hospitals": {"orders": "الزيارات", "aov": "الإيراد لكل زيارة", "kpis": ["labor_pct"], "needs": [("إشغال الأسرّة", "الأسرّة المتاحة والمشغولة يومياً")]},
    "logistics": {"orders": "الشحنات", "aov": "الإيراد لكل شحنة", "collections": True, "kpis": [], "needs": [("تكلفة الشحنة", "تكلفة كل شحنة")]},
    "other": {"orders": "الطلبات", "aov": "متوسط الطلب", "kpis": [], "needs": []},
}


def sector_metrics(sector, sm, mods, horizon, today):
    cfg = SECTOR_PRED.get(sector or "other", SECTOR_PRED["other"])
    out = []
    ex = sm.get("partial_month")
    for key, label, nonneg in (("orders", cfg["orders"], True), ("aov", cfg["aov"], True)) + ((("quantity", "الوحدات المباعة", True),) if cfg.get("units") else ()):
        f = forecast_series(series_of(sm["company"], key, exclude=ex), horizon, nonneg=nonneg, today=today, data_end=sm.get("data_end"))
        out.append({"key": key, "ar": label, "forecast": f})
    if cfg.get("collections") and RE._avail(mods.get("cashflow")):
        pts = [(m["period"], RE._n(m.get("inflow"))) for m in mods["cashflow"].get("movement") or [] if RE._n(m.get("inflow")) is not None]
        out.append({"key": "collections", "ar": "التحصيلات", "forecast": forecast_series(pts, horizon, today=today)})
    kp = {k["code"]: k for k in RE._g(mods.get("finance"), "sector", "kpis") or []}
    for code in cfg["kpis"]:
        k = kp.get(code)
        if not k:
            out.append({"key": "sector:" + code, "ar": code, "forecast": {"status": "insufficient", "points": [], "sufficiency_ar": "مؤشر القطاع غير متاح في الوحدة المالية"}})
            continue
        pts = [(s["period"], s["value"]) for s in k.get("series") or [] if s.get("value") is not None]
        out.append({"key": "sector:" + code, "ar": k["name_ar"], "forecast": forecast_series(pts, horizon, nonneg=False, use_season=False, today=today)})
    return {"sector": sector or "other", "ar": RE.sector_profile(sector)["ar"], "metrics": out,
            "needs": [{"ar": a, "needs_ar": n} for a, n in cfg["needs"]], "note_ar": "محرك تنبؤ واحد — القطاع يحدد المسميات والمؤشرات"}


# ═══════════════════════════════════════════════════════════
# خطة التدخل + التنبيهات + الموجز التنفيذي
# ═══════════════════════════════════════════════════════════
RISK_PLAN = {"profit_decline": ("المالية", "راجع جسر الربح: المصروفات المرتفعة وأصناف الهامش المنخفض"),
             "margin_decline": ("المشتريات", "راجع أسعار الموردين والخصومات قبل دورة الشراء القادمة"),
             "cash_shortfall": ("المالية", "رتّب أولويات الدفع وسرّع التحصيل وجهّز تمويلاً قصيراً"),
             "cash_below_min": ("المالية", "خطط لرفع الرصيد فوق الحد الأدنى"),
             "revenue_decline": ("المبيعات", "حلّل الانخفاض حسب الفرع والقناة وتواصل مع العملاء المتوقفين"),
             "branch_decline": ("مدير الفرع", "عالج المخزون والخصومات في الفرع وراجع أسباب انخفاض الطلبات")}
DUE = {"critical": 7, "high": 14, "medium": 30, "low": 45}


def interventions(risks, fdrivers, today):
    plan = []
    rd = [d for d in fdrivers if d["kind"] == "risk_driver"]
    for r in risks:
        base = r["code"].split(":")[0]
        owner, act = RISK_PLAN.get(base, ("الإدارة", None))
        eff = {"profit_decline": ("profit", "margin"), "margin_decline": ("margin",), "cash_shortfall": ("cash",), "cash_below_min": ("cash",),
               "revenue_decline": ("sales",), "branch_decline": ("sales",), "driver": ()}.get(base, ())
        causes = [d for d in rd if d["effect"] in eff][:3]
        if base == "driver":
            act = next((d.get("note_ar") for d in rd if r["code"].endswith(d["key"])), None)
        plan.append({"code": r["code"], "prediction_ar": r["ar"], "level": r["level"], "level_ar": r["level_ar"],
                     "cause": [{"ar": c["ar"], "evidence_ar": c["evidence_ar"], "link": c.get("link")} for c in causes] or [{"ar": e} for e in r["evidence"][:2]],
                     "impact": r.get("impact"), "action_ar": act or "راجع المسببات المرتبطة", "owner_ar": owner,
                     "due": (today + timedelta(days=DUE[r["level"]])).isoformat(), "due_days": DUE[r["level"]],
                     "measurement_ar": "قارن التوقع الأساسي بالفعلي بعد تنفيذ الإجراء (قبل/بعد في سجل التوقعات)"})
    return plan


def executive_brief(rev_fc, profit, gap, risks, fdrivers, scenarios, currency):
    lines = []
    if rev_fc.get("status") != "ok":
        return {"headline": "لا يمكن التنبؤ — " + rev_fc.get("sufficiency_ar", ""), "lines": [], "method_ar": "موجز حتمي من نتائج محرك التنبؤ — بلا ذكاء اصطناعي"}
    tot = _sum(rev_fc["points"])
    conf = rev_fc["confidence"]
    s = f"إذا استمرت الشركة على المسار الحالي، نتوقع إيراداً قدره {tot:,.0f} {currency} خلال {len(rev_fc['points'])} أشهر، بدرجة ثقة {conf['score']:.0f}% ({conf['label_ar']})"
    if profit.get("status") == "ok":
        s += f"، وصافي ربح {profit['total']['net_profit']:,.0f}"
    lines.append(s + ".")
    top = [d for d in fdrivers if d["kind"] in ("risk_driver", "branch") and d["effect"] in ("sales", "margin", "profit")][:3]
    if top:
        lines.append("الأسباب الرئيسية: " + "، ".join(d["ar"] for d in top) + ".")
    if gap.get("status") == "ok" and gap.get("target"):
        lines.append(gap["sentence_ar"] + (f" فرص التحسين المقدّرة {gap['recovery']['total']:,.0f} والفجوة المتبقية {gap['recovery']['remaining_gap']:,.0f}." if gap.get("recovery") else ""))
    if risks:
        lines.append("أبرز خطر متوقع: " + risks[0]["ar"] + f" ({risks[0]['level_ar']}، {risks[0]['horizon_ar']}).")
    comb = next((x for x in scenarios if x["code"] == "combined"), None)
    if comb:
        lines.append(f"إذا اتخذت الإدارة القرارات المجمّعة ({comb['name']}) فإن السيناريو المحاكى يغيّر صافي الربح بـ {comb['delta_profit']:+,.0f}"
                     + (f" والرصيد النهائي بـ {comb['delta_cash']:+,.0f}" if comb.get("delta_cash") is not None else "") + " — محاكاة وليست وعداً.")
    return {"headline": lines[0], "lines": lines, "method_ar": "موجز حتمي من نتائج محرك التنبؤ — بلا ذكاء اصطناعي"}


def analyze_prediction(mods, *, sales_rows=None, risk=None, drivers=None, bench=None, settings=None, stored=None, decisions=None,
                       sector=None, today=None, categories=None, currency="SAR", horizon=HORIZON):
    today = today or date.today()
    mods, settings = mods or {}, settings or {}
    scope = categories or RE.CAT_ORDER
    see_profit, see_cash = "profit" in scope, "liquidity" in scope
    sm = sales_monthly(sales_rows)
    ex = sm.get("partial_month")
    rev_pts = series_of(sm["company"], "revenue", exclude=ex)
    rev_fc = forecast_series(rev_pts, horizon, today=today, data_end=sm.get("data_end"))
    if rev_fc["status"] != "ok":
        return {"has_data": bool(rev_pts), "version": MODEL, "as_of": today.isoformat(), "status": "insufficient",
                "sufficiency_ar": rev_fc["sufficiency_ar"], "months_available": len(rev_pts),
                "message_ar": f"بيانات تاريخية غير كافية للتنبؤ — المتاح {rev_fc['months_used']} أشهر متصلة، والحد الأدنى {MIN_MONTHS}. لا نعرض توقعاً وكأنه دقيق.",
                "methodology": {"model": MODEL, "steps_ar": METHOD_AR}}
    orders_fc = forecast_series(series_of(sm["company"], "orders", exclude=ex), horizon, today=today, data_end=sm.get("data_end"))
    branches = branch_forecasts(sm, horizon, today)
    profit = profit_forecast((mods.get("finance") or {}).get("trends"), rev_fc, horizon, today) if see_profit else {"status": "restricted", "reason_ar": "خارج صلاحيتك"}
    min_cash = settings.get("min_cash") or ((mods.get("cashflow") or {}).get("settings") or {}).get("min_cash")
    cash = cash_forecast(mods.get("cashflow"), horizon, today, min_cash) if see_cash else {"status": "restricted", "reason_ar": "خارج صلاحيتك"}
    growth = growth_decomposition(rev_fc, orders_fc, branches, sm)
    fdrv = forecast_drivers(rev_fc, branches, profit, drivers)
    risks = future_risks(rev_fc, profit if see_profit else {}, cash if see_cash else {}, branches, drivers, sm)
    opps = future_opportunities(branches, drivers, bench, mods.get("operations"))
    # الهدف السنوي: إسقاط حتى نهاية السنة المالية (قد يتجاوز 6 أشهر بثقة أقل)
    rev_long = forecast_series(rev_pts, 12, today=today, data_end=sm.get("data_end"))
    recovery = []
    run = {b["branch"]: b["last6_total"] / 6 for b in branches if b["status"] == "ok"}
    if rev_long["status"] == "ok":
        fy_s, fy_e = fiscal_months(_next_ym(rev_pts[-1][0]), int(settings.get("fy_start") or 1))
        rem = len([p for p in rev_long["points"] if p["period"] <= fy_e])
        for b in branches:
            if b["status"] == "ok" and b["direction"] == "down":
                short = sum(max(0.0, run[b["branch"]] - p["value"]) for p in b["forecast"]["points"][:rem])
                recovery.append({"key": f"branch:{b['branch']}", "group": f"branch:{b['branch']}", "ar": f"إعادة الفرع {b['branch']} لمعدله السابق", "amount": round(short, 0),
                                 "basis_ar": "الفرق بين متوسط آخر 6 أشهر للفرع وتوقعه للأشهر المتبقية"})
        for d in (drivers or {}).get("drivers") or []:
            rec = next((i for i in d.get("impacts") or [] if i["type"] == "recovery"), None)
            if rec and d["key"] in ("x_discount_leakage", "x_returns_leakage"):
                recovery.append({"key": d["key"], "group": "leakage_sales", "ar": f"استرداد {d['name_ar']}", "amount": round(rec["amount"] * rem, 0),
                                 "basis_ar": "المبلغ الشهري القابل للاسترداد × الأشهر المتبقية إن استمر بنفس المستوى"})
        cu = (risk or {}).get("customers") or {}
        if cu.get("lost_revenue"):
            recovery.append({"key": "lost_customers", "group": "customers", "ar": "استعادة العملاء المتوقفين", "amount": round(cu["lost_revenue"] / 3 * rem, 0),
                             "basis_ar": "إنفاقهم الشهري السابق × الأشهر المتبقية — إن عادوا لنفس المستوى"})
        aov = ((bench or {}).get("comparisons") or {}).get("aov") or {}
        if (aov.get("impact") or {}).get("amount"):
            recovery.append({"key": "aov_sector", "group": "aov", "ar": "رفع متوسط الطلب لمعيار القطاع", "amount": round(aov["impact"]["amount"] * rem / 12, 0),
                             "basis_ar": "فرصة المقارنة القطاعية التوضيحية × الأشهر المتبقية ÷ 12"})
    tgt = dict(settings.get("targets") or {})
    gap = target_gap(sm, rev_long, profit, tgt, int(settings.get("fy_start") or 1), recovery) if rev_long["status"] == "ok" else {"status": "unavailable"}
    pg_profit = None
    if see_profit and profit.get("status") == "ok" and tgt.get("annual_profit"):
        pg_profit = {"target": float(tgt["annual_profit"]), "forecast_6m": profit["total"]["net_profit"],
                     "run_rate_annual": round(profit["forecast_avg"]["net_profit"] * 12, 2),
                     "gap": round(float(tgt["annual_profit"]) - profit["forecast_avg"]["net_profit"] * 12, 2),
                     "note_ar": "معدل سنوي من متوسط الأشهر المتوقعة"}
    inv_val = RE._g(mods.get("inventory"), "kpis", "inventory_value", "current") if RE._avail(mods.get("inventory")) else None
    base = scenario_baseline(rev_fc, profit, cash if see_cash else {}, branches, mods.get("operations"), inv_val) if see_profit else None
    leak_rec = next((r["amount"] * horizon for r in [{"amount": next((i["amount"] for i in d.get("impacts") or [] if i["type"] == "recovery"), None)}
                                                    for d in (drivers or {}).get("drivers") or [] if d["key"] == "leakage_pct"] if r["amount"]), None)
    scen = preset_scenarios(base, leak_rec) if base else []
    acts = {("revenue", "company"): {m: v for m, v in rev_pts}}
    for b, mon in sm["branches"].items():
        acts[("revenue", b)] = {m: v["revenue"] for m, v in mon.items() if m != ex}
    acts[("orders", "company")] = {m: v["orders"] for m, v in sm["company"].items() if m != ex}
    if see_profit:
        acts[("net_profit", "company")] = {t["period"]: t["net_profit"] for t in (mods.get("finance") or {}).get("trends") or [] if t.get("net_profit") is not None}
    acc = accuracy(stored, acts)
    dec = measure_decisions(decisions, stored, acts)
    sect = sector_metrics(sector, sm, mods, horizon, today)
    plan = interventions(risks, fdrv, today)
    brief = executive_brief(rev_fc, profit if see_profit else {}, gap, risks, fdrv, scen, currency)
    alerts = [{"kind": r["level"], "ar": r["ar"], "evidence": r["evidence"], "horizon_ar": r["horizon_ar"]} for r in risks]
    for k, v in (acc.get("by_branch") or {}).items():
        if v and v["accuracy"] < 70:
            alerts.append({"kind": "medium", "ar": f"دقة التوقع منخفضة لفرع {k} ({v['accuracy']}%)", "evidence": ["بيانات الفرع أقل استقراراً — تعامل مع توقعه بحذر"], "horizon_ar": "—"})
    if rev_fc["sufficiency"] in ("insufficient", "limited"):
        alerts.append({"kind": "low", "ar": "تاريخ البيانات محدود — الثقة مقيّدة", "evidence": [rev_fc["sufficiency_ar"]], "horizon_ar": "—"})
    snap = {"base_period": rev_pts[-1][0], "model": MODEL, "horizon": horizon, "confidence": rev_fc["confidence"]["score"],
            "predictions": [{"metric": "revenue", "scope": "company", "period": p["period"], "h": p["h"], "value": p["value"], "lower": p["lower"], "upper": p["upper"], "confidence": p["confidence"]} for p in rev_fc["points"]]
            + ([{"metric": "orders", "scope": "company", "period": p["period"], "h": p["h"], "value": p["value"], "lower": p["lower"], "upper": p["upper"], "confidence": p["confidence"]} for p in orders_fc["points"]] if orders_fc["status"] == "ok" else [])
            + [{"metric": "revenue", "scope": b["branch"], "period": p["period"], "h": p["h"], "value": p["value"], "lower": p["lower"], "upper": p["upper"], "confidence": p["confidence"]}
               for b in branches if b["status"] == "ok" for p in b["forecast"]["points"]]
            + ([{"metric": "net_profit", "scope": "company", "period": p["period"], "h": p["h"], "value": p["net_profit"], "lower": p["lower"], "upper": p["upper"], "confidence": p["confidence"]} for p in profit["points"]] if profit.get("status") == "ok" else []),
            "drivers": [{"key": d["key"], "ar": d["ar"], "effect": d["effect"], "evidence_ar": d["evidence_ar"]} for d in fdrv[:12]]}
    cur_rev = sum(_last_n(rev_pts, horizon))
    return {
        "has_data": True, "status": "ok", "version": MODEL, "as_of": today.isoformat(), "currency": currency, "horizon": horizon,
        "scope": {"profit": see_profit, "cash": see_cash, "note_ar": None if (see_profit and see_cash) else "عرض جزئي حسب صلاحيتك"},
        "data": {"months_available": len(rev_pts), "data_end": sm["data_end"].isoformat() if sm.get("data_end") else None, "partial_month": ex,
                 "partial_note_ar": f"الشهر {ex} غير مكتمل في البيانات — لم يدخل في التنبؤ" if ex else None, "missing_months": rev_fc["missing_months"],
                 "sufficiency": rev_fc["sufficiency"], "sufficiency_ar": rev_fc["sufficiency_ar"]},
        "outlook": {"current_revenue": round(cur_rev, 2), "forecast_revenue": _sum(rev_fc["points"]), "growth_pct": growth["growth_pct"] if growth else None,
                    "forecast_profit": profit["total"]["net_profit"] if profit.get("status") == "ok" else None,
                    "profit_growth_pct": profit.get("profit_growth_pct") if profit.get("status") == "ok" else None,
                    "confidence": rev_fc["confidence"], "basis_ar": f"مبني على {rev_fc['months_used']} شهراً من البيانات · {rev_fc['sufficiency_ar']}"},
        "sales": rev_fc, "orders": orders_fc, "profit": profit, "cash": cash, "branches": [{k: v for k, v in b.items() if k != "forecast"} | ({"points": b["forecast"]["points"], "backtest": b["forecast"].get("backtest")} if b["status"] == "ok" else {}) for b in branches],
        "growth": growth, "drivers": fdrv, "gap": gap, "profit_gap": pg_profit, "risks": risks, "opportunities": opps,
        "scenario_base_available": base is not None, "scenario_baseline": base, "scenarios": scen, "assumption_limits": {k: {"ar": v[0], "min": v[1], "max": v[2]} for k, v in ASSUMPTIONS.items()},
        "accuracy": acc, "decisions_measured": dec, "plan": plan, "alerts": alerts, "sector": sect, "brief": brief,
        "methodology": {"model": MODEL, "steps_ar": METHOD_AR, "confidence_rule_ar": CONF_RULE_AR, "updated": today.isoformat(),
                        "window": f"آخر {min(FIT_WINDOW, rev_fc['months_used'])} شهراً", "seasonality_ar": rev_fc["seasonality_ar"], "backtest": rev_fc.get("backtest"),
                        "damping": DAMP, "interval": "80%"},
        "signals": [{"id": "pred-" + RE._rid(r["code"], today.strftime("%Y-%m"))[5:], "type": "risk", "code": "future:" + r["code"], "source_module": "prediction",
                     "name_ar": "متوقع: " + r["ar"], "severity": "high" if r["level"] in ("critical", "high") else "medium", "dimension": None,
                     "period": today.strftime("%Y-%m"), "evidence": r["evidence"][:2], "suggested_action_ar": RISK_PLAN.get(r["code"].split(":")[0], (None, None))[1],
                     "estimated_impact": {"value": r["impact"]["amount"], "type_ar": r["impact"]["type_ar"]} if r.get("impact") else None,
                     "metric_id": r["code"], "method": MODEL} for r in risks],
        "ai_questions": ["ما المتوقع للمبيعات خلال 6 أشهر؟", "لماذا يتوقع انخفاض الأرباح؟", "أي فرع لديه أفضل فرصة للنمو؟", "ما أكبر خطر على توقع الأرباح؟",
                         "ماذا يحدث لو رفعنا المبيعات 10%؟", "ماذا يحدث لو خفضنا تكلفة المشتريات 5%؟", "ما الإجراءات التي تقلل فجوة الهدف؟"],
        "snapshot": snap,
        "disclaimer_ar": "توقعات تقديرية من محرك حتمي بمنهجية معلنة — ليست حقائق مؤكدة. السيناريوهات محاكاة لا تعدّل أي بيانات.",
    }
