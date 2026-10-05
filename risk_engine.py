"""
NABBAH — Phase 3.3 · Risk Intelligence Engine (محرك المخاطر المركزي)
محرك حتمي واحد يقرأ نتائج الوحدات (2.5 → 3.2) من نفس البيانات الموحّدة ويحوّلها إلى:
محركات خطر بأدلة → درجات → فئات → مؤشر مخاطر عام مُفسَّر → سلاسل ارتباط → سجل مخاطر → تنبيهات وقرارات.

مبادئ صارمة:
- لا ذكاء اصطناعي هنا. كل رقم من قاعدة منشورة (القواعد قابلة للتعديل لكل شركة/قطاع).
- البيانات الناقصة ≠ صفر: المحرك يقول «تعذّر التحديد» مع السبب والبيانات المطلوبة.
- الأثر المالي مصنّف ولا يُجمع بين أنواعه: فعلي / محتمل / تعرّض / فرصة استرداد.
- الارتباط بين المخاطر علاقة متزامنة، لا يُثبت السببية.
- يقرأ الأصول ولا يعدّلها (Risk يُشير إلى مصدره في الوحدة الأصلية).
"""
from decimal import Decimal
from datetime import date, datetime, timedelta
import hashlib

from nabbah_finance import to_decimal, round_money, round_pct, safe_divide

RISK_VERSION = "1.0"
D0 = Decimal("0")

# ═══════════════════════════════════════════════════════════
# 3.3.1 — عقد بيانات المخاطر (Risk Data Contract)
# ═══════════════════════════════════════════════════════════
CATS = {
    "liquidity":   {"ar": "مخاطر السيولة", "en": "Liquidity", "icon": "💧"},
    "customer":    {"ar": "مخاطر فقدان العملاء", "en": "Customer loss", "icon": "👥"},
    "profit":      {"ar": "مخاطر انخفاض الربح", "en": "Profit decline", "icon": "📉"},
    "operational": {"ar": "المخاطر التشغيلية", "en": "Operational", "icon": "⚙️"},
    "compliance":  {"ar": "مخاطر الامتثال", "en": "Compliance", "icon": "🧾"},
}
CAT_ORDER = ["liquidity", "customer", "profit", "operational", "compliance"]
LEVELS = ["low", "medium", "high", "critical"]
LEVEL_AR = {"low": "منخفض", "medium": "متوسط", "high": "مرتفع", "critical": "حرج", None: "تعذّر التحديد"}
SEV_BANDS = {"critical": 85, "high": 65, "medium": 40}          # درجة → مستوى (للمؤشر والفئات والمحركات)
SEV_RULE_AR = "حرج ≥ 85 · مرتفع ≥ 65 · متوسط ≥ 40 · منخفض < 40"
IMPACT_TYPES = {
    "actual":    {"ar": "أثر فعلي", "rule_ar": "خسارة حدثت فعلاً في الفترة ومقاسة من البيانات"},
    "potential": {"ar": "أثر محتمل", "rule_ar": "قد يحدث إن استمر الوضع — تقدير بقاعدة معلنة"},
    "exposure":  {"ar": "تعرّض", "rule_ar": "مبلغ معرّض للخطر وليس خسارة (ذمم، اعتماد على مورد، ضريبة تحت المراجعة)"},
    "recovery":  {"ar": "فرصة استرداد", "rule_ar": "جزء قابل للاسترداد بقاعدة معلنة من مركز استرداد الأموال"},
}

# كل محرك خطر: الفئة، اتجاه السوء، الحدود (متوسط/مرتفع/حرج)، الوحدة، المصدر، رابط الدليل، عقدة السلسلة، وما يلزم لحسابه.
# الحدود قيم افتراضية منشورة — تُعدَّل لكل شركة من الإعدادات ولكل قطاع من ملف القطاع.
DRIVERS = {
    # السيولة
    "runway_months": {"cat": "liquidity", "ar": "مدة السيولة (Runway)", "dir": "below", "t": (6, 3, 1.5), "unit": "شهر",
                      "src": "cashflow", "link": "company-cashflow-intelligence.html#liquidity", "node": "runway",
                      "needs_ar": "كشف حركات بنكية بعمود الرصيد أو رصيد افتتاحي في إعدادات التدفق النقدي"},
    "dso_days": {"cat": "liquidity", "ar": "مدة التحصيل (DSO)", "dir": "above", "t": (45, 60, 90), "unit": "يوم",
                 "src": "cashflow", "link": "company-cashflow-intelligence.html#collections", "node": "dso",
                 "needs_ar": "ملف الذمم المدينة (الفاتورة، التاريخ، المبلغ، المدفوع)"},
    "overdue_ar_pct": {"cat": "liquidity", "ar": "الذمم المتأخرة من إجمالي الذمم", "dir": "above", "t": (20, 35, 50), "unit": "%",
                       "src": "cashflow", "link": "company-cashflow-intelligence.html#collections", "node": "overdue_ar",
                       "needs_ar": "ملف الذمم المدينة بتاريخ الاستحقاق"},
    # الربح
    "gross_margin_drop_pp": {"cat": "profit", "ar": "تراجع هامش الربح الإجمالي", "dir": "above", "t": (1, 3, 5), "unit": "نقطة",
                             "src": "finance", "link": "company-financial-intelligence.html#variance", "node": "gross_margin",
                             "needs_ar": "مبيعات فترتين متتاليتين مع تكلفة المنتجات"},
    "net_margin_pct": {"cat": "profit", "ar": "هامش صافي الربح", "dir": "below", "t": (5, 0, -5), "unit": "%",
                       "src": "finance", "link": "company-financial-intelligence.html#statement", "node": "net_margin",
                       "needs_ar": "المبيعات + تكلفة المنتجات + المصروفات"},
    "leakage_pct": {"cat": "profit", "ar": "تسرب الإيرادات من صافي المبيعات", "dir": "above", "t": (3, 8, 15), "unit": "%",
                    "src": "leakage", "link": "company-leakage-intelligence.html#overview", "node": "leakage",
                    "needs_ar": "مبيعات بأعمدة الخصم والمرتجعات أو ملف المصروفات"},
    "cost_increase_pct": {"cat": "profit", "ar": "ارتفاع أسعار الشراء", "dir": "above", "t": (5, 10, 20), "unit": "%",
                          "src": "purchases", "link": "company-purchases-intelligence.html#cost", "node": "supplier_cost",
                          "needs_ar": "مشتريات فترتين بسعر الوحدة لنفس الأصناف"},
    # التشغيل
    "stockout_items": {"cat": "operational", "ar": "أصناف نافدة الآن", "dir": "above", "t": (1, 3, 8), "unit": "صنف",
                       "src": "inventory", "link": "company-inventory-intelligence.html#stockouts", "node": "stockout",
                       "needs_ar": "لقطة مخزون بالكميات لكل فرع"},
    "supplier_concentration_pct": {"cat": "operational", "ar": "تركّز الإنفاق على أكبر مورد", "dir": "above", "t": (40, 60, 80), "unit": "%",
                                   "src": "purchases", "link": "company-purchases-intelligence.html#suppliers", "node": "supplier_concentration",
                                   "needs_ar": "ملف المشتريات بعمود المورد"},
    "supplier_late_pct": {"cat": "operational", "ar": "تأخر تسليمات الموردين", "dir": "above", "t": (15, 30, 50), "unit": "%",
                          "src": "purchases", "link": "company-purchases-intelligence.html#suppliers", "node": "supplier_late",
                          "needs_ar": "تاريخ التسليم المتوقع وتاريخ الاستلام في المشتريات"},
    "on_time_gap_pp": {"cat": "operational", "ar": "فجوة التسليم في الموعد عن المستهدف", "dir": "above", "t": (3, 10, 20), "unit": "نقطة",
                       "src": "operations", "link": "company-operations-intelligence.html#fulfillment", "node": "on_time",
                       "needs_ar": "أوقات التسليم والموعد المستهدف للطلبات (أو SLA بالدقائق)"},
    "utilization_pct": {"cat": "operational", "ar": "استخدام الطاقة", "dir": "above", "t": (90, 100, 120), "unit": "%",
                        "src": "operations", "link": "company-operations-intelligence.html#capacity", "node": "utilization",
                        "needs_ar": "الطاقة اليومية للفروع في إعدادات العمليات"},
    "defect_rate_pct": {"cat": "operational", "ar": "نسبة الأخطاء وإعادة العمل", "dir": "above", "t": (3, 6, 10), "unit": "%",
                        "src": "operations", "link": "company-operations-intelligence.html#quality", "node": "defects",
                        "needs_ar": "عمود الأخطاء أو إعادة العمل في الطلبات التشغيلية"},
    "turnover_pct": {"cat": "operational", "ar": "دوران الموظفين (12 شهراً)", "dir": "above", "t": (20, 35, 50), "unit": "%",
                     "src": "hr", "link": "company-hr-intelligence.html#turnover", "node": "turnover",
                     "needs_ar": "ملف الموظفين بتاريخ التعيين وتاريخ انتهاء الخدمة"},
    # العملاء
    "lost_customer_revenue_pct": {"cat": "customer", "ar": "إيراد العملاء المفقودين", "dir": "above", "t": (5, 10, 20), "unit": "%",
                                  "src": "sales", "link": "company-sales-intelligence.html#customers", "node": "customer_loss",
                                  "needs_ar": "عمود «العميل» في ملف المبيعات لفترة 6 أشهر على الأقل"},
    "repeat_rate_drop_pp": {"cat": "customer", "ar": "تراجع نسبة العملاء المتكررين", "dir": "above", "t": (3, 8, 15), "unit": "نقطة",
                            "src": "sales", "link": "company-sales-intelligence.html#customers", "node": "repeat",
                            "needs_ar": "عمود «العميل» في ملف المبيعات لفترة 6 أشهر على الأقل"},
    "customer_concentration_pct": {"cat": "customer", "ar": "اعتماد الإيراد على أكبر عميل", "dir": "above", "t": (20, 35, 50), "unit": "%",
                                   "src": "sales", "link": "company-sales-intelligence.html#customers", "node": "customer_concentration",
                                   "needs_ar": "عمود «العميل» في ملف المبيعات"},
    "revenue_decline_pct": {"cat": "customer", "ar": "انخفاض الإيراد (آخر 90 يوماً)", "dir": "above", "t": (5, 10, 20), "unit": "%",
                            "src": "sales", "link": "company-sales-intelligence.html#trend", "node": "revenue",
                            "needs_ar": "مبيعات 180 يوماً على الأقل"},
    # الامتثال
    "invoice_error_count": {"cat": "compliance", "ar": "فواتير بأخطاء أو مكررة", "dir": "above", "t": (1, 10, 30), "unit": "فاتورة",
                            "src": "tax", "link": "company-tax-intelligence.html#einvoice", "node": "invoice_errors",
                            "needs_ar": "سجل الفواتير الإلكترونية"},
    "overdue_filings": {"cat": "compliance", "ar": "إقرارات متأخرة", "dir": "above", "t": (1, 2, 3), "unit": "إقرار",
                        "src": "tax", "link": "company-tax-intelligence.html#calendar", "node": "filings",
                        "needs_ar": "تكرار الإقرار وتواريخ التقديم في إعدادات الضرائب"},
}
DEFAULT_THRESHOLDS = {k: v["t"] for k, v in DRIVERS.items()}
MODULE_AR = {"cashflow": "التدفق النقدي", "finance": "الوحدة المالية", "leakage": "مركز استرداد الأموال", "purchases": "المشتريات",
             "inventory": "المخزون", "operations": "العمليات", "hr": "الموارد البشرية", "sales": "المبيعات", "tax": "الضرائب والزكاة"}

# ═══════════════════════════════════════════════════════════
# 3.3.2 — طبقة الإعدادات + ملفات مخاطر القطاعات (محرك واحد، قواعد مختلفة)
# ═══════════════════════════════════════════════════════════
BASE_WEIGHTS = {"liquidity": 1.0, "customer": 1.0, "profit": 1.0, "operational": 1.0, "compliance": 0.6}
SECTOR_RISK = {
    "fnb": {"ar": "المطاعم والأغذية", "weights": {"profit": 1.2, "operational": 1.2},
            "thresholds": {"stockout_items": (1, 2, 5), "leakage_pct": (2, 5, 10), "cost_increase_pct": (4, 8, 15)},
            "focus": ["leakage_pct", "cost_increase_pct", "stockout_items"], "note_ar": "الهدر وتكلفة المواد ونفاد الأصناف أهم مخاطر المطاعم"},
    "retail": {"ar": "التجزئة", "weights": {"operational": 1.1, "customer": 1.1},
               "thresholds": {"stockout_items": (2, 5, 12)}, "focus": ["stockout_items", "leakage_pct", "revenue_decline_pct"],
               "note_ar": "التوفر والخصومات والمرتجعات"},
    "ecommerce": {"ar": "التجارة الإلكترونية", "weights": {"customer": 1.3},
                  "thresholds": {"leakage_pct": (4, 10, 18), "repeat_rate_drop_pp": (2, 5, 10)},
                  "focus": ["repeat_rate_drop_pp", "lost_customer_revenue_pct", "leakage_pct"], "note_ar": "الاحتفاظ بالعملاء والمرتجعات"},
    "manufacturing": {"ar": "التصنيع", "weights": {"operational": 1.3},
                      "thresholds": {"defect_rate_pct": (2, 4, 8), "supplier_concentration_pct": (35, 55, 75)},
                      "focus": ["defect_rate_pct", "supplier_late_pct", "cost_increase_pct"], "note_ar": "الجودة واستمرارية التوريد"},
    "contracting": {"ar": "المقاولات", "weights": {"liquidity": 1.4},
                    "thresholds": {"dso_days": (60, 90, 120), "overdue_ar_pct": (25, 40, 60)},
                    "focus": ["runway_months", "dso_days", "overdue_ar_pct"], "note_ar": "التحصيل والسيولة أهم مخاطر المقاولات"},
    "distribution": {"ar": "التوزيع", "weights": {"liquidity": 1.2, "operational": 1.1},
                     "thresholds": {"dso_days": (50, 70, 100)}, "focus": ["dso_days", "supplier_concentration_pct", "stockout_items"],
                     "note_ar": "الائتمان للعملاء والاعتماد على الموردين"},
    "services": {"ar": "الخدمات", "weights": {"customer": 1.2, "operational": 1.1},
                 "thresholds": {"customer_concentration_pct": (15, 30, 45)}, "focus": ["customer_concentration_pct", "turnover_pct", "utilization_pct"],
                 "note_ar": "الاعتماد على عملاء كبار والاحتفاظ بالكفاءات"},
    "clinics": {"ar": "العيادات", "weights": {"liquidity": 1.1, "compliance": 0.9},
                "thresholds": {"dso_days": (60, 90, 120)}, "focus": ["dso_days", "utilization_pct", "invoice_error_count"],
                "note_ar": "مطالبات التأمين ومدة التحصيل"},
    "hospitals": {"ar": "المستشفيات", "weights": {"liquidity": 1.2, "operational": 1.2, "compliance": 1.0},
                  "thresholds": {"dso_days": (75, 105, 140), "utilization_pct": (85, 95, 110)},
                  "focus": ["dso_days", "utilization_pct", "turnover_pct"], "note_ar": "التحصيل من شركات التأمين والطاقة الاستيعابية"},
    "logistics": {"ar": "الخدمات اللوجستية", "weights": {"operational": 1.3},
                  "thresholds": {"on_time_gap_pp": (2, 6, 12)}, "focus": ["on_time_gap_pp", "utilization_pct", "dso_days"],
                  "note_ar": "الالتزام بالمواعيد والطاقة"},
    "other": {"ar": "عام", "weights": {}, "thresholds": {}, "focus": [], "note_ar": "القواعد الافتراضية"},
}

# ═══════════════════════════════════════════════════════════
# 3.3.8 — سلاسل الارتباط (علاقة متزامنة — ليست سببية مثبتة)
# ═══════════════════════════════════════════════════════════
CHAINS = [
    {"id": "supply_to_customer", "ar": "من المورد إلى العميل",
     "nodes": ["supplier_late_pct", "stockout_items", "on_time_gap_pp", "lost_customer_revenue_pct"]},
    {"id": "cost_to_profit", "ar": "من تكلفة الشراء إلى الربح", "nodes": ["cost_increase_pct", "gross_margin_drop_pp", "net_margin_pct"]},
    {"id": "leak_to_profit", "ar": "من التسرب إلى الربح", "nodes": ["leakage_pct", "gross_margin_drop_pp", "net_margin_pct"]},
    {"id": "dependency", "ar": "الاعتماد على مورد", "nodes": ["supplier_concentration_pct", "supplier_late_pct", "stockout_items"]},
    {"id": "collection_to_cash", "ar": "من التحصيل إلى السيولة", "nodes": ["overdue_ar_pct", "dso_days", "runway_months"]},
    {"id": "capacity_to_customer", "ar": "من الطاقة إلى العميل", "nodes": ["utilization_pct", "on_time_gap_pp", "lost_customer_revenue_pct"]},
    {"id": "people_to_quality", "ar": "من الموظفين إلى الجودة", "nodes": ["turnover_pct", "on_time_gap_pp", "defect_rate_pct"]},
    {"id": "customer_to_cash", "ar": "من العملاء إلى السيولة", "nodes": ["revenue_decline_pct", "net_margin_pct", "runway_months"]},
    {"id": "invoice_to_tax", "ar": "من أخطاء الفواتير إلى التعرض الضريبي", "nodes": ["invoice_error_count", "overdue_filings"]},
]
CHAIN_NOTE_AR = "السلسلة تُظهر مؤشرات مرتفعة متزامنة ومترابطة منطقياً — علاقة وليست سببية مثبتة."


def sector_profile(sector):
    return SECTOR_RISK.get(sector or "other") or SECTOR_RISK["other"]


def thresholds_for(overrides=None, sector=None):
    """الحدود الفعّالة: الافتراضي ← ملف القطاع ← إعدادات الشركة. ترجع أيضاً مصدر كل حد للشفافية."""
    out, src = {}, {}
    prof = sector_profile(sector)
    for k, t in DEFAULT_THRESHOLDS.items():
        out[k], src[k] = tuple(t), "default"
        if k in prof["thresholds"]:
            out[k], src[k] = tuple(prof["thresholds"][k]), "sector"
        ov = (overrides or {}).get(k)
        if ov and len(ov) == 3 and all(to_decimal(x) is not None for x in ov):
            vals = tuple(float(to_decimal(x)) for x in ov)
            asc = DRIVERS[k]["dir"] == "above"
            if (asc and vals[0] <= vals[1] <= vals[2]) or (not asc and vals[0] >= vals[1] >= vals[2]):
                out[k], src[k] = vals, "company"
    return out, src


def weights_for(sector=None, overrides=None):
    w = dict(BASE_WEIGHTS)
    w.update(sector_profile(sector)["weights"])
    for k, v in (overrides or {}).items():
        if k in w and to_decimal(v) is not None and float(to_decimal(v)) >= 0:
            w[k] = float(to_decimal(v))
    return w


def level_of(score):
    if score is None:
        return None
    for lv in ("critical", "high", "medium"):
        if score >= SEV_BANDS[lv]:
            return lv
    return "low"


def _rid(*p):
    return "risk-" + hashlib.sha1("|".join(str(x) for x in p).encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════
# 3.3.1 (تابع) — قراءة الإشارات من الوحدات (محوّل واحد لكل محرك خطر)
# ═══════════════════════════════════════════════════════════
def _g(d, *path):
    cur = d
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and -len(cur) <= p < len(cur):
            cur = cur[p]
        else:
            return None
        if cur is None:
            return None
    return cur


def _n(v):
    """يفك قيمة المال الموحّدة {value, value_decimal} إلى رقم."""
    if isinstance(v, dict):
        return v.get("value")
    return v


def _f(v, nd=2):
    d = to_decimal(v)
    return None if d is None else float(round(d, nd))


def _money(v):
    d = to_decimal(v)
    return None if d is None else float(round_money(d))


def _pct(a, b):
    a, b = to_decimal(a), to_decimal(b)
    if a is None or b is None or b == 0:
        return None
    return float(round_pct(safe_divide(a * 100, b)))


def _avail(m):
    return isinstance(m, dict) and bool(m.get("has_data"))


def _imp(kind, amount, basis_ar):
    m = _money(amount)
    return {"type": kind, "type_ar": IMPACT_TYPES[kind]["ar"], "amount": m, "basis_ar": basis_ar} if m is not None and m > 0 else None


def _na(reason_ar):
    return {"value": None, "reason_ar": reason_ar}


def _parse_day(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def customer_metrics(rows, as_of=None, window_days=90, min_named_share=50):
    """مؤشرات العملاء من صفوف المبيعات نفسها (العميل، التاريخ، صافي المبيعات).
    الفترة الحالية = آخر 90 يوماً · السابقة = الـ90 قبلها. بلا عمود عميل كافٍ → «تعذّر التحديد» (لا صفر)."""
    rows = rows or []
    dated = []
    for r in rows:
        d = _parse_day(r.get("date"))
        amt = to_decimal(r.get("net_sales"))
        if amt is None:
            g = to_decimal(r.get("gross_sales"))
            amt = None if g is None else g - (to_decimal(r.get("discounts")) or D0) - (to_decimal(r.get("returns")) or D0)
        if d is not None and amt is not None:
            dated.append((d, amt, (r.get("customer_name") or "").strip(), r.get("branch_name")))
    out = {"available": False, "records": len(dated)}
    if not dated:
        out["reason_ar"] = "لا توجد مبيعات مؤرخة"
        return out
    end = as_of or max(x[0] for x in dated)
    c_start, p_start = end - timedelta(days=window_days), end - timedelta(days=2 * window_days)
    cur = [x for x in dated if c_start < x[0] <= end]
    prev = [x for x in dated if p_start < x[0] <= c_start]
    out.update({"as_of": end.isoformat(), "window_days": window_days,
                "current_window": f"{(c_start + timedelta(days=1)).isoformat()} → {end.isoformat()}",
                "previous_window": f"{(p_start + timedelta(days=1)).isoformat()} → {c_start.isoformat()}"})
    rc, rp = sum((x[1] for x in cur), D0), sum((x[1] for x in prev), D0)
    out["revenue_current"], out["revenue_previous"] = _money(rc), _money(rp) if prev else None
    out["revenue_decline_pct"] = _pct(rp - rc, rp) if prev and rp > 0 else None
    named = sum(1 for x in dated if x[2])
    out["named_share_pct"] = _pct(named, len(dated))
    if not prev:
        out["reason_ar"] = f"تحتاج مبيعات {2 * window_days} يوماً على الأقل لمقارنة فترتين"
    if named * 100 < min_named_share * len(dated):
        out["customer_reason_ar"] = f"عمود «العميل» موجود في {out['named_share_pct'] or 0}% من الصفوف فقط (المطلوب {min_named_share}%)"
        out["available"] = out["revenue_decline_pct"] is not None
        return out

    def per_customer(xs):
        agg = {}
        for d, a, c, b in xs:
            if c:
                e = agg.setdefault(c, {"rev": D0, "n": 0, "branch": {}})
                e["rev"] += a
                e["n"] += 1
                if b:
                    e["branch"][b] = e["branch"].get(b, D0) + a
        return agg
    cc, pc = per_customer(cur), per_customer(prev)
    out["available"] = True
    out["customers_current"], out["customers_previous"] = len(cc), len(pc) if prev else None
    if cc:
        top = max(cc.items(), key=lambda kv: kv[1]["rev"])
        named_rev = sum((v["rev"] for v in cc.values()), D0)
        out["top_customer"] = {"name": top[0], "revenue": _money(top[1]["rev"]), "share_pct": _pct(top[1]["rev"], named_rev)}
        out["customer_concentration_pct"] = out["top_customer"]["share_pct"]
    if prev and pc:
        lost = {c: v for c, v in pc.items() if c not in cc}
        lost_rev = sum((v["rev"] for v in lost.values()), D0)
        prev_named = sum((v["rev"] for v in pc.values()), D0)
        out["lost_customers"] = len(lost)
        out["lost_revenue"] = _money(lost_rev)
        out["lost_customer_revenue_pct"] = _pct(lost_rev, prev_named)
        out["lost_top"] = [{"name": c, "previous_revenue": _money(v["rev"]), "orders": v["n"]}
                           for c, v in sorted(lost.items(), key=lambda kv: -kv[1]["rev"])[:10]]
        br = {}
        for c, v in pc.items():
            for b, a in v["branch"].items():
                e = br.setdefault(b, [D0, D0])
                e[1] += a
                if c in lost:
                    e[0] += a
        out["lost_by_branch"] = {b: _pct(l, t) for b, (l, t) in br.items() if t > 0}
        rep = lambda agg: _pct(sum(1 for v in agg.values() if v["n"] >= 2), len(agg)) if agg else None
        out["repeat_rate_current"], out["repeat_rate_previous"] = rep(cc), rep(pc)
        if out["repeat_rate_current"] is not None and out["repeat_rate_previous"] is not None:
            out["repeat_rate_drop_pp"] = round(out["repeat_rate_previous"] - out["repeat_rate_current"], 1)
    return out


def _mod_missing(mods, key):
    m = (mods or {}).get(key)
    if not _avail(m):
        return f"لا توجد بيانات في وحدة {MODULE_AR.get(key, key)}"
    return None


def extract_inputs(mods, customer=None):
    """يحوّل نتائج الوحدات إلى قيم محركات الخطر بعقد موحّد:
    {value, previous, evidence[], impacts[], by_branch{}, by_department{}, drill[], reason_ar}.
    لا يحسب من جديد ما حسبته الوحدة — يقرأه ويشير إلى مصدره."""
    mods = mods or {}
    cf, fn, lk, pu = mods.get("cashflow"), mods.get("finance"), mods.get("leakage"), mods.get("purchases")
    iv, op, hr, tx = mods.get("inventory"), mods.get("operations"), mods.get("hr"), mods.get("tax")
    cur = (customer or {})
    out = {}
    net_sales = _n(_g(fn, "summary", "revenue", "net_sales")) if _avail(fn) else None
    if net_sales is None and _avail(lk):
        net_sales = _n(_g(lk, "overview", "net_revenue"))

    # ── السيولة
    miss = _mod_missing(mods, "cashflow")
    if miss:
        for k in ("runway_months", "dso_days", "overdue_ar_pct"):
            out[k] = _na(miss)
    else:
        rw = next((x for x in (cf.get("liquidity") or []) if x.get("code") == "runway"), None)
        if rw and rw.get("value") is not None:
            net = _n(_g(cf, "flows", "net"))
            out["runway_months"] = {"value": _f(rw["value"], 1), "evidence": [f"السيولة تكفي {rw['value']} شهراً بمعدل الاستنزاف الحالي" if rw["value"] >= 0
                                                                              else f"الرصيد سالب — مدة السيولة {rw['value']} شهراً (عجز قائم)",
                                                                              rw.get("method_ar") or ""],
                                    "impacts": [i for i in [_imp("exposure", abs(net) if net is not None and net < 0 else None,
                                                                 "صافي التدفق النقدي السالب للشهر الحالي")] if i],
                                    "period": rw.get("period")}
        elif rw and str(rw.get("needs_ar") or "").startswith("لا استنزاف") and _n(_g(cf, "position", "balance")) is not None:
            out["runway_months"] = {"value": None, "safe_state_ar": "لا استنزاف — متوسط صافي التدفق موجب، فالسيولة لا تتناقص",
                                    "evidence": [f"الرصيد {_n(_g(cf, 'position', 'balance')):,.0f} ومتوسط صافي التدفق موجب"]}
        else:
            out["runway_months"] = _na((rw or {}).get("needs_ar") or "مدة السيولة غير متاحة — يلزم الرصيد ومعدل الاستنزاف")
        col = dict(cf.get("collections") or {})
        for _k in ("total_receivables", "overdue"):
            col[_k] = _n(col.get(_k))
        if col.get("dso") is not None:
            out["dso_days"] = {"value": _f(col["dso"], 0), "previous": _f(col.get("dso_previous"), 0),
                               "evidence": [f"DSO {col['dso']:.0f} يوماً" + (f" (كان {col['dso_previous']:.0f})" if col.get("dso_previous") is not None else ""),
                                            col.get("dso_method_ar") or ""],
                               "impacts": [i for i in [_imp("exposure", col.get("total_receivables"), "إجمالي الذمم القائمة")] if i]}
        else:
            out["dso_days"] = _na("مدة التحصيل غير متاحة — لا يوجد ملف ذمم مدينة")
        if col.get("total_receivables"):
            od = col.get("overdue") or 0
            out["overdue_ar_pct"] = {"value": _pct(od, col["total_receivables"]),
                                     "evidence": [f"متأخر {od:,.0f} من {col['total_receivables']:,.0f} ({col.get('open_invoices') or 0} فاتورة مفتوحة)"],
                                     "impacts": [i for i in [_imp("exposure", od, "ذمم تجاوزت تاريخ الاستحقاق — مخاطرة تحصيل وليست خسارة")] if i]}
        else:
            out["overdue_ar_pct"] = _na("لا توجد ذمم مدينة قائمة أو لا يوجد ملف ذمم")

    # ── الربح
    miss = _mod_missing(mods, "finance")
    if miss:
        out["gross_margin_drop_pp"] = _na(miss)
        out["net_margin_pct"] = _na(miss)
    else:
        gch = _g(fn, "variance", "gross_margin_change_pp")
        gm = _g(fn, "summary", "profitability", "gross_margin")
        if gch is not None:
            drop = round(-gch, 1)
            out["gross_margin_drop_pp"] = {"value": drop, "evidence": [f"الهامش الإجمالي {gm}% (تغيّر {gch:+} نقطة عن الفترة السابقة)",
                                                                       _g(fn, "variance", "main_cause_ar") or ""],
                                           "impacts": [i for i in [_imp("actual", (to_decimal(net_sales) * to_decimal(drop) / 100) if drop > 0 and net_sales else None,
                                                                        "نقاط الهامش المفقودة × صافي مبيعات الفترة")] if i]}
        else:
            out["gross_margin_drop_pp"] = _na("تغيّر الهامش الإجمالي غير متاح — يلزم فترتان مع تكلفة المنتجات")
        nm = _g(fn, "summary", "profitability", "net_margin")
        if nm is not None:
            npf = _n(_g(fn, "summary", "profitability", "net_profit"))
            out["net_margin_pct"] = {"value": _f(nm, 1), "evidence": [f"صافي الربح {npf:,.0f} · الهامش {nm}%" if npf is not None else f"الهامش {nm}%"],
                                     "impacts": [i for i in [_imp("actual", abs(npf) if npf is not None and npf < 0 else None, "صافي خسارة الفترة")] if i]}
        else:
            out["net_margin_pct"] = _na("صافي الهامش غير متاح — يلزم التكلفة والمصروفات")
    miss = _mod_missing(mods, "leakage")
    if miss:
        out["leakage_pct"] = _na(miss)
    elif _g(lk, "overview", "rate_pct") is not None:
        ov = {k: _n(v) for k, v in (lk.get("overview") or {}).items()}
        money = {k: _n(v) for k, v in (lk.get("money") or {}).items()}
        out["leakage_pct"] = {"value": _f(ov["rate_pct"], 1),
                              "evidence": [f"تسرب {ov.get('total') or 0:,.0f} = {ov['rate_pct']}% من صافي المبيعات"]
                              + [f"{c.get('label')}: {_n(c.get('leakage')):,.0f}" for c in (lk.get("root_causes") or [])[:3] if _n(c.get("leakage")) is not None],
                              "impacts": [i for i in [_imp("actual", ov.get("total"), "تسرب محقق في الفترة (مصروفات زائدة + خصومات ومرتجعات فوق المعتاد)"),
                                                      _imp("recovery", money.get("opportunity"), "الجزء القابل للاسترداد بقواعد مركز استرداد الأموال")] if i],
                              "by_branch": {b["key"]: b.get("rate_pct") for b in (lk.get("branches") or []) if b.get("rate_pct") is not None},
                              "root_causes": [{"type": c.get("type"), "label": c.get("label"), "amount": _n(c.get("leakage")), "note_ar": c.get("note_ar")}
                                              for c in (lk.get("root_causes") or [])[:5]]}
    else:
        out["leakage_pct"] = _na("نسبة التسرب غير متاحة — يلزم أعمدة الخصم/المرتجعات أو ملف المصروفات")

    # ── المشتريات (التكلفة + المورد)
    miss = _mod_missing(mods, "purchases")
    if miss:
        for k in ("cost_increase_pct", "supplier_concentration_pct", "supplier_late_pct"):
            out[k] = _na(miss)
    else:
        pi = pu.get("price_index") or {}
        if pi.get("change_pct") is not None:
            prods = sorted([p for p in (pu.get("products") or []) if p.get("variance_pct") is not None and p["variance_pct"] > 0],
                           key=lambda p: -p["variance_pct"])[:5]
            out["cost_increase_pct"] = {"value": _f(pi["change_pct"], 1),
                                        "evidence": [f"مؤشر أسعار الشراء {pi['change_pct']:+}% لنفس الأصناف"]
                                        + [f"{p['product_sku']}: {p['avg_unit_cost']} مقابل {p['baseline_unit_cost']} ({p['variance_pct']:+}%)" for p in prods[:3]],
                                        "impacts": [i for i in [_imp("actual", _n(pi.get("price_effect")) if (_n(pi.get("price_effect")) or 0) > 0 else None,
                                                                     "فرق السعر × الكمية المشتراة في الفترة")] if i],
                                        "drill": [{"level": "product", "key": p["product_sku"], "category": p.get("category"),
                                                   "variance_pct": p["variance_pct"], "current": p.get("avg_unit_cost"), "baseline": p.get("baseline_unit_cost"),
                                                   "link": "company-purchases-intelligence.html#cost"} for p in prods]}
        else:
            out["cost_increase_pct"] = _na("مؤشر أسعار الشراء غير متاح — يلزم سعر الوحدة لنفس الأصناف في فترتين")
        conc = pu.get("concentration") or {}
        if conc.get("top_share_pct") is not None:
            top = next((s for s in (pu.get("suppliers") or []) if s.get("supplier") == conc.get("top_supplier")), {})
            spend = _n(top.get("spend"))
            out["supplier_concentration_pct"] = {"value": _f(conc["top_share_pct"], 1),
                                                 "evidence": [f"{conc.get('top_supplier')} يمثل {conc['top_share_pct']}% من الإنفاق",
                                                              f"أكبر 3 موردين: {conc.get('top3_share_pct')}%" if conc.get("top3_share_pct") is not None else ""],
                                                 "impacts": [i for i in [_imp("exposure", spend, f"إنفاق الفترة لدى {conc.get('top_supplier')} — يتأثر إن تعطّل المورد")] if i],
                                                 "drill": [{"level": "supplier", "key": conc.get("top_supplier"), "link": "company-purchases-intelligence.html#suppliers"}]}
        else:
            out["supplier_concentration_pct"] = _na("لا يوجد عمود المورد في المشتريات")
        ev_lines, late_lines, worst = D0, D0, []
        for s in pu.get("suppliers") or []:
            d = s.get("delivery") or {}
            if d.get("late_pct") is not None and d.get("evaluated_lines"):
                n = Decimal(d["evaluated_lines"])
                ev_lines += n
                late_lines += n * to_decimal(d["late_pct"]) / 100
                worst.append((d["late_pct"], s.get("supplier"), d.get("evaluated_lines")))
        if ev_lines > 0:
            worst.sort(key=lambda x: -x[0])
            out["supplier_late_pct"] = {"value": _pct(late_lines, ev_lines),
                                        "evidence": [f"{_pct(late_lines, ev_lines)}% من {int(ev_lines)} تسليماً متأخرة"]
                                        + [f"{w[1]}: {w[0]}% متأخر ({w[2]} تسليم)" for w in worst[:3]],
                                        "drill": [{"level": "supplier", "key": w[1], "late_pct": w[0], "link": "company-purchases-intelligence.html#suppliers"} for w in worst[:5]]}
        else:
            out["supplier_late_pct"] = _na("لا توجد تواريخ تسليم متوقعة وفعلية في المشتريات")

    # ── المخزون
    miss = _mod_missing(mods, "inventory")
    if miss:
        out["stockout_items"] = _na(miss)
    else:
        sos = [x for x in (iv.get("stockouts") or []) if x.get("out_now")]
        so_kpi = _g(iv, "kpis", "stockout_rate") or {}
        n_out = so_kpi.get("stockout_items") if so_kpi.get("stockout_items") is not None else len(sos)
        brs = {}
        for x in sos:
            if x.get("branch"):
                brs[x["branch"]] = brs.get(x["branch"], 0) + 1
        out["stockout_items"] = {"value": n_out, "evidence": [f"{n_out} صنفاً نافداً الآن من {so_kpi.get('items') or '؟'} (نسبة النفاد {so_kpi.get('current')}%)"]
                                 + [f"{x.get('name') or x.get('product_sku')} — {x.get('branch') or ''}" for x in sos[:3]],
                                 "by_branch": brs,
                                 "drill": [{"level": "product", "key": x.get("product_sku"), "name": x.get("name"), "branch": x.get("branch"),
                                            "events": x.get("events"), "sales_impact": x.get("sales_impact"),
                                            "link": "company-inventory-intelligence.html#stockouts"} for x in sos[:10]]}

    # ── العمليات
    miss = _mod_missing(mods, "operations")
    if miss:
        for k in ("on_time_gap_pp", "utilization_pct", "defect_rate_pct"):
            out[k] = _na(miss)
    else:
        k_ = op.get("kpis") or {}
        brs = op.get("branches") or []
        ot = k_.get("on_time") or {}
        if ot.get("value") is not None and ot.get("target") is not None:
            out["on_time_gap_pp"] = {"value": round(float(ot["target"]) - float(ot["value"]), 1),
                                     "evidence": [f"التسليم في الموعد {ot['value']}% مقابل مستهدف {ot['target']}%"],
                                     "by_branch": {b["key"]: round(float(ot["target"]) - float(b["on_time"]), 1) for b in brs if b.get("on_time") is not None},
                                     "by_department": {d["key"]: round(float(ot["target"]) - float(d["on_time"]), 1)
                                                       for d in (op.get("departments") or []) if d.get("on_time") is not None}}
        else:
            out["on_time_gap_pp"] = _na(ot.get("reason_ar") or "التسليم في الموعد غير متاح")
        ut = k_.get("utilization") or {}
        out["utilization_pct"] = ({"value": _f(ut["value"], 1), "evidence": [f"استخدام الطاقة {ut['value']}%"],
                                   "by_branch": {b["key"]: b["utilization"] for b in brs if b.get("utilization") is not None}}
                                  if ut.get("value") is not None else _na(ut.get("reason_ar") or "الطاقة غير محددة"))
        q = k_.get("quality") or {}
        out["defect_rate_pct"] = ({"value": round(100 - float(q["value"]), 1), "evidence": [f"الجودة {q['value']}% ← أخطاء/إعادة عمل {round(100 - float(q['value']), 1)}%"],
                                   "by_branch": {b["key"]: round(100 - float(b["quality"]), 1) for b in brs if b.get("quality") is not None},
                                   "by_department": {d["key"]: round(100 - float(d["quality"]), 1) for d in (op.get("departments") or []) if d.get("quality") is not None}}
                                  if q.get("value") is not None else _na(q.get("reason_ar") or "لا توجد بيانات أخطاء"))

    # ── الموارد البشرية
    miss = _mod_missing(mods, "hr")
    if miss:
        out["turnover_pct"] = _na(miss)
    else:
        t12 = _g(hr, "kpis", "turnover", "current")
        tv = hr.get("turnover") or {}
        out["turnover_pct"] = ({"value": _f(t12, 1), "evidence": [f"الدوران {t12}% خلال 12 شهراً", tv.get("method_ar") or ""],
                                "by_branch": {x["key"]: x["rate"] for x in tv.get("by_branch") or [] if x.get("rate") is not None},
                                "by_department": {x["key"]: x["rate"] for x in tv.get("by_department") or [] if x.get("rate") is not None}}
                               if t12 is not None else _na("الدوران غير متاح — يلزم تاريخ انتهاء الخدمة للمغادرين"))

    # ── العملاء (من صفوف المبيعات نفسها)
    if not cur.get("records"):
        for k in ("lost_customer_revenue_pct", "repeat_rate_drop_pp", "customer_concentration_pct", "revenue_decline_pct"):
            out[k] = _na("لا توجد بيانات مبيعات مؤرخة")
    else:
        why = cur.get("customer_reason_ar") or cur.get("reason_ar") or "عمود «العميل» غير كافٍ"
        if cur.get("lost_customer_revenue_pct") is not None:
            out["lost_customer_revenue_pct"] = {"value": cur["lost_customer_revenue_pct"],
                                                "evidence": [f"{cur['lost_customers']} عميلاً اشتروا في {cur['previous_window']} ولم يشتروا في {cur['current_window']}",
                                                             f"إيرادهم السابق {cur['lost_revenue']:,.0f}"]
                                                + [f"{x['name']}: {x['previous_revenue']:,.0f}" for x in cur.get("lost_top", [])[:3]],
                                                "impacts": [i for i in [_imp("potential", cur.get("lost_revenue"), "إيراد 90 يوماً من عملاء توقفوا — قد لا يعود")] if i],
                                                "by_branch": cur.get("lost_by_branch") or {},
                                                "drill": [{"level": "customer", **x, "link": "company-sales-intelligence.html#customers"} for x in cur.get("lost_top", [])]}
        else:
            out["lost_customer_revenue_pct"] = _na(why)
        out["repeat_rate_drop_pp"] = ({"value": cur["repeat_rate_drop_pp"],
                                       "evidence": [f"العملاء المتكررون {cur['repeat_rate_current']}% مقابل {cur['repeat_rate_previous']}% في الفترة السابقة"]}
                                      if cur.get("repeat_rate_drop_pp") is not None else _na(why))
        tc = cur.get("top_customer")
        out["customer_concentration_pct"] = ({"value": tc["share_pct"], "evidence": [f"{tc['name']} = {tc['share_pct']}% من إيراد العملاء المسمّين آخر 90 يوماً"],
                                              "impacts": [i for i in [_imp("exposure", tc["revenue"], "إيراد أكبر عميل — معرّض إن توقف")] if i]}
                                             if tc and tc.get("share_pct") is not None else _na(cur.get("customer_reason_ar") or why))
        out["revenue_decline_pct"] = ({"value": cur["revenue_decline_pct"],
                                       "evidence": [f"الإيراد {cur['revenue_current']:,.0f} مقابل {cur['revenue_previous']:,.0f} (آخر 90 يوماً مقابل الـ90 قبلها)"],
                                       "impacts": [i for i in [_imp("actual", (to_decimal(cur['revenue_previous']) - to_decimal(cur['revenue_current'])) if cur["revenue_decline_pct"] > 0 else None,
                                                                    "فرق الإيراد بين الفترتين")] if i]}
                                      if cur.get("revenue_decline_pct") is not None else _na(cur.get("reason_ar") or "تحتاج مبيعات 180 يوماً"))

    # ── الامتثال
    miss = _mod_missing(mods, "tax")
    if miss:
        out["invoice_error_count"] = _na(miss)
        out["overdue_filings"] = _na(miss)
    else:
        h = _g(tx, "einvoice", "health")
        exp = next((e for e in (tx.get("exposures") or []) if e.get("key") == "invoice_errors"), None)
        if _g(tx, "einvoice", "available") and h:
            n = (h.get("error") or 0) + (h.get("duplicate") or 0)
            out["invoice_error_count"] = {"value": n, "evidence": [f"أخطاء {h.get('error') or 0} · مكررة {h.get('duplicate') or 0} · تحتاج مراجعة {h.get('needs_review') or 0}"],
                                          "impacts": [i for i in [_imp("exposure", _n((exp or {}).get("tax_amount")), "ضريبة على فواتير تحتاج تصحيحاً — أثر ضريبي محتمل وليس غرامة")] if i],
                                          "by_branch": {b["branch"]: b.get("invoice_errors") for b in tx.get("branches") or [] if b.get("invoice_errors")}}
        else:
            out["invoice_error_count"] = _na(_g(tx, "einvoice", "reason_ar") or "لا يوجد سجل فواتير إلكترونية")
        cal = _g(tx, "calendar")
        if cal and cal.get("available"):
            od = [c for c in cal.get("items") or [] if c.get("status") == "overdue"]
            out["overdue_filings"] = {"value": len(od), "evidence": [f"{len(od)} إقرار تجاوز موعده دون تسجيل التقديم"] + [str(c.get("period")) for c in od[:3]]}
        else:
            out["overdue_filings"] = _na("تكرار الإقرار غير محدد في إعدادات الضرائب")
    for k, v in out.items():
        v["evidence"] = [e for e in v.get("evidence") or [] if e]
    return out


# ═══════════════════════════════════════════════════════════
# 3.3.3 + 3.3.4 — محرك القواعد والدرجة (حتمي ومفسَّر)
# ═══════════════════════════════════════════════════════════
SCORE_RULE_AR = ("درجة المحرك 0–100: دون حد «متوسط» = 0–39 بالتناسب · بين متوسط ومرتفع = 40–64 · بين مرتفع وحرج = 65–84 · "
                 "عند الحرج أو أسوأ = 85–100. ثم تعديلات معلنة: الاتجاه (±5) · الاستمرار في تقييمات سابقة (+2 لكل مرة حتى +6) · "
                 "حجم الأثر المالي ≥ 5% من الإيراد (+5). الثقة لا تغيّر الدرجة — تُعرض بجانبها.")


def base_score(value, t, direction):
    """تحويل القيمة إلى درجة بالتناسب بين الحدود. للمؤشرات «الأقل أسوأ» نعكس الإشارة (نفس المعادلة)."""
    if value is None:
        return None
    x = float(value) if direction == "above" else -float(value)
    m, h, c = (t if direction == "above" else tuple(-v for v in t))
    span = (c - m) or 1.0
    if x < m:   # المنطقة الآمنة: صفر عند «مرساة» = حد المتوسط − ضعف المسافة بين المتوسط والمرتفع
        lo = m - 2 * (h - m)
        if direction == "above" and m > 0:
            lo = max(0.0, lo)
        return round(max(0.0, 39.0 * (x - lo) / ((m - lo) or 1)), 1)
    if x < h:
        return round(40 + 24.0 * (x - m) / ((h - m) or 1), 1)
    if x < c:
        return round(65 + 19.0 * (x - h) / ((c - h) or 1), 1)
    return round(min(100.0, 85 + 15.0 * (x - c) / span), 1)


def score_driver(key, inp, t, *, revenue=None, history_levels=None, meta=None):
    """درجة محرك واحد مع تفصيل كل مكوّن («كيف وصلنا لهذا الرقم؟»). meta: لمسببات 3.4 المساندة بنفس المعادلة."""
    meta = meta or DRIVERS[key]
    v = inp.get("value")
    if v is None and inp.get("safe_state_ar"):      # حالة آمنة مقاسة (مثل: لا استنزاف) — ليست بيانات ناقصة
        return {"score": 0.0, "level": "low", "parts": [{"code": "safe_state", "ar": inp["safe_state_ar"], "points": 0.0}], "status": "evaluated"}
    if v is None:
        return {"score": None, "level": None, "parts": [], "status": "unable"}
    b = base_score(v, t, meta["dir"])
    parts = [{"code": "threshold", "ar": f"القيمة {v} {meta['unit']} مقابل الحدود {t[0]} / {t[1]} / {t[2]}", "points": b}]
    s = b
    prev = inp.get("previous")
    if prev is not None and prev != v:
        worse = (v > prev) if meta["dir"] == "above" else (v < prev)
        d = 5 if worse else -5
        parts.append({"code": "trend", "ar": f"الاتجاه {'يسوء' if worse else 'يتحسن'} ({prev} ← {v})", "points": d})
        s += d
    streak = 0
    for lv in reversed(history_levels or []):
        if lv in ("medium", "high", "critical"):
            streak += 1
        else:
            break
    if streak and b >= 40:
        d = min(6, 2 * streak)
        parts.append({"code": "duration", "ar": f"مرتفع في {streak} تقييم سابق متتالٍ", "points": d})
        s += d
    rev = to_decimal(revenue)
    big = [i for i in inp.get("impacts") or [] if i["type"] in ("actual", "potential") and rev and rev > 0
           and to_decimal(i["amount"]) is not None and to_decimal(i["amount"]) * 100 >= rev * 5]
    if big and b >= 40:
        parts.append({"code": "impact", "ar": f"الأثر {big[0]['amount']:,.0f} ≥ 5% من الإيراد", "points": 5})
        s += 5
    s = round(max(0.0, min(100.0, s)), 1)
    return {"score": s, "level": level_of(s), "parts": parts, "status": "evaluated"}


def evaluate_drivers(inputs, thresholds, src, *, revenue=None, history=None, disabled=None):
    out = []
    hist_levels = {}
    for h in history or []:
        for k, lv in (h.get("driver_levels") or {}).items():
            hist_levels.setdefault(k, []).append(lv)
    for key, meta in DRIVERS.items():
        if key in (disabled or ()):
            continue
        inp = inputs.get(key) or _na("غير مقروء")
        sc = score_driver(key, inp, thresholds[key], revenue=revenue, history_levels=hist_levels.get(key))
        out.append({"key": key, "id": _rid("driver", key), "category": meta["cat"], "category_ar": CATS[meta["cat"]]["ar"],
                    "name_ar": meta["ar"], "unit": meta["unit"], "direction": meta["dir"], "value": inp.get("value"),
                    "previous": inp.get("previous"), "display_ar": inp.get("safe_state_ar"), "thresholds": list(thresholds[key]), "threshold_source": src[key],
                    "score": sc["score"], "level": sc["level"], "level_ar": LEVEL_AR[sc["level"]], "score_parts": sc["parts"],
                    "status": sc["status"], "evidence": inp.get("evidence") or [], "impacts": inp.get("impacts") or [],
                    "by_branch": inp.get("by_branch") or {}, "by_department": inp.get("by_department") or {},
                    "drill": inp.get("drill") or [], "root_causes": inp.get("root_causes") or [],
                    "source_module": meta["src"], "source_ar": MODULE_AR[meta["src"]], "link": meta["link"],
                    "reason_ar": inp.get("reason_ar"), "needs_ar": None if (inp.get("value") is not None or inp.get("safe_state_ar")) else meta["needs_ar"]})
    return out


def category_scores(drivers, weights):
    """درجة الفئة = 60% أسوأ محرك + 40% متوسط المحركات المقيّمة. بلا محرك مقيّم → «تعذّر التحديد» مع الأسباب."""
    cats = {}
    for c in CAT_ORDER:
        ds = [d for d in drivers if d["category"] == c]
        ev = [d for d in ds if d["score"] is not None]
        conf = _pct(len(ev), len(ds)) if ds else None
        if not ev:
            cats[c] = {"key": c, "ar": CATS[c]["ar"], "icon": CATS[c]["icon"], "score": None, "level": None, "level_ar": LEVEL_AR[None],
                       "status": "unable", "weight": weights.get(c, 1.0), "drivers": len(ds), "evaluated": 0, "confidence_pct": conf,
                       "unable_reasons": [{"driver": d["name_ar"], "reason_ar": d["reason_ar"], "needs_ar": d["needs_ar"]} for d in ds]}
            continue
        worst = max(ev, key=lambda d: d["score"])
        mean = sum(d["score"] for d in ev) / len(ev)
        sc = round(0.6 * worst["score"] + 0.4 * mean, 1)
        cats[c] = {"key": c, "ar": CATS[c]["ar"], "icon": CATS[c]["icon"], "score": sc, "level": level_of(sc), "level_ar": LEVEL_AR[level_of(sc)],
                   "status": "evaluated", "weight": weights.get(c, 1.0), "drivers": len(ds), "evaluated": len(ev), "confidence_pct": conf,
                   "top_driver": {"key": worst["key"], "name_ar": worst["name_ar"], "score": worst["score"]},
                   "formula_ar": f"60% × {worst['score']} (أسوأ محرك: {worst['name_ar']}) + 40% × {round(mean, 1)} (متوسط {len(ev)} محركات) = {sc}",
                   "unable_reasons": [{"driver": d["name_ar"], "reason_ar": d["reason_ar"], "needs_ar": d["needs_ar"]} for d in ds if d["score"] is None],
                   "impacts": _impact_totals([d for d in ev])}
    return cats


def overall_index(cats):
    """المؤشر العام = متوسط مرجّح لدرجات الفئات المحددة فقط (أوزان القطاع). الفئات المتعذرة لا تُحسب صفراً."""
    ev = [c for c in cats.values() if c["score"] is not None and c["weight"] > 0]
    if not ev:
        return {"score": None, "level": None, "level_ar": LEVEL_AR[None], "contributions": [],
                "explanation_ar": "تعذّر حساب المؤشر — لا توجد فئة واحدة ببيانات كافية"}
    tw = sum(c["weight"] for c in ev)
    contrib = [{"category": c["key"], "ar": c["ar"], "score": c["score"], "weight": c["weight"],
                "weight_share_pct": round(100 * c["weight"] / tw, 1), "points": round(c["score"] * c["weight"] / tw, 1)} for c in ev]
    score = round(sum(c["score"] * c["weight"] for c in ev) / tw, 1)
    contrib.sort(key=lambda x: -x["points"])
    expl = " + ".join(f"{x['ar']} {x['score']}×{x['weight_share_pct']}%" for x in contrib) + f" = {score}"
    skipped = [c["ar"] for c in cats.values() if c["score"] is None]
    return {"score": score, "level": level_of(score), "level_ar": LEVEL_AR[level_of(score)], "contributions": contrib,
            "explanation_ar": expl, "excluded_ar": skipped, "rule_ar": SEV_RULE_AR,
            "excluded_note_ar": ("فئات متعذّرة لم تدخل الحساب (لا تُعامل كصفر): " + "، ".join(skipped)) if skipped else None}


def confidence(drivers, cats):
    """الثقة = المحركات المقيّمة ÷ كل المحركات المفعّلة. كفاية البيانات: جيدة ≥ 80% · محدودة ≥ 50% · غير كافية."""
    n = len(drivers)
    ev = sum(1 for d in drivers if d["score"] is not None)
    pct = _pct(ev, n) if n else None
    lab = None if pct is None else ("good" if pct >= 80 else "limited" if pct >= 50 else "insufficient")
    return {"pct": pct, "evaluated": ev, "total": n, "sufficiency": lab,
            "sufficiency_ar": {"good": "بيانات كافية", "limited": "كفاية البيانات محدودة", "insufficient": "بيانات غير كافية", None: "—"}[lab],
            "rule_ar": "الثقة = محركات الخطر التي توفرت بياناتها ÷ كل المحركات المفعّلة",
            "missing": [{"driver": d["name_ar"], "category_ar": d["category_ar"], "needs_ar": d["needs_ar"], "source_ar": d["source_ar"]}
                        for d in drivers if d["score"] is None]}


# مبالغ متداخلة (جزء من بعض) لا تُجمع: داخل كل مجموعة يؤخذ الأكبر فقط.
OVERLAP = {"dso_days": "receivables", "overdue_ar_pct": "receivables",
           "net_margin_pct": "pnl", "gross_margin_drop_pp": "pnl", "leakage_pct": "pnl", "cost_increase_pct": "pnl",
           "revenue_decline_pct": "revenue", "lost_customer_revenue_pct": "revenue"}
OVERLAP_RULE_AR = ("لا تُجمع المبالغ المتداخلة: الذمم المتأخرة جزء من إجمالي الذمم، والتسرب وارتفاع التكلفة وتراجع الهامش أجزاء من صافي الربح — "
                   "داخل كل مجموعة متداخلة يُحتسب الأكبر فقط.")


def _impact_totals(drivers):
    """مجاميع الأثر لكل نوع على حدة — لا تُجمع الأنواع معاً، ولا تُجمع المبالغ المتداخلة داخل النوع."""
    tot = {}
    for d in drivers:
        for i in d.get("impacts") or []:
            if i.get("amount") is None:
                continue
            e = tot.setdefault(i["type"], {"groups": {}, "items": 0, "sources": []})
            g = OVERLAP.get(d["key"], d["key"])
            e["groups"][g] = max(e["groups"].get(g, D0), to_decimal(i["amount"]))
            e["items"] += 1
            e["sources"].append({"driver": d["key"], "name_ar": d["name_ar"], "amount": i["amount"], "group": g})
    out = []
    for k in IMPACT_TYPES:
        if k in tot:
            v = tot[k]
            out.append({"type": k, "type_ar": IMPACT_TYPES[k]["ar"], "amount": _money(sum(v["groups"].values(), D0)), "items": v["items"],
                        "sources": v["sources"], "rule_ar": IMPACT_TYPES[k]["rule_ar"],
                        "overlap_ar": OVERLAP_RULE_AR if len(v["groups"]) < v["items"] else None})
    return out


# ═══════════════════════════════════════════════════════════
# 3.3.8 + 3.3.9 — الارتباط والسبب الجذري
# ═══════════════════════════════════════════════════════════
ELEVATED = ("medium", "high", "critical")


def correlations(drivers):
    """سلسلة نشطة = محركان متتاليان على الأقل مرتفعان. الرسم: عقد = محركات، حواف = أزواج متتالية في السلاسل."""
    by = {d["key"]: d for d in drivers}
    chains, edges = [], {}
    for ch in CHAINS:
        nodes = [{"key": k, "name_ar": DRIVERS[k]["ar"], "level": (by.get(k) or {}).get("level"),
                  "score": (by.get(k) or {}).get("score"), "source_ar": MODULE_AR[DRIVERS[k]["src"]]} for k in ch["nodes"]]
        run, best = [], []
        for n in nodes:
            run = run + [n["key"]] if n["level"] in ELEVATED else []
            if len(run) > len(best):
                best = list(run)
        for a, b in zip(ch["nodes"], ch["nodes"][1:]):
            la, lb = (by.get(a) or {}).get("level"), (by.get(b) or {}).get("level")
            e = edges.setdefault((a, b), {"from": a, "to": b, "active": False, "chains": []})
            e["chains"].append(ch["id"])
            e["active"] = e["active"] or (la in ELEVATED and lb in ELEVATED)
        evaluated = sum(1 for n in nodes if n["level"] is not None)
        chains.append({"id": ch["id"], "ar": ch["ar"], "nodes": nodes, "active": len(best) >= 2, "active_path": best,
                       "evaluated_nodes": evaluated, "complete": evaluated == len(nodes),
                       "status_ar": ("نشطة: " + " ← ".join(DRIVERS[k]["ar"] for k in best)) if len(best) >= 2 else
                       ("غير نشطة" if evaluated >= 2 else "بيانات غير كافية للحكم")})
    chains.sort(key=lambda c: (not c["active"], -len(c["active_path"])))
    used = {k for e in edges for k in e}
    graph = {"nodes": [{"key": k, "name_ar": DRIVERS[k]["ar"], "category": DRIVERS[k]["cat"], "level": (by.get(k) or {}).get("level"),
                        "score": (by.get(k) or {}).get("score")} for k in DRIVERS if k in used],
             "edges": list(edges.values()), "note_ar": CHAIN_NOTE_AR}
    return chains, graph


def root_causes(drivers, chains, mods=None):
    """لكل محرك مرتفع: المحركات السابقة له في السلاسل النشطة + أسباب التسرب (3.1) + التعرض الضريبي (3.2). عوامل محتملة لا أسباب مؤكدة."""
    by = {d["key"]: d for d in drivers}
    out = []
    for d in sorted([d for d in drivers if d["level"] in ELEVATED], key=lambda d: -d["score"]):
        ups = []
        for ch in chains:
            if d["key"] in ch["active_path"]:
                i = ch["active_path"].index(d["key"])
                for k in ch["active_path"][:i]:
                    if k not in [u["key"] for u in ups]:
                        ups.append({"key": k, "name_ar": DRIVERS[k]["ar"], "level": by[k]["level"], "score": by[k]["score"],
                                    "chain_ar": ch["ar"], "source_ar": by[k]["source_ar"], "link": by[k]["link"]})
        links = []
        if d["key"] in ("gross_margin_drop_pp", "net_margin_pct", "leakage_pct"):
            lk = by.get("leakage_pct") or {}
            for c in lk.get("root_causes") or []:
                links.append({"module": "leakage", "module_ar": MODULE_AR["leakage"], "label": c.get("label"), "amount": c.get("amount"),
                              "link": "company-leakage-intelligence.html#causes", "note_ar": c.get("note_ar")})
        if d["category"] == "compliance" and _avail((mods or {}).get("tax")):
            for e in (mods["tax"].get("exposures") or [])[:4]:
                links.append({"module": "tax", "module_ar": MODULE_AR["tax"], "label": e.get("label"), "amount": _n(e.get("tax_amount")),
                              "link": "company-tax-intelligence.html#exposure", "note_ar": e.get("basis_ar") or "تعرض محتمل وليس غرامة"})
        out.append({"driver": d["key"], "name_ar": d["name_ar"], "level": d["level"], "score": d["score"], "upstream": ups,
                    "module_causes": links, "own_evidence": d["evidence"][:3],
                    "summary_ar": ("مرتبط بـ: " + "، ".join(u["name_ar"] for u in ups)) if ups else "لا يوجد محرك سابق مرتفع في السلاسل — راجع الأدلة المباشرة",
                    "note_ar": "عوامل مرتبطة بالبيانات — ليست سبباً مؤكداً"})
    return out


# ═══════════════════════════════════════════════════════════
# 3.3.10 + 3.3.11 — المخاطر حسب الفرع والقسم
# ═══════════════════════════════════════════════════════════
def _unit_map(drivers, thresholds, field):
    units = sorted({u for d in drivers for u in (d.get(field) or {})})
    rows = []
    for u in units:
        cells, details = {}, []
        for c in CAT_ORDER:
            sc = []
            for d in drivers:
                if d["category"] != c or u not in (d.get(field) or {}):
                    continue
                v = d[field][u]
                if v is None:
                    continue
                s = base_score(v, thresholds[d["key"]], d["direction"])
                sc.append(s)
                details.append({"driver": d["key"], "name_ar": d["name_ar"], "value": v, "unit": d["unit"], "score": s, "level": level_of(s)})
            cells[c] = None if not sc else {"score": round(0.6 * max(sc) + 0.4 * sum(sc) / len(sc), 1),
                                            "level": level_of(round(0.6 * max(sc) + 0.4 * sum(sc) / len(sc), 1)), "drivers": len(sc)}
        av = [x["score"] for x in cells.values() if x]
        total = round(sum(av) / len(av), 1) if av else None
        details.sort(key=lambda x: -x["score"])
        rows.append({"key": u, "cells": cells, "score": total, "level": level_of(total), "top": details[:3], "details": details})
    rows.sort(key=lambda r: -(r["score"] or -1))
    return rows


def branch_heatmap(drivers, thresholds, mods=None):
    rows = _unit_map(drivers, thresholds, "by_branch")
    sig = {}
    for name, m in (mods or {}).items():
        if _avail(m):
            for x in m.get("signals") or []:
                if x.get("type") == "risk" and x.get("dimension"):
                    sig.setdefault(x["dimension"], []).append({"module": name, "module_ar": MODULE_AR.get(name, name), "name_ar": x.get("name_ar"),
                                                               "severity": x.get("severity")})
    for r in rows:
        r["module_signals"] = sig.get(r["key"], [])[:8]
    return {"rows": rows, "categories": [{"key": c, "ar": CATS[c]["ar"]} for c in CAT_ORDER],
            "rule_ar": "خلية الفرع = نفس حدود المحرك على قيمة الفرع (60% أسوأ + 40% متوسط). «—» = لا توجد بيانات للفرع في هذه الفئة.",
            "company_level_ar": "مدة السيولة وصافي الهامش وأسعار الشراء والامتثال الكلي تُقاس على مستوى الشركة ولا تُقسَم على الفروع."}


def _scope_map(bm, scope):
    bm["categories"] = [c for c in bm["categories"] if c["key"] in scope]
    for r in bm["rows"]:
        r["cells"] = {k: v for k, v in r["cells"].items() if k in scope}
    return bm


def department_map(drivers, thresholds):
    return {"rows": _unit_map(drivers, thresholds, "by_department"),
            "rule_ar": "الأقسام تُقيَّم بمحركات متاحة لها فقط: الدوران، التسليم في الموعد، الأخطاء."}


# ═══════════════════════════════════════════════════════════
# 3.3.12 — الخط الزمني والاتجاهات (من لقطات التقييم المحفوظة)
# ═══════════════════════════════════════════════════════════
TREND_WINDOWS = [("30d", 30, "30 يوماً"), ("90d", 90, "90 يوماً"), ("6m", 182, "6 أشهر"), ("12m", 365, "12 شهراً")]


def trends(history, current, today):
    snaps = sorted([h for h in history or [] if _parse_day(h.get("date"))], key=lambda h: h["date"])
    out = []
    for code, days, ar in TREND_WINDOWS:
        target = today - timedelta(days=days)
        cand = [h for h in snaps if _parse_day(h["date"]) <= target]
        if not cand or current.get("score") is None:
            out.append({"window": code, "ar": ar, "available": False,
                        "reason_ar": f"لا يوجد تقييم محفوظ قبل {ar} — يُبنى السجل مع كل تقييم"})
            continue
        b = cand[-1]
        bi = b.get("index")
        cats = {c: (round(current["categories"][c] - b["categories"][c], 1)
                    if current["categories"].get(c) is not None and (b.get("categories") or {}).get(c) is not None else None) for c in CAT_ORDER}
        out.append({"window": code, "ar": ar, "available": bi is not None, "base_date": b["date"], "base_index": bi,
                    "change": round(current["score"] - bi, 1) if bi is not None else None, "categories": cats,
                    "direction_ar": None if bi is None else ("يرتفع (يسوء)" if current["score"] > bi + 2 else "ينخفض (يتحسن)" if current["score"] < bi - 2 else "مستقر")})
    series = [{"date": h["date"], "index": h.get("index"), "confidence": h.get("confidence"), **{c: (h.get("categories") or {}).get(c) for c in CAT_ORDER}}
              for h in snaps[-60:]]
    return {"windows": out, "series": series, "rule_ar": "التغير = المؤشر الآن − آخر تقييم محفوظ قبل بداية النافذة (± 2 = مستقر)"}


# ═══════════════════════════════════════════════════════════
# 3.3.13 → 3.3.17 — التنبيهات، السجل، خطط المعالجة، تتبع القرار، قبل/بعد
# ═══════════════════════════════════════════════════════════
STATUSES = ["detected", "reviewed", "decision", "approved", "action", "measurement", "reassessment", "monitoring", "closed"]
STATUS_AR = {"detected": "مكتشف", "reviewed": "تمت المراجعة", "decision": "بانتظار قرار", "approved": "قرار معتمد", "action": "قيد التنفيذ",
             "measurement": "قياس الأثر", "reassessment": "إعادة تقييم", "monitoring": "مراقبة", "closed": "مغلق"}
OWNER_DEFAULT = {"liquidity": "المالية", "profit": "المدير المالي", "operational": "العمليات", "customer": "المبيعات", "compliance": "المحاسبة/الضرائب"}
OWNER_BY_DRIVER = {"cost_increase_pct": "المشتريات", "supplier_concentration_pct": "المشتريات", "supplier_late_pct": "المشتريات",
                   "turnover_pct": "الموارد البشرية", "stockout_items": "المخزون/المشتريات"}
DUE_DAYS = {"critical": 3, "high": 7, "medium": 14, "low": 30}
ACTIONS = {
    "runway_months": "خفّض الاستنزاف الشهري وأجّل المصروفات غير الضرورية، وجهّز خيار تمويل قصير الأجل",
    "dso_days": "راجع شروط الائتمان وابدأ متابعة أسبوعية لأكبر الذمم",
    "overdue_ar_pct": "ابدأ تحصيل الفواتير المتأخرة بدءاً بأكبر العملاء، وأوقف الائتمان للمتأخرين جداً",
    "gross_margin_drop_pp": "حلّل الأصناف التي انخفض هامشها وراجع التسعير أو تكلفة الشراء",
    "net_margin_pct": "راجع أكبر بنود المصروفات وأصناف الهامش المنخفض",
    "leakage_pct": "افتح مركز استرداد الأموال وابدأ بأكبر بند قابل للاسترداد",
    "cost_increase_pct": "تفاوض مع الموردين على الأصناف الأعلى ارتفاعاً أو اطلب عروضاً بديلة",
    "stockout_items": "أعد طلب الأصناف النافدة وراجع نقاط إعادة الطلب",
    "supplier_concentration_pct": "قيّم موردين بديلين للأصناف الأساسية",
    "supplier_late_pct": "ناقش الالتزام بالمواعيد مع الموردين المتأخرين وزد مخزون الأمان مؤقتاً",
    "on_time_gap_pp": "حدّد مرحلة الاختناق في العمليات ووزّع الطاقة على الفروع المتأخرة",
    "utilization_pct": "وفّر طاقة إضافية أو أعد توزيع الطلبات قبل أن يتأثر التسليم",
    "defect_rate_pct": "راجع أسباب الأخطاء وإعادة العمل وأضف نقطة تحقق للجودة",
    "turnover_pct": "راجع أسباب المغادرة في الأقسام الأعلى دوراناً وخطط بدائل للأدوار الحرجة",
    "lost_customer_revenue_pct": "تواصل مع أكبر العملاء المتوقفين لفهم سبب التوقف",
    "repeat_rate_drop_pp": "راجع تجربة العميل وبرامج الاحتفاظ",
    "customer_concentration_pct": "نوّع قاعدة العملاء وراجع شروط العقد مع العميل الأكبر",
    "revenue_decline_pct": "حلّل الانخفاض حسب الفرع والقناة والمنتج في وحدة المبيعات",
    "invoice_error_count": "صحّح الفواتير الخاطئة والمكررة قبل موعد الإقرار",
    "overdue_filings": "سجّل الإقرارات المقدّمة أو قدّم المتأخر منها",
}


def _sev4_to3(lv):
    return {"critical": "high", "high": "high", "medium": "medium"}.get(lv, "low")


def first_seen(key, history, today):
    seen = None
    for h in sorted(history or [], key=lambda h: h.get("date") or "", reverse=True):
        if (h.get("driver_levels") or {}).get(key) in ELEVATED:
            seen = h["date"]
        else:
            break
    return seen or today.isoformat()


def register(drivers, stored, history, today):
    """السجل = مخاطر مكتشفة (محرك ≥ متوسط) + مخاطر محفوظة (يدوية أو سابقة). الحفظ لا يغيّر المصدر."""
    st = {x["risk_key"]: x for x in stored or []}
    by = {d["key"]: d for d in drivers}
    items = []
    keys = [d["key"] for d in drivers if d["level"] in ELEVATED] + [k for k in st if k not in {d["key"] for d in drivers if d["level"] in ELEVATED}]
    for k in keys:
        s, d = st.get(k) or {}, by.get(k)
        cat = (d or {}).get("category") or s.get("category") or "operational"
        lv = (d or {}).get("level") or s.get("level")
        cur_score = (d or {}).get("score")
        ba = None
        if s.get("baseline_score") is not None and cur_score is not None:
            ch = round(cur_score - float(s["baseline_score"]), 1)
            ba = {"baseline_score": s["baseline_score"], "baseline_value": s.get("baseline_value"), "baseline_date": s.get("baseline_date"),
                  "current_score": cur_score, "current_value": (d or {}).get("value"), "change": ch,
                  "result": "improved" if ch <= -5 else "worsened" if ch >= 5 else "unchanged",
                  "result_ar": "تحسّن" if ch <= -5 else "ساء" if ch >= 5 else "دون تغيير يُذكر",
                  "note_ar": "قياس قبل/بعد على نفس القاعدة — لا يُثبت أن الإجراء هو السبب"}
        status = s.get("status") or "detected"
        due = s.get("due_date") or (today + timedelta(days=DUE_DAYS.get(lv or "low", 30))).isoformat()
        items.append({"id": _rid("reg", k), "risk_key": k, "title": s.get("title") or (d or {}).get("name_ar") or k, "category": cat,
                      "category_ar": CATS.get(cat, {}).get("ar"), "level": lv, "level_ar": LEVEL_AR.get(lv), "score": cur_score,
                      "value": (d or {}).get("value"), "unit": (d or {}).get("unit"),
                      "impacts": (d or {}).get("impacts") or [], "owner": s.get("owner") or OWNER_BY_DRIVER.get(k) or OWNER_DEFAULT.get(cat),
                      "owner_is_default": not s.get("owner"), "status": status, "status_ar": STATUS_AR.get(status, status),
                      "due_date": due, "overdue": bool(due and due < today.isoformat() and status not in ("closed", "monitoring")),
                      "mitigation": s.get("mitigation") or "", "suggested_action_ar": ACTIONS.get(k), "decision_id": s.get("decision_id"),
                      "detected_on": s.get("created_on") or first_seen(k, history, today), "source_ar": (d or {}).get("source_ar"),
                      "link": (d or {}).get("link"), "manual": d is None and k not in DRIVERS and not k.startswith("x:"),
                      "supporting": k.startswith("x:"), "saved": bool(s),
                      "still_elevated": bool(d and d["level"] in ELEVATED), "before_after": ba, "history": s.get("history") or [],
                      "resolved_candidate": bool(s and d and d["level"] == "low")})
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}
    items.sort(key=lambda x: (x["status"] == "closed", order.get(x["level"], 4), -(x["score"] or 0)))
    return items


def alerts(drivers, reg, today):
    rmap = {r["risk_key"]: r for r in reg}
    out = []
    for d in sorted([d for d in drivers if d["level"] in ELEVATED], key=lambda d: -d["score"]):
        r = rmap.get(d["key"]) or {}
        if r.get("status") == "closed":
            continue
        out.append({"id": _rid("alert", d["key"], today.isoformat()[:7]), "risk_key": d["key"], "risk": d["name_ar"],
                    "category": d["category"], "category_ar": d["category_ar"], "severity": d["level"], "severity_ar": d["level_ar"],
                    "score": d["score"], "evidence": d["evidence"][:4], "impacts": d["impacts"], "detected": r.get("detected_on") or today.isoformat(),
                    "source_ar": d["source_ar"], "link": d["link"], "action_ar": ACTIONS.get(d["key"]), "owner": r.get("owner"),
                    "due_date": r.get("due_date"), "status": r.get("status") or "detected", "status_ar": STATUS_AR.get(r.get("status") or "detected"),
                    "decision_id": r.get("decision_id")})
    return out


def to_signals(drivers, period):
    """إشارات بالصيغة الموحّدة لمركز القيادة التنفيذي."""
    out = []
    for d in sorted([d for d in drivers if d["level"] in ELEVATED], key=lambda d: -d["score"]):
        imp = next((i for i in d["impacts"] if i["type"] in ("actual", "potential", "exposure")), None)
        out.append({"id": _rid("sig", d["key"], period), "type": "risk", "code": d["key"], "source_module": "risk", "name_ar": d["name_ar"],
                    "severity": _sev4_to3(d["level"]), "risk_level": d["level"], "dimension": None, "period": period, "evidence": d["evidence"][:3],
                    "suggested_action_ar": ACTIONS.get(d["key"]), "metric_id": d["key"], "score": d["score"], "category": d["category"],
                    "estimated_impact": {"value": imp["amount"], "type": imp["type"], "type_ar": imp["type_ar"]} if imp else None,
                    "method": f"rule-based (risk-v{RISK_VERSION})"})
    return out


def workflow_board(reg):
    """مسار القرار: مكتشف → مراجعة → قرار → اعتماد → تنفيذ → قياس → إعادة تقييم."""
    cols = []
    for s in STATUSES:
        xs = [r for r in reg if r["status"] == s]
        cols.append({"status": s, "ar": STATUS_AR[s], "count": len(xs),
                     "items": [{"risk_key": r["risk_key"], "title": r["title"], "level": r["level"], "owner": r["owner"], "due_date": r["due_date"],
                                "overdue": r["overdue"], "decision_id": r["decision_id"]} for r in xs]})
    return cols


# ═══════════════════════════════════════════════════════════
# 3.3.21 + 3.3.22 — أسئلة للذكاء الاصطناعي (يشرح فقط) + موجز الرئيس التنفيذي (حتمي)
# ═══════════════════════════════════════════════════════════
def ai_questions(index, top):
    qs = []
    if index.get("score") is not None:
        qs.append(f"كيف وصل مؤشر المخاطر إلى {index['score']}؟")
    for d in top[:3]:
        qs.append(f"لماذا «{d['name_ar']}» عند مستوى {d['level_ar']}؟ وما المرتبط به؟")
    qs += ["ما أكبر مخاطرة يجب أن أتصرف فيها هذا الأسبوع؟", "ما البيانات الناقصة التي ترفع ثقة التقييم؟",
           "ما الذي تغيّر منذ آخر تقييم؟"]
    return qs


def ceo_brief(index, conf, cats, drivers, chains, alerts_, tr, currency):
    top = sorted([d for d in drivers if d["level"] in ELEVATED], key=lambda d: -d["score"])[:3]
    lines = []
    if index["score"] is None:
        lines.append("تعذّر حساب مؤشر المخاطر — لا توجد بيانات كافية في أي فئة.")
    else:
        lines.append(f"مؤشر المخاطر {index['score']} ({index['level_ar']}) · الثقة {conf['pct']}% — {conf['sufficiency_ar']}.")
    for d in top:
        imp = next((i for i in d["impacts"]), None)
        lines.append(f"{d['name_ar']}: {d['value']} {d['unit']} ({d['level_ar']})" +
                     (f" — {imp['type_ar']} {imp['amount']:,.0f} {currency}" if imp else ""))
    act = [c for c in chains if c["active"]]
    if act:
        lines.append(f"سلسلة مرتبطة: {act[0]['status_ar'].replace('نشطة: ', '')} (علاقة لا سببية).")
    w = next((x for x in tr["windows"] if x["window"] == "30d" and x.get("available")), None)
    if w and w.get("change") is not None:
        lines.append(f"منذ 30 يوماً: {w['change']:+} نقطة ({w['direction_ar']}).")
    unable = [c["ar"] for c in cats.values() if c["score"] is None]
    if unable:
        lines.append("تعذّر تقييم: " + "، ".join(unable) + " — أكمل البيانات.")
    need = [a for a in alerts_ if not a.get("decision_id") and a["severity"] in ("critical", "high")]
    return {"headline": lines[0], "lines": lines, "decisions_needed": [{"risk": a["risk"], "action_ar": a["action_ar"], "owner": a["owner"],
                                                                        "due_date": a["due_date"]} for a in need[:5]],
            "method_ar": "موجز مولَّد بقواعد ثابتة من نفس الأرقام — بلا ذكاء اصطناعي"}


# ═══════════════════════════════════════════════════════════
# المُنسِّق — analyze_risk
# ═══════════════════════════════════════════════════════════
def analyze_risk(mods, *, customer_rows=None, settings=None, sector=None, history=None, stored=None,
                 today=None, currency="SAR", period=None, categories=None):
    """mods: نتائج الوحدات {sales, inventory, purchases, cashflow, hr, operations, finance, leakage, tax}.
    settings: {thresholds:{driver:[m,h,c]}, weights:{cat:w}, disabled:[driver]}.
    history: لقطات تقييم سابقة [{date, index, categories, driver_levels, confidence}].
    stored: عناصر السجل المحفوظة [{risk_key, status, owner, due_date, mitigation, decision_id, baseline_score, ...}]."""
    today = today or date.today()
    settings = settings or {}
    thr, src = thresholds_for(settings.get("thresholds"), sector)
    w = weights_for(sector, settings.get("weights"))
    disabled = set(settings.get("disabled") or [])
    scope = [c for c in CAT_ORDER if not categories or c in categories]       # نطاق الدور (RBAC): فئات خارج النطاق لا تُحسب ولا تُعرض
    disabled |= {k for k, m in DRIVERS.items() if m["cat"] not in scope}
    cust = customer_metrics(customer_rows or [])
    inputs = extract_inputs(mods or {}, cust)
    any_mod = any(_avail(m) for m in (mods or {}).values()) or bool(cust.get("records"))
    revenue = _n(_g((mods or {}).get("finance"), "summary", "revenue", "net_sales")) if _avail((mods or {}).get("finance")) else cust.get("revenue_current")
    drivers = evaluate_drivers(inputs, thr, src, revenue=revenue, history=history, disabled=disabled)
    cats = {k: v for k, v in category_scores(drivers, w).items() if k in scope}
    idx = overall_index(cats)
    conf = confidence(drivers, cats)
    chains, graph = correlations(drivers)
    rc = root_causes(drivers, chains, mods)
    stored = [x for x in stored or [] if (DRIVERS.get(x.get("risk_key"), {}).get("cat") or x.get("category") or "operational") in scope]
    reg = register(drivers, stored, history, today)
    al = alerts(drivers, reg, today)
    cur_snap = {"score": idx["score"], "categories": {c: (cats.get(c) or {}).get("score") for c in CAT_ORDER}}
    tr = trends(history, cur_snap, today)
    prof = sector_profile(sector)
    top = sorted([d for d in drivers if d["level"] in ELEVATED], key=lambda d: -d["score"])
    period = period or today.strftime("%Y-%m")
    return {
        "has_data": any_mod, "version": f"risk-v{RISK_VERSION}", "as_of": today.isoformat(), "period": period, "currency": currency,
        "index": idx, "confidence": conf, "categories": [cats[c] for c in CAT_ORDER if c in cats], "drivers": drivers,
        "scope": {"categories": scope, "full": len(scope) == len(CAT_ORDER),
                  "note_ar": None if len(scope) == len(CAT_ORDER) else "عرض جزئي حسب صلاحيتك — المؤشر محسوب من الفئات المتاحة لك فقط"},
        "top_risks": [{"key": d["key"], "name_ar": d["name_ar"], "category_ar": d["category_ar"], "score": d["score"], "level": d["level"],
                       "level_ar": d["level_ar"], "value": d["value"], "unit": d["unit"], "evidence": d["evidence"][:2],
                       "impacts": d["impacts"], "link": d["link"], "source_ar": d["source_ar"]} for d in top[:6]],
        "impacts": _impact_totals([d for d in drivers if d["level"] in ELEVATED]),
        "impact_note_ar": "كل نوع أثر يُعرض منفصلاً ولا تُجمع الأنواع معاً (الفعلي ≠ المحتمل ≠ التعرض ≠ الاسترداد).",
        "chains": chains, "graph": graph, "root_causes": rc,
        "branches": _scope_map(branch_heatmap(drivers, thr, mods if len(scope) == len(CAT_ORDER) else None), scope), "departments": department_map(drivers, thr),
        "trends": tr, "register": reg, "alerts": al, "workflow": workflow_board(reg),
        "customers": {k: v for k, v in cust.items() if k not in ("lost_by_branch",)},
        "rules": {"thresholds": {k: {"values": list(v), "source": src[k], "default": list(DEFAULT_THRESHOLDS[k]), "direction": DRIVERS[k]["dir"],
                                     "unit": DRIVERS[k]["unit"], "name_ar": DRIVERS[k]["ar"], "category": DRIVERS[k]["cat"],
                                     "enabled": k not in disabled} for k, v in thr.items()},
                  "weights": w, "score_rule_ar": SCORE_RULE_AR, "severity_rule_ar": SEV_RULE_AR,
                  "category_rule_ar": "درجة الفئة = 60% أسوأ محرك + 40% متوسط المحركات المقيّمة",
                  "index_rule_ar": "المؤشر = متوسط مرجّح لدرجات الفئات المقيّمة بأوزان القطاع (الفئات المتعذرة لا تُحسب صفراً)",
                  "impact_types": IMPACT_TYPES},
        "sector": {"key": sector or "other", "ar": prof["ar"], "note_ar": prof["note_ar"], "weights": w,
                   "focus": [{"key": k, "name_ar": DRIVERS[k]["ar"]} for k in prof["focus"]],
                   "overrides": {k: list(v) for k, v in prof["thresholds"].items()}},
        "signals": to_signals(drivers, period),
        "ai_questions": ai_questions(idx, top),
        "brief": ceo_brief(idx, conf, cats, drivers, chains, al, tr, currency),
        "snapshot": {"date": today.isoformat(), "index": idx["score"], "confidence": conf["pct"],
                     "categories": {c: (cats.get(c) or {}).get("score") for c in CAT_ORDER},
                     "driver_levels": {d["key"]: d["level"] for d in drivers if d["level"]},
                     "driver_scores": {d["key"]: d["score"] for d in drivers if d["score"] is not None}},
        "disclaimer_ar": "تقييم مبني على قواعد معلنة من بياناتك — يقيس المؤشرات ولا يُثبت الأسباب. البيانات الناقصة تظهر «تعذّر التحديد» ولا تُعامل كصفر.",
    }
