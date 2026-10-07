"""
NABBAH — Phase 3.5 · Sector Benchmark Intelligence (المقارنة بالقطاع)
3.3: ما الخطر؟ · 3.4: ما الذي يحرّكه؟ · 3.5: أين تقف الشركة مقارنةً بمعيار قطاعها؟

قواعد صارمة:
- لا معيار بلا مصدر: كل Benchmark له مصدر وفترة وقطاع وجغرافيا وحجم ومنهجية وعيّنة وثقة وحداثة.
- لا بيانات معيار كافية → «المعيار غير متاح» — وليس 0% ولا متوسطاً مخترعاً.
- لا مؤشر للشركة إذا بياناتها لا تدعمه.
- لا ترتيب/مئين بدون توزيع موثّق (Percentiles) من مصدر المعيار.
- الفجوة المالية «فرصة توضيحية» وليست استرداداً مضموناً.
- معيار الأقران من شركات نبّاه: مجمّع ومجهّل وبموافقة الشركات فقط، وبحد أدنى 5 شركات — لا تُكشف بيانات أي شركة.
- لا مواقع منافسين مخترعة: «تموضع قطاعي» مقابل المعيار فقط.
- حتمي بلا ذكاء اصطناعي.
"""
import re
from decimal import Decimal
from datetime import date, datetime, timedelta

from nabbah_finance import to_decimal, round_money
import risk_engine as RE

BENCH_VERSION = "1.0"
D0 = Decimal("0")

# ═══════════════════════════════════════════════════════════
# 3.5.1 — عقد البيانات: كتالوج المؤشرات القابلة للمقارنة
# dir: higher (الأعلى أفضل) / lower (الأقل أفضل) · tol: (قريب، فجوة حرجة، abs=بالوحدة | rel=نسبة %)
# basis: أساس الحساب — لا نقارن أساسين مختلفين (مثلاً نمو سنوي بشهري)
# ═══════════════════════════════════════════════════════════
GROUPS = {"financial": "المالية", "sales": "المبيعات", "inventory": "المخزون", "operations": "العمليات", "customer": "العملاء",
          "hr": "الموارد البشرية", "cash": "السيولة والتحصيل", "sector": "مؤشرات القطاع"}
GROUP_CAT = {"financial": "profit", "sales": "customer", "inventory": "operational", "operations": "operational", "customer": "customer",
             "hr": "operational", "cash": "liquidity", "sector": "profit"}
METRICS = {
    "gross_margin": {"g": "financial", "ar": "هامش الربح الإجمالي", "unit": "%", "dir": "higher", "tol": (1, 5, "abs"), "basis": "ttm"},
    "net_margin": {"g": "financial", "ar": "هامش صافي الربح", "unit": "%", "dir": "higher", "tol": (1, 4, "abs"), "basis": "ttm"},
    "ebitda_margin": {"g": "financial", "ar": "هامش EBITDA", "unit": "%", "dir": "higher", "tol": (1, 4, "abs"), "basis": "ttm"},
    "expense_ratio": {"g": "financial", "ar": "المصروفات التشغيلية من الإيراد", "unit": "%", "dir": "lower", "tol": (1, 5, "abs"), "basis": "ttm"},
    "revenue_growth": {"g": "financial", "ar": "نمو الإيراد السنوي", "unit": "%", "dir": "higher", "tol": (2, 10, "abs"), "basis": "yoy"},
    "aov": {"g": "sales", "ar": "متوسط قيمة الطلب", "unit": "SAR", "dir": "higher", "tol": (5, 20, "rel"), "basis": "ttm"},
    "discount_rate": {"g": "sales", "ar": "نسبة الخصم", "unit": "%", "dir": "lower", "tol": (0.5, 3, "abs"), "basis": "ttm"},
    "return_rate": {"g": "sales", "ar": "نسبة المرتجعات", "unit": "%", "dir": "lower", "tol": (0.5, 3, "abs"), "basis": "ttm"},
    "inventory_turnover": {"g": "inventory", "ar": "معدل دوران المخزون", "unit": "مرة", "dir": "higher", "tol": (10, 40, "rel"), "basis": "annualized"},
    "dio": {"g": "inventory", "ar": "أيام المخزون (DIO)", "unit": "يوم", "dir": "lower", "tol": (10, 40, "rel"), "basis": "point"},
    "stockout_rate": {"g": "inventory", "ar": "نسبة نفاد الأصناف", "unit": "%", "dir": "lower", "tol": (1, 5, "abs"), "basis": "point"},
    "slow_moving_pct": {"g": "inventory", "ar": "المخزون الراكد من قيمة المخزون", "unit": "%", "dir": "lower", "tol": (2, 10, "abs"), "basis": "point"},
    "fulfillment_time": {"g": "operations", "ar": "زمن التنفيذ", "unit": "دقيقة", "dir": "lower", "tol": (10, 40, "rel"), "basis": "month"},
    "on_time": {"g": "operations", "ar": "التسليم في الموعد", "unit": "%", "dir": "higher", "tol": (2, 10, "abs"), "basis": "month"},
    "order_accuracy": {"g": "operations", "ar": "دقة الطلبات", "unit": "%", "dir": "higher", "tol": (1, 5, "abs"), "basis": "month"},
    "sla_compliance": {"g": "operations", "ar": "الالتزام بـ SLA", "unit": "%", "dir": "higher", "tol": (2, 10, "abs"), "basis": "month"},
    "customer_retention": {"g": "customer", "ar": "الاحتفاظ بالعملاء (90 يوماً)", "unit": "%", "dir": "higher", "tol": (2, 10, "abs"), "basis": "q"},
    "repeat_purchase": {"g": "customer", "ar": "العملاء المتكررون", "unit": "%", "dir": "higher", "tol": (2, 10, "abs"), "basis": "q"},
    "revenue_per_employee": {"g": "hr", "ar": "الإيراد لكل موظف", "unit": "SAR", "dir": "higher", "tol": (10, 40, "rel"), "basis": "ttm"},
    "profit_per_employee": {"g": "hr", "ar": "الربح لكل موظف", "unit": "SAR", "dir": "higher", "tol": (10, 40, "rel"), "basis": "ttm"},
    "employee_turnover": {"g": "hr", "ar": "دوران الموظفين", "unit": "%", "dir": "lower", "tol": (3, 15, "abs"), "basis": "ttm"},
    "dso": {"g": "cash", "ar": "مدة التحصيل (DSO)", "unit": "يوم", "dir": "lower", "tol": (10, 40, "rel"), "basis": "point"},
}
SECTOR_KPI_META = {"food_cost_pct": ("lower", (1, 5, "abs")), "cogs_pct": ("lower", (1, 5, "abs")), "labor_pct": ("lower", (1, 5, "abs")),
                   "delivery_commission_pct": ("lower", (1, 5, "abs")), "discount_pct": ("lower", (0.5, 3, "abs")),
                   "returns_pct": ("lower", (0.5, 3, "abs")), "shrinkage_pct": ("lower", (0.5, 2, "abs")), "revenue_per_employee": ("higher", (10, 40, "rel"))}
SECTOR_KPI_AR = {"food_cost_pct": "تكلفة الطعام %", "cogs_pct": "تكلفة البضاعة %", "labor_pct": "تكلفة العمالة %", "delivery_commission_pct": "عمولات التوصيل %",
                 "discount_pct": "نسبة الخصم (مؤشر القطاع)", "returns_pct": "نسبة المرتجعات (مؤشر القطاع)", "shrinkage_pct": "فاقد المخزون %",
                 "revenue_per_employee": "الإيراد لكل موظف (مؤشر القطاع)"}
# مؤشرات مطلوبة في المواصفة لكن لا توجد بياناتها في نبّاه حالياً
DATA_NEEDED = {
    "conversion": ("sales", "معدل التحويل", "عدد الزوار/الجلسات لكل فرع أو متجر"),
    "nps": ("customer", "NPS", "نتائج استبيان NPS"), "csat": ("customer", "CSAT", "نتائج استبيان الرضا"),
    "cac": ("customer", "تكلفة اكتساب العميل (CAC)", "مصروف التسويق مصنّفاً + عدد العملاء الجدد"),
    "clv": ("customer", "القيمة الدائمة للعميل (CLV)", "سجل مشتريات العميل لسنتين على الأقل مع التكلفة"),
}

# ═══════════════════════════════════════════════════════════
# 3.5.2 + 3.5.20 — ملف القطاع: محرك واحد + إعداد لكل قطاع من 11
# ═══════════════════════════════════════════════════════════
SECTOR_BENCH = {
    "fnb": {"focus": ["sector:food_cost_pct", "sector:labor_pct", "aov", "sector:delivery_commission_pct", "gross_margin", "discount_rate"],
            "needs": [("دوران الطاولات", "عدد الطاولات ومدة الجلسة لكل فاتورة"), ("الهدر", "سجل الهدر اليومي")]},
    "retail": {"focus": ["inventory_turnover", "dio", "gross_margin", "return_rate", "discount_rate", "sector:shrinkage_pct"],
               "needs": [("نسبة البيع (Sell-through)", "الكمية المستلمة مقابل المباعة لكل موسم"), ("التخفيضات (Markdown)", "سعر البيع الأصلي والمخفّض"),
                         ("معدل التحويل", "عدد الزوار لكل متجر")]},
    "ecommerce": {"focus": ["customer_retention", "repeat_purchase", "return_rate", "aov", "discount_rate", "on_time"],
                  "needs": [("معدل التحويل", "جلسات المتجر الإلكتروني"), ("CAC", "مصروف التسويق + العملاء الجدد")]},
    "manufacturing": {"focus": ["gross_margin", "inventory_turnover", "order_accuracy", "on_time", "dio"],
                      "needs": [("كفاءة المعدات (OEE)", "ساعات التشغيل والتوقف والإنتاج الجيد")]},
    "contracting": {"focus": ["gross_margin", "dso", "net_margin", "revenue_growth"],
                    "needs": [("هامش المشروع", "إيراد وتكلفة كل مشروع"), ("انحراف التكلفة", "ميزانية مقابل فعلي لكل مشروع"),
                              ("تأخر المشاريع", "التواريخ المخططة والفعلية للمراحل")]},
    "distribution": {"focus": ["dso", "inventory_turnover", "gross_margin", "on_time", "stockout_rate"], "needs": []},
    "services": {"focus": ["revenue_per_employee", "employee_turnover", "net_margin", "customer_retention"],
                 "needs": [("نسبة الساعات المفوترة", "ساعات العمل المفوترة مقابل المتاحة")]},
    "clinics": {"focus": ["dso", "revenue_per_employee", "net_margin", "employee_turnover"], "needs": [("نسبة إشغال المواعيد", "سجل المواعيد المتاحة والمحجوزة")]},
    "hospitals": {"focus": ["dso", "revenue_per_employee", "employee_turnover", "net_margin"], "needs": [("نسبة إشغال الأسرّة", "الأسرّة المتاحة والمشغولة يومياً")]},
    "logistics": {"focus": ["on_time", "fulfillment_time", "dso", "revenue_per_employee"], "needs": [("تكلفة الشحنة", "تكلفة كل شحنة ووزنها/مسافتها")]},
    "other": {"focus": ["gross_margin", "net_margin", "revenue_growth", "dso"], "needs": []},
}

# ── 3.5.11 الحجم (حدود الإيراد السنوي — قابلة للتعديل من إعدادات الشركة)
SIZE_BANDS = [("micro", "متناهية الصغر", 3_000_000), ("small", "صغيرة", 40_000_000), ("medium", "متوسطة", 200_000_000), ("large", "كبيرة", None)]
SIZE_AR = {k: a for k, a, _ in SIZE_BANDS}
SIZE_RULE_AR = "الحجم من الإيراد السنوي: متناهية الصغر ≤ 3 مليون · صغيرة ≤ 40 مليون · متوسطة ≤ 200 مليون · كبيرة أكثر (حدود منشآت للإيرادات — يمكن تعديل التصنيف من الملف)"

# ── 3.5.10 الجغرافيا: المدينة → المنطقة
REGIONS = {"riyadh": "الرياض", "makkah": "مكة المكرمة", "eastern": "المنطقة الشرقية", "madinah": "المدينة المنورة", "qassim": "القصيم",
           "asir": "عسير", "tabuk": "تبوك", "hail": "حائل", "jazan": "جازان", "najran": "نجران", "baha": "الباحة", "jouf": "الجوف",
           "northern": "الحدود الشمالية"}
CITY_REGION = {"الرياض": "riyadh", "riyadh": "riyadh", "الخرج": "riyadh", "جدة": "makkah", "jeddah": "makkah", "مكة": "makkah", "مكة المكرمة": "makkah",
               "makkah": "makkah", "الطائف": "makkah", "taif": "makkah", "رابغ": "makkah", "الدمام": "eastern", "dammam": "eastern", "الخبر": "eastern",
               "khobar": "eastern", "الظهران": "eastern", "dhahran": "eastern", "الأحساء": "eastern", "الجبيل": "eastern", "jubail": "eastern",
               "القطيف": "eastern", "حفر الباطن": "eastern", "المدينة": "madinah", "المدينة المنورة": "madinah", "madinah": "madinah", "ينبع": "madinah",
               "بريدة": "qassim", "عنيزة": "qassim", "buraidah": "qassim", "أبها": "asir", "abha": "asir", "خميس مشيط": "asir", "تبوك": "tabuk",
               "tabuk": "tabuk", "حائل": "hail", "hail": "hail", "جازان": "jazan", "jazan": "jazan", "نجران": "najran", "najran": "najran",
               "الباحة": "baha", "سكاكا": "jouf", "الجوف": "jouf", "عرعر": "northern"}
GEO_AR = {"national": "وطني", "regional": "إقليمي", "local": "محلي"}
CONF_ORDER = ["low", "medium", "high"]
CONF_AR = {"high": "مرتفعة", "medium": "متوسطة", "low": "منخفضة"}
POS_AR = {"above": "أفضل من القطاع", "near": "قريب من القطاع", "below": "أقل من القطاع", "critical": "فجوة حرجة"}
RANK_AR = {"top": "أداء متقدم", "above_avg": "فوق المتوسط", "average": "متوسط", "below_avg": "دون المتوسط", "critical": "فجوة حرجة"}
ORIGIN_AR = {"platform": "معيار نبّاه (مصدر موثّق)", "company": "معيار أدخلته شركتك", "peer": "أقران نبّاه (مجمّع ومجهّل)"}


def size_segment(annual_revenue):
    r = to_decimal(annual_revenue)
    if r is None or r <= 0:
        return None
    for k, _, lim in SIZE_BANDS:
        if lim is None or r <= lim:
            return k
    return "large"


def region_of(city):
    return CITY_REGION.get((city or "").strip().lower()) or CITY_REGION.get((city or "").strip())


_n, _g, _avail = RE._n, RE._g, RE._avail


def _f(v, nd=2):
    d = to_decimal(_n(v))
    return None if d is None else float(round(d, nd))


def _pct(a, b):
    return RE._pct(a, b)


def _m(v):
    d = to_decimal(_n(v))
    return None if d is None else float(round_money(d))


# ═══════════════════════════════════════════════════════════
# قيم الشركة لكل مؤشر (من نتائج الوحدات نفسها — لا مصدر جديد)
# ═══════════════════════════════════════════════════════════
def sales_ttm(rows, as_of=None):
    xs = []
    for r in rows or []:
        d = RE._parse_day(r.get("date"))
        if d is None:
            continue
        g, ds, rt = to_decimal(r.get("gross_sales")), to_decimal(r.get("discounts") if r.get("discounts") is not None else r.get("discount")), to_decimal(r.get("returns"))
        ns = to_decimal(r.get("net_sales"))
        if ns is None and g is not None:
            ns = g - (ds or D0) - (rt or D0)
        xs.append((d, ns, g, ds, rt, r.get("reference"), r.get("branch_name")))
    if not xs:
        return None
    end = as_of or max(x[0] for x in xs)
    start = end - timedelta(days=365)
    cur = [x for x in xs if start < x[0] <= end]
    prev = [x for x in xs if start - timedelta(days=365) < x[0] <= start]
    def agg(ys):
        net = sum((y[1] for y in ys if y[1] is not None), D0)
        gross = sum((y[2] for y in ys if y[2] is not None), D0)
        disc = sum((y[3] for y in ys if y[3] is not None), D0)
        ret = sum((y[4] for y in ys if y[4] is not None), D0)
        refs = {y[5] for y in ys if y[5]}
        tx = len(refs) if refs else len(ys)
        return {"net": net, "gross": gross, "disc": disc, "ret": ret, "tx": tx, "has_disc": any(y[3] is not None for y in ys),
                "has_ret": any(y[4] is not None for y in ys), "months": len({y[0].strftime("%Y-%m") for y in ys})}
    out = {"cur": agg(cur), "prev": agg(prev) if prev else None, "end": end}
    out["full_prev_year"] = bool(prev) and min(x[0] for x in xs) <= start - timedelta(days=330)
    br = {}
    for y in cur:
        if y[6]:
            br.setdefault(y[6], []).append(y)
    out["branches"] = {b: agg(v) for b, v in br.items()}
    return out


def company_metrics(mods, sales_rows=None, customers=None):
    """قيمة الشركة لكل مؤشر + أساس الحساب + الأشهر + الدليل. لا قيمة → السبب (لا صفر)."""
    fn, iv, op, hr, cf, lk = (mods.get(k) for k in ("finance", "inventory", "operations", "hr", "cashflow", "leakage"))
    out, ctx = {}, {}
    def put(code, value, basis_ar, months=None, evidence=None, by_branch=None, series=None, coverage=None):
        out[code] = {"value": value, "basis_ar": basis_ar, "months": months, "evidence": [e for e in (evidence or []) if e],
                     "by_branch": by_branch or {}, "series": series or [], "coverage": coverage}
    def na(code, why):
        out[code] = {"value": None, "reason_ar": why}
    # ── المالية (آخر 12 شهراً من الاتجاه الشهري للوحدة المالية)
    if _avail(fn):
        tr = [t for t in fn.get("trends") or [] if t.get("revenue")][-12:]
        rev = sum((to_decimal(t["revenue"]) for t in tr), D0)
        months = len(tr)
        def ttm(field):
            vals = [to_decimal(t.get(field)) for t in tr]
            return None if not tr or any(v is None for v in vals) else sum(vals, D0)
        gp, eb, npf, ox = ttm("gross_profit"), ttm("ebitda"), ttm("net_profit"), ttm("opex")
        basis = f"آخر {months} شهراً" + ("" if months >= 12 else " (أقل من سنة)")
        ctx.update({"revenue_ttm": float(rev) if rev else None, "months_fin": months, "cogs_ttm": float(rev - gp) if gp is not None and rev else None})
        for code, num, label in (("gross_margin", gp, "مجمل الربح"), ("net_margin", npf, "صافي الربح"), ("ebitda_margin", eb, "EBITDA"), ("expense_ratio", ox, "المصروفات التشغيلية")):
            if num is not None and rev:
                put(code, _pct(num, rev), basis, months, [f"{label} {float(num):,.0f} ÷ الإيراد {float(rev):,.0f} ({basis})"],
                    series=[(t["period"], _pct(to_decimal(t.get({"gross_margin": "gross_profit", "net_margin": "net_profit", "ebitda_margin": "ebitda", "expense_ratio": "opex"}[code])), to_decimal(t["revenue"])))
                            for t in tr if t.get({"gross_margin": "gross_profit", "net_margin": "net_profit", "ebitda_margin": "ebitda", "expense_ratio": "opex"}[code]) is not None])
            else:
                na(code, "يلزم تكلفة المنتجات والمصروفات في الوحدة المالية")
        if "gross_margin" in out and out["gross_margin"].get("value") is not None:
            out["gross_margin"]["by_branch"] = {b["key"]: b["gross_margin"] for b in fn.get("branches") or [] if b.get("gross_margin") is not None}
    else:
        for c in ("gross_margin", "net_margin", "ebitda_margin", "expense_ratio"):
            na(c, "لا توجد بيانات في الوحدة المالية")
    # ── المبيعات (آخر 365 يوماً من صفوف المبيعات)
    st = sales_ttm(sales_rows)
    if st and st["cur"]["tx"]:
        c = st["cur"]
        ctx.update({"gross_ttm": float(c["gross"]), "transactions_ttm": c["tx"], "sales_net_ttm": float(c["net"])})
        if not ctx.get("revenue_ttm"):
            ctx["revenue_ttm"] = float(c["net"])
        bb = {b: float(round(v["net"] / v["tx"], 2)) for b, v in st["branches"].items() if v["tx"]}
        put("aov", float(round(c["net"] / c["tx"], 2)), f"آخر 365 يوماً ({c['months']} شهراً)", c["months"],
            [f"صافي المبيعات {float(c['net']):,.0f} ÷ {c['tx']:,} طلب"], by_branch=bb)
        for code, part, has in (("discount_rate", "disc", "has_disc"), ("return_rate", "ret", "has_ret")):
            if c[has] and c["gross"]:
                put(code, _pct(c[part], c["gross"]), f"آخر 365 يوماً", c["months"], [f"{float(c[part]):,.0f} ÷ إجمالي المبيعات {float(c['gross']):,.0f}"],
                    by_branch={b: _pct(v[part], v["gross"]) for b, v in st["branches"].items() if v["gross"]})
            else:
                na(code, "لا يوجد عمود " + ("الخصم" if part == "disc" else "المرتجعات") + " في المبيعات")
        if st["prev"] and st["full_prev_year"] and st["prev"]["net"]:
            put("revenue_growth", _pct(c["net"] - st["prev"]["net"], st["prev"]["net"]), "آخر 12 شهراً مقابل الـ12 قبلها", 24,
                [f"{float(c['net']):,.0f} مقابل {float(st['prev']['net']):,.0f}"])
            ctx["revenue_prev_ttm"] = float(st["prev"]["net"])
        else:
            na("revenue_growth", "النمو السنوي يحتاج مبيعات 24 شهراً (لا نقارن نمواً شهرياً بمعيار سنوي)")
    else:
        for c in ("aov", "discount_rate", "return_rate", "revenue_growth"):
            na(c, "لا توجد مبيعات مؤرخة")
    # ── المخزون
    if _avail(iv):
        k = iv.get("kpis") or {}
        for code, key, unit in (("inventory_turnover", "inventory_turnover", "مرة"), ("dio", "dio", "يوم"), ("stockout_rate", "stockout_rate", "%")):
            v = (k.get(key) or {}).get("current")
            if v is not None:
                put(code, _f(v), (k.get(key) or {}).get("window") or "آخر لقطة", None, [f"{(k.get(key) or {}).get('name_ar') or code}: {v}"])
            else:
                na(code, (k.get(key) or {}).get("reason_ar") or "يلزم تكلفة الوحدة والكميات المباعة")
        val = (k.get("inventory_value") or {}).get("current")
        sm = sum(x for x in ((k.get("slow_moving_value") or {}).get("current"), (k.get("obsolete_value") or {}).get("current")) if x is not None)
        if val:
            put("slow_moving_pct", _pct(sm, val), "آخر لقطة", None, [f"راكد/متقادم {sm:,.0f} من مخزون {val:,.0f}"])
        else:
            na("slow_moving_pct", "قيمة المخزون غير متاحة")
        ctx["inventory_value"] = val
    else:
        for c in ("inventory_turnover", "dio", "stockout_rate", "slow_moving_pct"):
            na(c, "لا توجد بيانات مخزون")
    # ── العمليات
    if _avail(op):
        k = op.get("kpis") or {}
        brs = op.get("branches") or []
        for code, key, bk in (("fulfillment_time", "fulfillment", "fulfillment_avg"), ("on_time", "on_time", "on_time"), ("order_accuracy", "accuracy", "accuracy")):
            m = k.get(key) or {}
            if m.get("value") is not None:
                put(code, _f(m["value"], 1), f"شهر {op.get('period')}", 1, [f"{m.get('name_ar')}: {m['value']} {m.get('unit') or ''} (تغطية {m.get('coverage_pct')}%)"],
                    by_branch={b["key"]: b[bk] for b in brs if b.get(bk) is not None}, coverage=m.get("coverage_pct"))
            else:
                na(code, m.get("reason_ar") or "بيانات الطلبات غير كافية")
        sla = op.get("sla") or {}
        if sla.get("available") and sla.get("compliance") is not None:
            put("sla_compliance", _f(sla["compliance"], 1), f"شهر {op.get('period')}", 1, [f"ملتزم {sla.get('met')} · متجاوز {sla.get('breached')}"],
                by_branch={b["key"]: b["compliance"] for b in sla.get("by_branch") or [] if b.get("compliance") is not None})
        else:
            na("sla_compliance", "SLA غير محدد")
    else:
        for c in ("fulfillment_time", "on_time", "order_accuracy", "sla_compliance"):
            na(c, "لا توجد بيانات عمليات")
    # ── العملاء
    cu = customers or {}
    if cu.get("customers_previous") and cu.get("lost_customers") is not None:
        put("customer_retention", round(100 - 100 * cu["lost_customers"] / cu["customers_previous"], 1), "آخر 90 يوماً مقابل الـ90 قبلها", 6,
            [f"{cu['customers_previous'] - cu['lost_customers']} من {cu['customers_previous']} عميلاً اشتروا مجدداً"])
    else:
        na("customer_retention", cu.get("customer_reason_ar") or cu.get("reason_ar") or "يلزم عمود «العميل» في المبيعات لـ 180 يوماً")
    if cu.get("repeat_rate_current") is not None:
        put("repeat_purchase", cu["repeat_rate_current"], "آخر 90 يوماً", 3, [f"عملاء اشتروا مرتين فأكثر: {cu['repeat_rate_current']}%"])
    else:
        na("repeat_purchase", cu.get("customer_reason_ar") or "يلزم عمود «العميل» في المبيعات")
    # ── الموارد البشرية
    if _avail(hr):
        p = hr.get("productivity") or {}
        rpe, ppe = _n(p.get("revenue_per_employee")), _n(p.get("profit_per_employee"))
        put("revenue_per_employee", _m(rpe), p.get("method_ar") or "آخر 12 شهراً", 12, [p.get("method_ar")]) if rpe else na("revenue_per_employee", "يلزم المبيعات وعدد الموظفين")
        put("profit_per_employee", _m(ppe), "آخر 12 شهراً", 12, []) if ppe else na("profit_per_employee", p.get("profit_note_ar") or "يلزم صافي الربح السنوي")
        t = _g(hr, "kpis", "turnover", "current")
        put("employee_turnover", _f(t, 1), "12 شهراً", 12, [_g(hr, "turnover", "method_ar")],
            by_branch={x["key"]: x["rate"] for x in _g(hr, "turnover", "by_branch") or [] if x.get("rate") is not None}) if t is not None else na("employee_turnover", "يلزم تاريخ انتهاء الخدمة")
    else:
        for c in ("revenue_per_employee", "profit_per_employee", "employee_turnover"):
            na(c, "لا توجد بيانات موظفين")
    # ── السيولة
    dso = _g(cf, "collections", "dso") if _avail(cf) else None
    put("dso", _f(dso, 0), "الذمم القائمة ÷ فواتير 90 يوماً × 90", 3, [_g(cf, "collections", "dso_method_ar")]) if dso is not None else na("dso", "يلزم ملف الذمم المدينة")
    # ── مؤشرات القطاع (من طبقة القطاع في الوحدة المالية — نفس المصدر)
    for k in _g(fn, "sector", "kpis") or []:
        code = "sector:" + k["code"]
        meta = SECTOR_KPI_META.get(k["code"], ("lower", (1, 5, "abs")))
        if k.get("value") is not None:
            put(code, k["value"], "الشهر الحالي", 1, [k.get("formula_ar")], series=[(s["period"], s["value"]) for s in k.get("series") or [] if s.get("value") is not None])
        else:
            na(code, "يحتاج بيانات المؤشر: " + (k.get("formula_ar") or ""))
        out[code].update({"_meta": {"g": "sector", "ar": k["name_ar"], "unit": "%" if k.get("unit", "percent") == "percent" else "SAR",
                                    "dir": "lower" if meta[0] == "lower" else "higher", "tol": meta[1], "basis": "month"}})
    return out, ctx


def meta_of(code, cm=None):
    if code in METRICS:
        return METRICS[code]
    m = ((cm or {}).get(code) or {}).get("_meta")
    if m:
        return m
    k = code.split(":", 1)[-1]
    if code.startswith("sector:") and k in SECTOR_KPI_META:
        d, tol = SECTOR_KPI_META[k]
        return {"g": "sector", "ar": SECTOR_KPI_AR.get(k, k), "unit": "SAR" if k == "revenue_per_employee" else "%", "dir": d, "tol": tol, "basis": "month"}
    return None


# ═══════════════════════════════════════════════════════════
# 3.5.3 + 3.5.4 — طبقة المعايير: المصدر والمنهجية والثقة والحداثة
# كل سجل معيار: metric, sector, sub_sector, country, region, city, size_segment, business_model, period, period_end, basis,
#               value, p10..p90, sample_size, source, source_url, methodology, confidence, published_on, version, origin
# ═══════════════════════════════════════════════════════════
REQUIRED_FIELDS = ("metric", "sector", "period", "value", "source", "methodology", "confidence")
STALE_MONTHS = 24


def validate_benchmark(b):
    """يرفض أي معيار بلا مصدر أو منهجية أو فترة أو ثقة. يرجع قائمة أخطاء (فارغة = سليم)."""
    errs = [f"حقل مطلوب: {f}" for f in REQUIRED_FIELDS if b.get(f) in (None, "")]
    if b.get("confidence") not in (None, "") and b.get("confidence") not in CONF_ORDER:
        errs.append("الثقة: high / medium / low")
    if b.get("value") not in (None, "") and to_decimal(b.get("value")) is None:
        errs.append("القيمة غير رقمية")
    ps = [to_decimal(b.get(k)) for k in ("p10", "p25", "p50", "p75", "p90") if b.get(k) not in (None, "")]
    if any(p is None for p in ps) or ps != sorted(ps):
        errs.append("المئينات يجب أن تكون أرقاماً تصاعدية")
    if b.get("sample_size") not in (None, "") and (to_decimal(b.get("sample_size")) is None or to_decimal(b.get("sample_size")) < 1):
        errs.append("حجم العيّنة غير صالح")
    if b.get("size_segment") not in (None, "", "all") and b.get("size_segment") not in SIZE_AR:
        errs.append("الحجم: micro / small / medium / large / all")
    return errs


def _age_months(b, today):
    d = RE._parse_day(b.get("period_end") or b.get("published_on"))
    if d is None:
        p = str(b.get("period") or "")
        d = RE._parse_day(p[:4] + "-12-31") if p[:4].isdigit() else None
    return None if d is None else max(0, (today.year - d.year) * 12 + today.month - d.month)


def effective_confidence(b, today):
    """الثقة الفعلية = ثقة المصدر، تنخفض درجة إذا: قديم (> 24 شهراً) · عيّنة < 10 · معيار عام لكل القطاعات."""
    c = CONF_ORDER.index(b.get("confidence") if b.get("confidence") in CONF_ORDER else "low")
    notes = []
    age = _age_months(b, today)
    if age is not None and age > STALE_MONTHS:
        c -= 1; notes.append(f"بيانات قديمة ({age} شهراً)")
    n = to_decimal(b.get("sample_size"))
    if n is not None and n < 10:
        c -= 1; notes.append(f"عيّنة محدودة ({int(n)})")
    if b.get("sector") == "all":
        c -= 1; notes.append("معيار عام لكل القطاعات")
    c = max(0, c)
    return CONF_ORDER[c], notes, age


def _geo_level(b):
    if b.get("city"):
        return "local"
    if b.get("region"):
        return "regional"
    return "national"


def candidates(metric, datasets, profile, basis=None):
    """المعايير المطابقة للمؤشر والقطاع والجغرافيا والحجم ونموذج العمل — الأخص أولاً."""
    out = []
    for b in datasets or []:
        if b.get("metric") != metric or not b.get("is_active", True):
            continue
        if b.get("sector") not in (profile.get("sector"), "all"):
            continue
        if b.get("sub_sector") and b["sub_sector"] != profile.get("sub_sector"):
            continue
        if (b.get("country") or "SA") != (profile.get("country") or "SA"):
            continue
        if b.get("region") and b["region"] != profile.get("region"):
            continue
        if b.get("city") and (b["city"] or "").strip().lower() != (profile.get("city") or "").strip().lower():
            continue
        seg = b.get("size_segment") or "all"
        if seg != "all" and seg != profile.get("size_segment"):
            continue
        if b.get("business_model") and b["business_model"] != profile.get("business_model"):
            continue
        if basis == "yoy" and (b.get("basis") or "annual") not in ("yoy", "annual"):
            continue
        spec = {"local": 3, "regional": 2, "national": 1}[_geo_level(b)] + (1 if seg != "all" else 0) + (1 if b.get("sub_sector") else 0) \
            + (1 if b.get("business_model") else 0) + (0 if b.get("sector") != "all" else -2)
        out.append((spec, b))
    def year(b):
        m = re.search(r"(19|20)\d{2}", str(b.get("period_end") or b.get("period") or ""))
        return int(m.group(0)) if m else 0
    return [b for _, b in sorted(out, key=lambda x: (-x[0], -CONF_ORDER.index(x[1].get("confidence") if x[1].get("confidence") in CONF_ORDER else "low"), -year(x[1])))]


# ── أقران نبّاه: تجميع مجهّل بحد أدنى 5 شركات موافقة
PEER_MIN = 5


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def peer_benchmarks(values_by_metric, sector, size_segment=None, period=None, minimum=PEER_MIN):
    """values_by_metric: {metric: [قيمة لكل شركة أخرى موافقة]} — يرجع معايير مجمّعة فقط (لا قيمة فردية)."""
    out = []
    for m, vals in (values_by_metric or {}).items():
        v = sorted(float(x) for x in vals if x is not None)
        if len(v) < minimum:
            continue
        n = len(v)
        out.append({"id": f"peer:{m}", "metric": m, "sector": sector, "size_segment": size_segment or "all", "period": period or "آخر تقييم",
                    "basis": "mixed", "value": round(_quantile(v, 0.5), 2), "p10": round(_quantile(v, .1), 2), "p25": round(_quantile(v, .25), 2),
                    "p50": round(_quantile(v, .5), 2), "p75": round(_quantile(v, .75), 2), "p90": round(_quantile(v, .9), 2), "sample_size": n,
                    "source": "شركات نبّاه المشاركة في المقارنة (بموافقتها)", "methodology": f"وسيط ومئينات آخر قيمة لكل شركة من {n} شركة — مجمّع ومجهّل",
                    "confidence": "high" if n >= 30 else "medium" if n >= 10 else "low", "origin": "peer", "published_on": None})
    return out


# ═══════════════════════════════════════════════════════════
# 3.5.5 → 3.5.9 — المقارنة، الفجوة، المئين والترتيب، الأثر المالي
# ═══════════════════════════════════════════════════════════
def percentile_of(value, b, direction):
    """مئين الأداء بالاستيفاء الخطي بين نقاط التوزيع المعلنة. بلا توزيع → None (لا ترتيب)."""
    pts = [(q, to_decimal(b.get(k))) for q, k in ((10, "p10"), (25, "p25"), (50, "p50"), (75, "p75"), (90, "p90")) if b.get(k) not in (None, "")]
    if len(pts) < 3 or value is None:
        return None
    v = float(value)
    pts = [(q, float(x)) for q, x in pts]
    if v <= pts[0][1]:
        p = pts[0][0] * (v / pts[0][1]) if pts[0][1] > 0 and v > 0 else max(1.0, pts[0][0] / 2)
        p = min(p, pts[0][0])
    elif v >= pts[-1][1]:
        p = pts[-1][0] + (100 - pts[-1][0]) / 2
    else:
        p = None
        for (q1, x1), (q2, x2) in zip(pts, pts[1:]):
            if x1 <= v <= x2:
                p = q1 + (q2 - q1) * ((v - x1) / (x2 - x1) if x2 != x1 else 0.5)
                break
    p = max(1.0, min(99.0, p))
    return round(p if direction == "higher" else 100 - p, 0)


def rank_of(pct):
    if pct is None:
        return None
    return "top" if pct >= 75 else "above_avg" if pct >= 55 else "average" if pct >= 45 else "below_avg" if pct >= 25 else "critical"


def position_of(value, bench, meta):
    near, crit, mode = meta["tol"]
    gap = float(value) - float(bench)
    worse = -gap if meta["dir"] == "higher" else gap          # موجب = أسوأ
    size = abs(worse) if mode == "abs" else (abs(worse) / abs(float(bench)) * 100 if float(bench) else abs(worse))
    if size <= near:
        return "near"
    if worse < 0:
        return "above"
    return "critical" if size >= crit else "below"


IMPACT_RULES = {
    "gross_margin": ("margin", "الفجوة بالنقاط × إيراد آخر 12 شهراً"), "net_margin": ("margin", "الفجوة بالنقاط × إيراد آخر 12 شهراً"),
    "ebitda_margin": ("margin", "الفجوة بالنقاط × إيراد آخر 12 شهراً"), "expense_ratio": ("margin", "الفجوة بالنقاط × إيراد آخر 12 شهراً"),
    "revenue_growth": ("growth", "(نمو المعيار − نمو الشركة) × إيراد السنة السابقة"),
    "discount_rate": ("gross", "الفجوة بالنقاط × إجمالي مبيعات 12 شهراً"), "return_rate": ("gross", "الفجوة بالنقاط × إجمالي مبيعات 12 شهراً"),
    "dio": ("cash_days_cogs", "الأيام الزائدة × تكلفة المبيعات اليومية — نقد محتجز في المخزون وليس ربحاً"),
    "dso": ("cash_days_rev", "الأيام الزائدة × الإيراد اليومي — نقد محتجز في الذمم وليس ربحاً"),
    "aov": ("aov", "(متوسط المعيار − متوسط الشركة) × عدد الطلبات — توضيحي، يفترض ثبات عدد الطلبات"),
}


def gap_impact(code, company, bench, meta, ctx):
    """فرصة توضيحية مبنية على فجوة المعيار — ليست استرداداً مضموناً. فقط حين الشركة أسوأ ومنهجية الحساب مدعومة."""
    worse = (float(bench) - float(company)) if meta["dir"] == "higher" else (float(company) - float(bench))
    if worse <= 0:
        return None
    kind = IMPACT_RULES.get(code, ("margin", "الفجوة بالنقاط × إيراد آخر 12 شهراً") if code.startswith("sector:") and meta["unit"] == "%" else None)
    if not kind:
        return None
    k, basis = kind
    amt, typ = None, "profit"
    if k == "margin" and ctx.get("revenue_ttm"):
        amt = worse / 100 * ctx["revenue_ttm"]
    elif k == "growth" and ctx.get("revenue_prev_ttm"):
        amt = worse / 100 * ctx["revenue_prev_ttm"]; typ = "revenue"
    elif k == "gross" and ctx.get("gross_ttm"):
        amt = worse / 100 * ctx["gross_ttm"]
    elif k == "cash_days_cogs" and ctx.get("cogs_ttm"):
        amt = worse * ctx["cogs_ttm"] / 365; typ = "cash"
    elif k == "cash_days_rev" and ctx.get("revenue_ttm"):
        amt = worse * ctx["revenue_ttm"] / 365; typ = "cash"
    elif k == "aov" and ctx.get("transactions_ttm"):
        amt = worse * ctx["transactions_ttm"]; typ = "revenue"
    if amt is None:
        return {"amount": None, "reason_ar": "أساس الحساب غير متاح (إيراد/تكلفة 12 شهراً)"}
    return {"amount": round(amt, 0), "type": typ, "type_ar": {"profit": "فرصة ربح توضيحية", "revenue": "فرصة إيراد توضيحية", "cash": "نقد محتجز توضيحي"}[typ],
            "basis_ar": basis, "note_ar": "فرصة توضيحية مبنية على فجوة المعيار — ليست استرداداً مضموناً"}


def compare(code, cm, datasets, profile, today, ctx):
    meta = meta_of(code, cm)
    c = cm.get(code) or {"value": None, "reason_ar": "غير محسوب"}
    base = {"metric": code, "name_ar": meta["ar"], "group": meta["g"], "group_ar": GROUPS[meta["g"]], "unit": meta["unit"], "direction": meta["dir"],
            "company": c.get("value"), "company_basis_ar": c.get("basis_ar"), "company_evidence": c.get("evidence") or [], "company_reason_ar": c.get("reason_ar"),
            "by_branch": c.get("by_branch") or {}, "series": c.get("series") or []}
    if c.get("value") is None:
        return {**base, "status": "company_unavailable", "status_ar": "قيمة الشركة غير متاحة"}
    cands = candidates(code, datasets, profile, meta.get("basis"))
    if not cands:
        return {**base, "status": "benchmark_unavailable", "status_ar": "المعيار غير متاح",
                "reason_ar": "لا يوجد معيار موثّق لهذا المؤشر يطابق قطاعك وجغرافيتك وحجمك — لا نعرض متوسطاً مخترعاً"}
    b = cands[0]
    conf, notes, age = effective_confidence(b, today)
    v, bv = float(c["value"]), float(b["value"])
    gap = round(v - bv, 2)
    pos = position_of(v, bv, meta)
    pct = percentile_of(v, b, meta["dir"])
    levels = {}
    for x in cands:
        lv = _geo_level(x)
        if lv not in levels:
            levels[lv] = {"value": x["value"], "source": x.get("source"), "period": x.get("period"), "origin": x.get("origin", "platform"),
                          "gap": round(v - float(x["value"]), 2), "position": position_of(v, float(x["value"]), meta),
                          "percentile": percentile_of(v, x, meta["dir"]), "geo_ar": GEO_AR[lv], "size_ar": SIZE_AR.get(x.get("size_segment"), "كل الأحجام")}
    m_months = c.get("months")
    c_conf = "high" if (m_months or 12) >= 12 else "medium" if (m_months or 0) >= 3 else "low"
    if c.get("coverage") is not None and c["coverage"] < 80:
        c_conf = "low"
    final = CONF_ORDER[min(CONF_ORDER.index(conf), CONF_ORDER.index(c_conf))]
    return {**base, "status": "compared", "benchmark": bv, "gap": gap, "gap_pct": round(gap / bv * 100, 1) if bv else None,
            "gap_unit": "نقطة" if meta["unit"] == "%" else meta["unit"], "position": pos, "position_ar": POS_AR[pos],
            "percentile": pct, "rank": rank_of(pct), "rank_ar": RANK_AR.get(rank_of(pct)) if pct is not None else None,
            "rank_reason_ar": None if pct is not None else "لا ترتيب ولا مئين: المعيار بلا توزيع منشور",
            "impact": gap_impact(code, v, bv, meta, ctx),
            "source": {"id": b.get("id"), "origin": b.get("origin", "platform"), "origin_ar": ORIGIN_AR.get(b.get("origin", "platform")),
                       "source": b.get("source"), "source_url": b.get("source_url"), "period": b.get("period"), "methodology": b.get("methodology"),
                       "sample_size": b.get("sample_size"), "sector": b.get("sector"), "sub_sector": b.get("sub_sector"), "geo_level": _geo_level(b),
                       "geo_ar": GEO_AR[_geo_level(b)], "geography": b.get("city") or (REGIONS.get(b.get("region")) if b.get("region") else (b.get("country") or "SA")),
                       "size_segment": b.get("size_segment") or "all", "size_ar": SIZE_AR.get(b.get("size_segment"), "كل الأحجام"),
                       "business_model": b.get("business_model"), "version": b.get("version"), "age_months": age,
                       "freshness_ar": None if age is None else ("حديث" if age <= 12 else "مقبول" if age <= STALE_MONTHS else "قديم")},
            "benchmark_confidence": conf, "benchmark_confidence_ar": CONF_AR[conf], "confidence_notes": notes,
            "company_confidence": c_conf, "confidence": final, "confidence_ar": CONF_AR[final], "geo_levels": levels,
            "same_size": (b.get("size_segment") or "all") != "all"}


# ═══════════════════════════════════════════════════════════
# 3.5.17 → 3.5.19 — الربط بالمخاطر والمسببات والأسباب الجذرية
# ═══════════════════════════════════════════════════════════
METRIC_DRIVERS = {
    "gross_margin": ["cost_increase_pct", "gross_margin_drop_pp", "x_discount_leakage", "x_low_margin_products", "x_returns_leakage"],
    "net_margin": ["net_margin_pct", "x_excess_opex", "leakage_pct", "cost_increase_pct", "x_branch_underperformance"],
    "ebitda_margin": ["net_margin_pct", "x_excess_opex", "gross_margin_drop_pp"],
    "expense_ratio": ["x_excess_opex", "x_payroll_pressure"],
    "revenue_growth": ["revenue_decline_pct", "lost_customer_revenue_pct", "x_new_customer_decline", "x_price_increase"],
    "aov": ["x_price_increase", "x_discount_leakage"],
    "discount_rate": ["x_discount_leakage"], "return_rate": ["x_returns_leakage", "defect_rate_pct"],
    "inventory_turnover": ["stockout_items", "supplier_late_pct"], "dio": ["supplier_late_pct", "stockout_items"],
    "stockout_rate": ["stockout_items", "supplier_late_pct", "supplier_concentration_pct"], "slow_moving_pct": [],
    "fulfillment_time": ["on_time_gap_pp", "x_bottleneck", "utilization_pct"], "on_time": ["on_time_gap_pp", "x_bottleneck", "utilization_pct", "turnover_pct"],
    "order_accuracy": ["x_order_errors", "defect_rate_pct"], "sla_compliance": ["on_time_gap_pp", "x_bottleneck"],
    "customer_retention": ["lost_customer_revenue_pct", "repeat_rate_drop_pp", "on_time_gap_pp", "stockout_items"],
    "repeat_purchase": ["repeat_rate_drop_pp", "x_price_increase"],
    "revenue_per_employee": ["revenue_decline_pct", "turnover_pct"], "profit_per_employee": ["net_margin_pct"],
    "employee_turnover": ["turnover_pct"], "dso": ["dso_days", "overdue_ar_pct", "customer_concentration_pct"],
    "sector:food_cost_pct": ["cost_increase_pct", "x_low_margin_products"], "sector:cogs_pct": ["cost_increase_pct"],
    "sector:labor_pct": ["x_payroll_pressure", "turnover_pct"], "sector:discount_pct": ["x_discount_leakage"], "sector:returns_pct": ["x_returns_leakage"],
}
OPPORTUNITIES = [
    {"id": "margin", "ar": "تحسين الهامش الإجمالي", "metrics": ["gross_margin", "sector:food_cost_pct", "sector:cogs_pct", "discount_rate", "return_rate"],
     "areas_ar": "المشتريات + مزيج المنتجات + الخصومات", "links": ["company-purchases-intelligence.html#cost", "company-leakage-intelligence.html#discount"]},
    {"id": "opex", "ar": "ضبط المصروفات التشغيلية", "metrics": ["expense_ratio", "net_margin", "ebitda_margin", "sector:labor_pct"],
     "areas_ar": "المصروفات + العمالة", "links": ["company-financial-intelligence.html#expenses"]},
    {"id": "aov", "ar": "رفع متوسط قيمة الطلب", "metrics": ["aov"], "areas_ar": "المبيعات + التسعير + الحزم", "links": ["company-sales-intelligence.html#products"]},
    {"id": "growth", "ar": "تسريع النمو", "metrics": ["revenue_growth", "revenue_per_employee"], "areas_ar": "الاستقطاب + متوسط إنفاق العميل",
     "links": ["company-sales-intelligence.html#trend"]},
    {"id": "inventory", "ar": "تقليل أيام المخزون", "metrics": ["dio", "inventory_turnover", "slow_moving_pct", "stockout_rate"],
     "areas_ar": "المخزون + المشتريات", "links": ["company-inventory-intelligence.html#reorder"]},
    {"id": "collection", "ar": "تسريع التحصيل", "metrics": ["dso"], "areas_ar": "الذمم + شروط الائتمان", "links": ["company-cashflow-intelligence.html#collections"]},
    {"id": "service", "ar": "تحسين التنفيذ والالتزام", "metrics": ["on_time", "fulfillment_time", "sla_compliance", "order_accuracy"],
     "areas_ar": "العمليات + الطاقة", "links": ["company-operations-intelligence.html#fulfillment"]},
    {"id": "retention", "ar": "رفع الاحتفاظ بالعملاء", "metrics": ["customer_retention", "repeat_purchase"], "areas_ar": "تجربة العميل + الولاء",
     "links": ["company-sales-intelligence.html#customers"]},
    {"id": "people", "ar": "استقرار الكوادر", "metrics": ["employee_turnover", "profit_per_employee"], "areas_ar": "الموارد البشرية", "links": ["company-hr-intelligence.html#turnover"]},
]


def _why(code, drivers_by):
    out = []
    for k in METRIC_DRIVERS.get(code, []):
        d = drivers_by.get(k)
        if d and d.get("level") in RE.ELEVATED:
            out.append({"key": k, "name_ar": d["name_ar"], "level": d["level"], "evidence": (d.get("evidence") or [""])[0],
                        "contribution_pct": (d.get("contribution") or {}).get("pct"),
                        "candidates": [{"name_ar": c["name_ar"], "confidence_ar": c["confidence_ar"]} for c in (d.get("candidates") or [])[:2]],
                        "action_ar": (d.get("recommendation") or {}).get("text_ar"), "link": f"company-risk-drivers.html#driver/{k}"})
    return out


def _trend_dir(vals, direction):
    v = [x for x in vals if x is not None][-6:]
    if len(v) < 3:
        return None
    n = len(v)
    xm = (n - 1) / 2
    ym = sum(v) / n
    den = sum((i - xm) ** 2 for i in range(n)) or 1
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(v)) / den
    scale = (abs(ym) or 1) * 0.01
    if abs(slope) < scale:
        return "stable"
    good = slope > 0 if direction == "higher" else slope < 0
    return "improving" if good else "worsening"


TREND_AR = {"improving": "يتحسن", "worsening": "يتراجع", "stable": "مستقر", None: "بيانات غير كافية"}


def sector_trend(code, datasets, profile):
    """اتجاه المعيار نفسه عبر فترات منشورة متعددة لنفس المستوى — لا اتجاه بأقل من فترتين."""
    cs = candidates(code, datasets, profile)
    if not cs:
        return None
    lvl = _geo_level(cs[0])
    same = [b for b in cs if _geo_level(b) == lvl and (b.get("size_segment") or "all") == (cs[0].get("size_segment") or "all") and b.get("origin", "platform") == cs[0].get("origin", "platform")]
    def key(b):
        m = re.search(r"(19|20)\d{2}", str(b.get("period_end") or b.get("period") or ""))
        return (int(m.group(0)) if m else 0, str(b.get("period")))
    pts = sorted({str(b.get("period")): b for b in same}.values(), key=key)
    if len(pts) < 2:
        return {"points": [{"period": b.get("period"), "value": b["value"]} for b in pts], "change": None, "note_ar": "فترة معيار واحدة — لا اتجاه للقطاع"}
    return {"points": [{"period": b.get("period"), "value": b["value"]} for b in pts], "change": round(float(pts[-1]["value"]) - float(pts[-2]["value"]), 2),
            "note_ar": f"من {pts[-2].get('period')} إلى {pts[-1].get('period')} — المصدر: {pts[-1].get('source')}"}


def position_history(code, history):
    rows = sorted([h for h in history or [] if h.get("metric") == code], key=lambda h: h["period"])
    by = {}
    for h in rows:
        by[h["period"]] = h
    pts = list(by.values())[-8:]
    if len(pts) < 2:
        return {"points": [{"period": p["period"], "percentile": p.get("percentile"), "gap": p.get("gap")} for p in pts], "signal": None}
    use_p = all(p.get("percentile") is not None for p in pts[-3:])
    seq = [p.get("percentile") if use_p else p.get("gap_signed") for p in pts[-3:]]
    sig = None
    if len(seq) == 3 and None not in seq:
        if seq[0] > seq[1] > seq[2]:
            sig = "losing"
        elif seq[0] < seq[1] < seq[2]:
            sig = "gaining"
    return {"points": [{"period": p["period"], "percentile": p.get("percentile"), "gap": p.get("gap")} for p in pts], "signal": sig,
            "signal_ar": {"losing": "الشركة تفقد موقعها في القطاع", "gaining": "الشركة تتقدم في القطاع", None: None}[sig],
            "basis_ar": "المئين" if use_p else "الفجوة عن المعيار"}


def _signed(c):
    if c.get("status") != "compared":
        return None
    return round(c["gap"] if c["direction"] == "higher" else -c["gap"], 2)


def _rel(c):
    """الفجوة النسبية الموقّعة (موجب = أفضل) لترتيب نقاط القوة والضعف."""
    s = _signed(c)
    if s is None:
        return None
    b = abs(float(c["benchmark"])) or 1
    return round(s / b * 100, 1) if c["unit"] != "%" else round(s / max(b, 1) * 100, 1)


def strategic_insights(cmp):
    """استنتاجات «قد» من تركيب الفجوات — اتجاه للبحث وليست أحكاماً."""
    P = lambda k: (cmp.get(k) or {}).get("position")
    bad, good = ("below", "critical"), ("above", "near")
    out = []
    if P("revenue_growth") in bad and P("customer_retention") in ("above",):
        out.append({"ar": "النمو أقل من القطاع بينما الاحتفاظ بالعملاء أعلى منه — المشكلة قد لا تكون في الاحتفاظ، بل في استقطاب عملاء جدد أو متوسط إنفاق العميل.",
                    "look_ar": "راجع العملاء الجدد ومتوسط قيمة الطلب", "metrics": ["revenue_growth", "customer_retention", "aov"]})
    if P("gross_margin") in bad and P("discount_rate") in good and P("return_rate") in (None,) + good:
        out.append({"ar": "الهامش الإجمالي أقل من القطاع رغم أن الخصومات ضمن المعيار — الفجوة قد تكون في جانب التكلفة (أسعار الشراء أو مزيج الأصناف).",
                    "look_ar": "راجع أسعار الموردين والأصناف منخفضة الهامش", "metrics": ["gross_margin", "discount_rate"]})
    if P("gross_margin") in bad and P("discount_rate") in bad:
        out.append({"ar": "الهامش الإجمالي والخصومات كلاهما أسوأ من القطاع — سياسة الخصم قد تكون جزءاً من فجوة الهامش.",
                    "look_ar": "راجع الخصومات حسب الفرع والصنف", "metrics": ["gross_margin", "discount_rate"]})
    if P("net_margin") in bad and P("gross_margin") in good:
        out.append({"ar": "صافي الهامش أقل من القطاع بينما الهامش الإجمالي ضمنه — الفجوة قد تكون في المصروفات التشغيلية وليست في التسعير.",
                    "look_ar": "راجع نسبة المصروفات وأكبر بنودها", "metrics": ["net_margin", "gross_margin", "expense_ratio"]})
    if P("dio") in bad and P("stockout_rate") in bad:
        out.append({"ar": "أيام المخزون أعلى من القطاع والنفاد أعلى أيضاً — قد يكون الخلل في مزيج المخزون (أصناف راكدة وأخرى ناقصة) لا في حجمه.",
                    "look_ar": "راجع الراكد مقابل النافد حسب الصنف", "metrics": ["dio", "stockout_rate"]})
    if P("on_time") in bad and P("employee_turnover") in bad:
        out.append({"ar": "التسليم في الموعد ودوران الموظفين كلاهما أسوأ من القطاع — قد يرتبط الأداء التشغيلي باستقرار الفريق.",
                    "look_ar": "قارن الفروع الأعلى دوراناً بالأقل التزاماً", "metrics": ["on_time", "employee_turnover"]})
    return out


def analyze_benchmark(mods, *, sales_rows=None, risk=None, drivers=None, datasets=None, peers=None, profile=None, history=None,
                      today=None, categories=None, currency="SAR"):
    """mods: نتائج الوحدات · risk: نتيجة 3.3 · drivers: نتيجة 3.4 · datasets: معايير موثّقة (منصّة + الشركة) ·
    peers: معايير الأقران المجمّعة · profile: ملف القطاع · history: مقارنات سابقة محفوظة · categories: نطاق الدور."""
    today = today or date.today()
    profile = dict(profile or {})
    mods = mods or {}
    cm, ctx = company_metrics(mods, sales_rows, (risk or {}).get("customers"))
    if not profile.get("size_segment"):
        profile["size_segment"] = size_segment(ctx.get("revenue_ttm") and ctx["revenue_ttm"] * (12 / max(ctx.get("months_fin") or 12, 1)))
    if profile.get("city") and not profile.get("region"):
        profile["region"] = region_of(profile["city"])
    sector = profile.get("sector") or "other"
    scope = categories or RE.CAT_ORDER
    allds = list(datasets or []) + list(peers or [])
    codes = [c for c in list(METRICS) + [k for k in cm if k.startswith("sector:")] if GROUP_CAT[meta_of(c, cm)["g"]] in scope]
    cmp = {c: compare(c, cm, allds, profile, today, ctx) for c in codes}
    drivers_by = {d["key"]: d for d in (drivers or {}).get("drivers") or []}
    risk_cats = {c["key"]: c for c in (risk or {}).get("categories") or []}
    for c, x in cmp.items():
        x["why"] = _why(c, drivers_by) if x.get("position") in ("below", "critical") else []
        x["risk_link"] = {"category": GROUP_CAT[x["group"]], "category_ar": RE.CATS[GROUP_CAT[x["group"]]]["ar"],
                          "level": (risk_cats.get(GROUP_CAT[x["group"]]) or {}).get("level"), "score": (risk_cats.get(GROUP_CAT[x["group"]]) or {}).get("score")}
        x["company_trend"] = TREND_AR[_trend_dir([v for _, v in x.get("series") or []], x["direction"])]
        x["sector_trend"] = sector_trend(c, allds, profile) if x["status"] == "compared" else None
        x["history"] = position_history(c, history)
        x["signed_gap"] = _signed(x)
        x["rel_gap"] = _rel(x)
    compared = [x for x in cmp.values() if x["status"] == "compared"]
    strengths = sorted([x for x in compared if x["position"] == "above"], key=lambda x: -(x["rel_gap"] or 0))
    weaknesses = sorted([x for x in compared if x["position"] in ("below", "critical")], key=lambda x: (x["position"] != "critical", x["rel_gap"] or 0))
    focus = SECTOR_BENCH.get(sector, SECTOR_BENCH["other"])
    return {
        "has_data": bool(compared) or any(v.get("value") is not None for v in cm.values()), "version": f"bench-v{BENCH_VERSION}", "as_of": today.isoformat(),
        "currency": currency, "profile": _profile_view(profile, ctx),
        "summary": _summary(cmp, strengths, weaknesses),
        "comparisons": cmp, "groups": [{"key": g, "ar": a, "metrics": [c for c in codes if meta_of(c, cm)["g"] == g]} for g, a in GROUPS.items()
                                        if any(meta_of(c, cm)["g"] == g for c in codes)],
        "strengths": [_brief(x) for x in strengths], "weaknesses": [_brief(x) for x in weaknesses],
        "opportunities": opportunities(cmp, drivers_by),
        "insights": strategic_insights(cmp),
        "branches": branch_benchmark(cmp),
        "positioning": positioning(cmp),
        "alerts": alerts(cmp),
        "signals": signals(cmp, today),
        "sector_config": {"sector": sector, "ar": RE.sector_profile(sector)["ar"], "focus": [{"metric": f, "name_ar": meta_of(f, cm)["ar"] if meta_of(f, cm) else f,
                                                                                              "status": (cmp.get(f) or {}).get("status", "company_unavailable")} for f in focus["focus"]],
                          "needs": [{"ar": a, "needs_ar": n} for a, n in focus["needs"]], "note_ar": "محرك مقارنة واحد + إعداد لكل قطاع"},
        "data_needed": [{"key": k, "group_ar": GROUPS[g], "ar": a, "needs_ar": n} for k, (g, a, n) in DATA_NEEDED.items() if GROUP_CAT[g] in scope],
        "quality": _quality(cmp, allds),
        "rules": {"position_ar": "قريب = ضمن هامش التسامح للمؤشر · فجوة حرجة = أسوأ بأكثر من الحد الحرج · الترتيب والمئين فقط عند وجود توزيع منشور للمعيار",
                  "rank_ar": "متقدم ≥ 75 · فوق المتوسط ≥ 55 · متوسط ≥ 45 · دون المتوسط ≥ 25 · فجوة حرجة < 25", "size_ar": SIZE_RULE_AR,
                  "confidence_ar": "ثقة المقارنة = الأدنى بين ثقة المعيار (تنخفض للقديم > 24 شهراً، والعيّنة < 10، والمعيار العام) وثقة قيمة الشركة",
                  "peer_ar": f"معيار الأقران: شركات نبّاه في نفس القطاع التي وافقت على المشاركة، بحد أدنى {PEER_MIN} شركات، مجمّع ومجهّل — لا تُعرض قيمة أي شركة",
                  "impact_ar": "الأثر المالي «فرصة توضيحية» من فجوة المعيار — ليس استرداداً مضموناً"},
        "snapshot": [{"metric": c, "company_value": x["company"], "benchmark_value": x.get("benchmark"), "gap": x.get("gap"), "gap_signed": x["signed_gap"],
                      "gap_pct": x.get("gap_pct"), "percentile": x.get("percentile"), "position": x.get("position"), "confidence": x.get("confidence"),
                      "origin": (x.get("source") or {}).get("origin"), "benchmark_id": (x.get("source") or {}).get("id")}
                     for c, x in cmp.items() if x["company"] is not None],
        "ai_questions": ["كيف أداء شركتي مقارنة بالقطاع؟", "ما أكبر فجوة ولماذا؟", "أين نحن أفضل من القطاع؟", "هل المشكلة مالية أم تشغيلية؟",
                         "أي فرع لديه أكبر فجوة؟", "ما الذي لو عالجناه سيقربنا من المعيار؟"],
        "disclaimer_ar": "المقارنة تعتمد فقط على معايير موثّقة بمصدرها. المؤشر بلا معيار يظهر «المعيار غير متاح» ولا يُقارن بمتوسط مخترع.",
    }


def _profile_view(p, ctx):
    return {"sector": p.get("sector"), "sector_ar": RE.sector_profile(p.get("sector"))["ar"], "sub_sector": p.get("sub_sector"),
            "country": p.get("country") or "SA", "region": p.get("region"), "region_ar": REGIONS.get(p.get("region")), "city": p.get("city"),
            "size_segment": p.get("size_segment"), "size_ar": SIZE_AR.get(p.get("size_segment"), "غير محدد"), "business_model": p.get("business_model"),
            "revenue_ttm": ctx.get("revenue_ttm"), "peer_opt_in": bool(p.get("peer_opt_in")), "size_source_ar": "إعداد الشركة" if p.get("size_override") else "من إيراد آخر 12 شهراً"}


def _brief(x):
    return {"metric": x["metric"], "name_ar": x["name_ar"], "group_ar": x["group_ar"], "company": x["company"], "benchmark": x["benchmark"], "gap": x["gap"],
            "gap_unit": x["gap_unit"], "rel_gap": x["rel_gap"], "position": x["position"], "position_ar": x["position_ar"], "confidence_ar": x["confidence_ar"],
            "unit": x["unit"], "impact": x.get("impact"), "why": x.get("why") or [], "percentile": x.get("percentile")}


def _summary(cmp, s, w):
    xs = list(cmp.values())
    comp = [x for x in xs if x["status"] == "compared"]
    pcts = [x["percentile"] for x in comp if x.get("percentile") is not None]
    return {"metrics": len(xs), "compared": len(comp), "benchmark_unavailable": sum(1 for x in xs if x["status"] == "benchmark_unavailable"),
            "company_unavailable": sum(1 for x in xs if x["status"] == "company_unavailable"), "above": len(s),
            "near": sum(1 for x in comp if x["position"] == "near"), "below": sum(1 for x in comp if x["position"] == "below"),
            "critical": sum(1 for x in comp if x["position"] == "critical"),
            "median_percentile": sorted(pcts)[len(pcts) // 2] if pcts else None, "percentile_metrics": len(pcts),
            "biggest_gap": _brief(w[0]) if w else None, "biggest_strength": _brief(s[0]) if s else None,
            "illustrative_total_note_ar": "لا نجمع الفرص التوضيحية لأنها متداخلة (الهامش يشمل التكلفة والخصومات)"}


def opportunities(cmp, drivers_by):
    out = []
    for o in OPPORTUNITIES:
        ms = [cmp[m] for m in o["metrics"] if m in cmp and cmp[m].get("position") in ("below", "critical")]
        if not ms:
            continue
        imp = [m["impact"] for m in ms if (m.get("impact") or {}).get("amount")]
        best = max(imp, key=lambda i: i["amount"]) if imp else None
        drv = []
        for m in ms:
            for w in m.get("why") or []:
                if w["key"] not in [d["key"] for d in drv]:
                    drv.append(w)
        out.append({"id": o["id"], "ar": o["ar"], "areas_ar": o["areas_ar"], "links": o["links"],
                    "gaps": [{"metric": m["metric"], "name_ar": m["name_ar"], "company": m["company"], "benchmark": m["benchmark"], "gap": m["gap"],
                              "gap_unit": m["gap_unit"], "position_ar": m["position_ar"]} for m in ms],
                    "impact": best, "impact_note_ar": "أكبر فرصة توضيحية ضمن المجموعة (لا تُجمع لأنها متداخلة)" if len(imp) > 1 else None,
                    "drivers": drv[:5], "actions": [d["action_ar"] for d in drv if d.get("action_ar")][:3],
                    "priority": "critical" if any(m["position"] == "critical" for m in ms) else "high"})
    out.sort(key=lambda o: (o["priority"] != "critical", -((o.get("impact") or {}).get("amount") or 0)))
    return out


def branch_benchmark(cmp):
    rows = {}
    for c, x in cmp.items():
        if x["status"] != "compared":
            continue
        meta = {"dir": x["direction"], "tol": (METRICS.get(c) or {}).get("tol") or SECTOR_KPI_META.get(c.split(":")[-1], ("lower", (1, 5, "abs")))[1]}
        for b, v in (x.get("by_branch") or {}).items():
            if v is None:
                continue
            pos = position_of(v, x["benchmark"], meta)
            rows.setdefault(b, []).append({"metric": c, "name_ar": x["name_ar"], "value": v, "benchmark": x["benchmark"], "gap": round(float(v) - float(x["benchmark"]), 2),
                                           "unit": x["unit"], "position": pos, "position_ar": POS_AR[pos]})
    out = [{"branch": b, "items": sorted(v, key=lambda i: ["critical", "below", "near", "above"].index(i["position"])),
            "below": sum(1 for i in v if i["position"] in ("below", "critical")), "above": sum(1 for i in v if i["position"] == "above")} for b, v in rows.items()]
    out.sort(key=lambda r: (-r["below"], r["above"]))
    return {"rows": out, "rule_ar": "قيمة الفرع مقابل نفس معيار الشركة (نفس المصدر والمستوى) — لا يوجد معيار على مستوى الفرع"}


def positioning(cmp):
    """تموضع قطاعي: السعر (متوسط الطلب ÷ المعيار) × الأداء (متوسط الأداء النسبي للمؤشرات المقارنة). لا مواقع منافسين مخترعة."""
    comp = [x for x in cmp.values() if x["status"] == "compared" and x["metric"] != "aov"]
    perf = None
    if len(comp) >= 3:
        vals = [max(-50.0, min(50.0, x["rel_gap"] or 0)) for x in comp]
        perf = round(100 + sum(vals) / len(vals), 1)
    a = cmp.get("aov") or {}
    price = round(100 * float(a["company"]) / float(a["benchmark"]), 1) if a.get("status") == "compared" and a.get("benchmark") else None
    quad = None
    if perf is not None and price is not None:
        quad = ("premium" if price >= 100 else "value") + "_" + ("strong" if perf >= 100 else "weak")
    return {"price_index": price, "performance_index": perf, "metrics_used": len(comp), "quadrant": quad,
            "quadrant_ar": {"premium_strong": "سعر أعلى وأداء أعلى من القطاع", "premium_weak": "سعر أعلى وأداء أقل — خطر على القيمة المقدمة",
                            "value_strong": "سعر أقل وأداء أعلى — قد تكون هناك مساحة للتسعير", "value_weak": "سعر أقل وأداء أقل"}.get(quad),
            "reason_ar": None if quad else ("يلزم معيار متوسط الطلب لمحور السعر" if price is None else "يلزم 3 مؤشرات مقارنة على الأقل لمحور الأداء"),
            "competitors_note_ar": "لا نعرض مواقع منافسين بدون بيانات موثقة عنهم — هذا تموضع مقابل معيار القطاع (100 = المعيار)"}


def alerts(cmp):
    out = []
    for c, x in cmp.items():
        if x["status"] != "compared":
            continue
        h = (x.get("history") or {}).get("signal")
        if x["position"] in ("critical", "below") or x["position"] == "above" or h == "losing":
            kind = "critical" if x["position"] == "critical" else "below" if x["position"] == "below" else ("losing" if h == "losing" else "advantage")
            out.append({"metric": c, "name_ar": x["name_ar"], "kind": kind,
                        "kind_ar": {"critical": "🔴 فجوة حرجة", "below": "🟠 أقل من القطاع", "advantage": "🟢 ميزة قطاعية", "losing": "🟠 تراجع في الموقع"}[kind],
                        "text_ar": f"{x['name_ar']}: {x['company']} مقابل معيار {x['benchmark']} (الفجوة {x['gap']:+} {x['gap_unit']})",
                        "confidence_ar": x["confidence_ar"], "source": (x.get("source") or {}).get("source"), "why": x.get("why") or [], "impact": x.get("impact"),
                        "position_trend_ar": (x.get("history") or {}).get("signal_ar")})
    order = {"critical": 0, "below": 1, "losing": 2, "advantage": 3}
    out.sort(key=lambda a: order[a["kind"]])
    return out


def signals(cmp, today):
    out = []
    for c, x in cmp.items():
        if x["status"] != "compared" or x["position"] == "near":
            continue
        risk = x["position"] in ("below", "critical")
        out.append({"id": "bench-" + RE._rid(c, today.strftime("%Y-%m"))[5:], "type": "risk" if risk else "opportunity", "code": "sector_gap:" + c,
                    "source_module": "benchmark", "name_ar": ("فجوة عن القطاع: " if risk else "ميزة قطاعية: ") + x["name_ar"],
                    "severity": "high" if x["position"] == "critical" else ("medium" if risk else "low"), "dimension": None, "period": today.strftime("%Y-%m"),
                    "evidence": [f"الشركة {x['company']} مقابل معيار {x['benchmark']} ({(x.get('source') or {}).get('source')})", f"الثقة: {x['confidence_ar']}"],
                    "suggested_action_ar": (x.get("why") or [{}])[0].get("action_ar") or "راجع مسببات الفجوة في مركز المسببات",
                    "estimated_impact": {"value": x["impact"]["amount"], "type": x["impact"]["type"], "note_ar": x["impact"]["note_ar"]} if (x.get("impact") or {}).get("amount") else None,
                    "metric_id": c, "method": f"benchmark-v{BENCH_VERSION}"})
    return out


def _quality(cmp, ds):
    xs = list(cmp.values())
    comp = [x for x in xs if x["status"] == "compared"]
    conf = {k: sum(1 for x in comp if x["confidence"] == k) for k in CONF_ORDER}
    origins = {}
    for x in comp:
        o = (x.get("source") or {}).get("origin")
        origins[o] = origins.get(o, 0) + 1
    return {"coverage_pct": _pct(len(comp), len(xs)) if xs else None, "by_confidence": conf, "by_origin": {ORIGIN_AR.get(k, k): v for k, v in origins.items()},
            "datasets": len(ds or []), "stale": sum(1 for x in comp if (x.get("source") or {}).get("freshness_ar") == "قديم"),
            "note_ar": "التغطية = مؤشرات لها قيمة للشركة ومعيار موثّق معاً ÷ كل المؤشرات"}
