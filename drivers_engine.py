"""
NABBAH — Phase 3.4 · Risk Drivers Intelligence (محرك مسببات المخاطر)
3.3 يجيب: ما الخطر؟  ·  3.4 يجيب: ما الذي يحرّك هذا الخطر وبكم؟  ·  3.6 (لاحقاً): لماذا حدث من الأساس؟

يقرأ نتيجة محرك المخاطر 3.3 + نتائج الوحدات نفسها (لا ينشئ مبيعات أو مشتريات أو مالية جديدة) ويضيف:
- مساهمة كل مسبب في درجة الخطر (تفكيك دقيق للمعادلة المنشورة — المجموع = درجة الفئة بالضبط).
- مسببات مساندة (Supporting Drivers) تشرح المسببات الرئيسية ولا تدخل في المؤشر.
- فجوة الأداء (الحالي مقابل الهدف/الحد المقبول/خط الأساس)، الثقة وكفاية البيانات، الاتجاه والاستمرارية،
  الكيانات المتأثرة، الأسباب المرشحة (محتمل/مرجّح/عالي الثقة)، التوصية والأولوية، تاريخ المسبب.

مبادئ: حتمي بلا ذكاء اصطناعي · الناقص ≠ صفر · الارتباط ليس سببية · الأثر مصنّف ولا يُخلط · لا أرقام مخترعة.
"""
from decimal import Decimal
from datetime import date, datetime, timedelta
from statistics import median

from nabbah_finance import to_decimal, round_money
import risk_engine as RE

DRIVERS_VERSION = "1.0"
D0 = Decimal("0")

# ═══════════════════════════════════════════════════════════
# المسببات المساندة — تُقرأ من الوحدات، تُقاس بنفس معادلة الدرجة، ولا تدخل في مؤشر المخاطر
# ═══════════════════════════════════════════════════════════
SUPPORT = {
    "x_discount_leakage": {"cat": "profit", "ar": "خصومات فوق المعتاد", "dir": "above", "t": (0.5, 2, 4), "unit": "نقطة", "src": "leakage",
                           "link": "company-leakage-intelligence.html#discount", "parent": "leakage_pct",
                           "needs_ar": "عمود الخصم في ملف المبيعات"},
    "x_returns_leakage": {"cat": "profit", "ar": "مرتجعات فوق المعتاد", "dir": "above", "t": (0.5, 2, 4), "unit": "نقطة", "src": "leakage",
                          "link": "company-leakage-intelligence.html#returns", "parent": "leakage_pct",
                          "needs_ar": "عمود المرتجعات في ملف المبيعات"},
    "x_excess_opex": {"cat": "profit", "ar": "مصروفات تشغيلية زائدة", "dir": "above", "t": (1, 3, 6), "unit": "% من الإيراد", "src": "leakage",
                      "link": "company-leakage-intelligence.html#opex", "parent": "leakage_pct", "needs_ar": "ملف المصروفات لثلاثة أشهر على الأقل"},
    "x_low_margin_products": {"cat": "profit", "ar": "إيراد من أصناف منخفضة الهامش", "dir": "above", "t": (10, 25, 40), "unit": "% من الإيراد",
                              "src": "finance", "link": "company-financial-intelligence.html#revenue", "parent": "gross_margin_drop_pp",
                              "needs_ar": "تكلفة المنتجات لحساب هامش كل صنف"},
    "x_branch_underperformance": {"cat": "profit", "ar": "إيراد فروع بهامش أقل من الشركة", "dir": "above", "t": (10, 25, 40), "unit": "% من الإيراد",
                                  "src": "finance", "link": "company-financial-intelligence.html#branches", "parent": "net_margin_pct",
                                  "needs_ar": "مبيعات مسندة للفروع مع تكلفة المنتجات"},
    "x_cash_outflow_increase": {"cat": "liquidity", "ar": "ارتفاع التدفق الخارج", "dir": "above", "t": (5, 15, 30), "unit": "%", "src": "cashflow",
                                "link": "company-cashflow-intelligence.html#drivers", "parent": "runway_months", "needs_ar": "حركات بنكية لشهرين على الأقل"},
    "x_upcoming_shortfall": {"cat": "liquidity", "ar": "عجز متوقع خلال 14 يوماً", "dir": "above", "t": (5, 15, 30), "unit": "% من الداخل المتوقع",
                             "src": "cashflow", "link": "company-cashflow-intelligence.html#short", "parent": "runway_months",
                             "needs_ar": "حركات 90 يوماً + الذمم المستحقة"},
    "x_payroll_pressure": {"cat": "liquidity", "ar": "ضغط الرواتب على التحصيل", "dir": "above", "t": (35, 50, 70), "unit": "% من الداخل",
                           "src": "hr", "link": "company-hr-intelligence.html#compensation", "parent": "runway_months",
                           "needs_ar": "تكلفة الموظفين الشهرية + الحركات البنكية"},
    "x_supplier_payments": {"cat": "liquidity", "ar": "ارتفاع مدفوعات الموردين", "dir": "above", "t": (10, 25, 50), "unit": "%", "src": "cashflow",
                            "link": "company-cashflow-intelligence.html#drivers", "parent": "runway_months", "needs_ar": "تصنيف حركات الموردين في الكشف"},
    "x_tax_liability": {"cat": "liquidity", "ar": "التزام ضريبي مستحق من النقد", "dir": "above", "t": (10, 25, 50), "unit": "% من الرصيد",
                        "src": "tax", "link": "company-tax-intelligence.html#vat", "parent": "runway_months",
                        "needs_ar": "ضريبة المدخلات والمخرجات + رصيد نقدي موجب"},
    "x_low_cash": {"cat": "liquidity", "ar": "الرصيد مقابل الحد الأدنى للنقد", "dir": "below", "t": (100, 75, 50), "unit": "% من الحد الأدنى",
                   "src": "cashflow", "link": "company-cashflow-intelligence.html#liquidity", "parent": "runway_months",
                   "needs_ar": "الحد الأدنى للنقد في إعدادات التدفق النقدي"},
    "x_new_customer_decline": {"cat": "customer", "ar": "تراجع العملاء الجدد", "dir": "above", "t": (10, 25, 50), "unit": "%", "src": "sales",
                               "link": "company-sales-intelligence.html#customers", "parent": "revenue_decline_pct",
                               "needs_ar": "عمود «العميل» في المبيعات لـ 180 يوماً"},
    "x_price_increase": {"cat": "customer", "ar": "ارتفاع متوسط سعر البيع", "dir": "above", "t": (5, 10, 20), "unit": "%", "src": "sales",
                         "link": "company-sales-intelligence.html#products", "parent": "lost_customer_revenue_pct",
                         "needs_ar": "الكمية وصافي المبيعات لكل سطر"},
    "x_bottleneck": {"cat": "operational", "ar": "طلبات متأثرة باختناق", "dir": "above", "t": (5, 15, 30), "unit": "% من الطلبات", "src": "operations",
                     "link": "company-operations-intelligence.html#flow", "parent": "on_time_gap_pp", "needs_ar": "أوقات مراحل الطلب"},
    "x_order_errors": {"cat": "operational", "ar": "أخطاء الطلبات", "dir": "above", "t": (2, 5, 10), "unit": "%", "src": "operations",
                       "link": "company-operations-intelligence.html#quality", "parent": "defect_rate_pct", "needs_ar": "عمود «طلب صحيح» في الطلبات"},
}
# مسببات لا توجد لها بيانات في نبّاه حالياً — تُعرض بصدق «بيانات غير كافية» مع ما يلزم
DATA_NEEDED = {
    "n_complaints": {"cat": "customer", "ar": "الشكاوى", "needs_ar": "سجل الشكاوى (التاريخ، الفرع، النوع، الحل)"},
    "n_nps": {"cat": "customer", "ar": "صافي نقاط الترويج (NPS)", "needs_ar": "نتائج استبيان NPS"},
    "n_csat": {"cat": "customer", "ar": "رضا العملاء (CSAT)", "needs_ar": "نتائج استبيان الرضا بعد الخدمة"},
}
ALL_META = {**RE.DRIVERS, **SUPPORT}
CHILDREN = {}
for _k, _m in SUPPORT.items():
    CHILDREN.setdefault(_m["parent"], []).append(_k)

# مسببات كل قطاع (Sector Driver Configuration) — نفس المحرك، اختيار وترتيب مختلف
SECTOR_DRIVERS = {
    "fnb": [("تكلفة الطعام", "kpi:food_cost_pct"), ("الهدر", "need:waste"), ("التوصيل", "kpi:delivery_commission_pct"),
            ("العمالة", "kpi:labor_pct"), ("نفاد الأصناف", "stockout_items"), ("الخصومات", "x_discount_leakage")],
    "retail": [("المخزون", "stockout_items"), ("أسعار الموردين", "cost_increase_pct"), ("التخفيضات", "x_discount_leakage"),
               ("تحويل الزوار", "need:conversion"), ("المرتجعات", "x_returns_leakage")],
    "ecommerce": [("الاحتفاظ بالعملاء", "repeat_rate_drop_pp"), ("المرتجعات", "x_returns_leakage"), ("العملاء الجدد", "x_new_customer_decline"),
                  ("التوصيل", "on_time_gap_pp"), ("الخصومات", "x_discount_leakage")],
    "manufacturing": [("الجودة", "defect_rate_pct"), ("أسعار المواد", "cost_increase_pct"), ("تأخر الموردين", "supplier_late_pct"),
                      ("الطاقة", "utilization_pct"), ("العمالة", "kpi:labor_pct")],
    "contracting": [("تكلفة المشاريع", "need:project_cost"), ("أسعار المواد", "cost_increase_pct"), ("تأخر الدفعات", "dso_days"),
                    ("جدول المشاريع", "need:project_schedule"), ("استغلال الموارد", "utilization_pct")],
    "distribution": [("التحصيل", "dso_days"), ("الاعتماد على مورد", "supplier_concentration_pct"), ("النفاد", "stockout_items"),
                     ("أسعار الشراء", "cost_increase_pct")],
    "services": [("الاعتماد على عميل", "customer_concentration_pct"), ("دوران الموظفين", "turnover_pct"), ("الطاقة", "utilization_pct"),
                 ("العمالة", "kpi:labor_pct")],
    "clinics": [("تحصيل التأمين", "dso_days"), ("الطاقة", "utilization_pct"), ("أخطاء الفواتير", "invoice_error_count"), ("العمالة", "kpi:labor_pct")],
    "hospitals": [("تحصيل التأمين", "dso_days"), ("الطاقة الاستيعابية", "utilization_pct"), ("دوران الكوادر", "turnover_pct"), ("العمالة", "kpi:labor_pct")],
    "logistics": [("الالتزام بالمواعيد", "on_time_gap_pp"), ("الطاقة", "utilization_pct"), ("التحصيل", "dso_days"), ("الاختناقات", "x_bottleneck")],
    "other": [],
}
SECTOR_NEEDS = {"waste": "سجل الهدر اليومي (الصنف، الكمية، السبب)", "conversion": "عدد الزوار لكل متجر (عدّاد الدخول)",
                "project_cost": "ميزانية وتكلفة فعلية لكل مشروع", "project_schedule": "مراحل المشاريع بتواريخ مخططة وفعلية"}
KPI_WORSE_HIGH = {"food_cost_pct", "cogs_pct", "labor_pct", "delivery_commission_pct", "discount_pct", "returns_pct", "shrinkage_pct"}

# الإدارات الوظيفية: من يملك أي مسبب
DEPARTMENTS = {
    "المشتريات": ["cost_increase_pct", "supplier_concentration_pct", "supplier_late_pct"],
    "العمليات": ["on_time_gap_pp", "utilization_pct", "defect_rate_pct", "stockout_items", "x_bottleneck", "x_order_errors"],
    "المبيعات": ["revenue_decline_pct", "lost_customer_revenue_pct", "repeat_rate_drop_pp", "customer_concentration_pct",
                 "x_discount_leakage", "x_returns_leakage", "x_new_customer_decline", "x_price_increase"],
    "المالية": ["runway_months", "dso_days", "overdue_ar_pct", "net_margin_pct", "gross_margin_drop_pp", "leakage_pct", "x_excess_opex",
                "x_cash_outflow_increase", "x_upcoming_shortfall", "x_supplier_payments", "x_low_cash", "x_low_margin_products", "x_branch_underperformance"],
    "الموارد البشرية": ["turnover_pct", "x_payroll_pressure"],
    "الامتثال والضرائب": ["invoice_error_count", "overdue_filings", "x_tax_liability"],
}

# الأسباب المرشحة لكل مسبب (يُعرض منها المرتفع فقط، مع مستوى الدليل)
CANDIDATES = {
    "cost_increase_pct": ["supplier_concentration_pct", "supplier_late_pct"],
    "gross_margin_drop_pp": ["cost_increase_pct", "x_discount_leakage", "x_low_margin_products", "x_returns_leakage"],
    "net_margin_pct": ["gross_margin_drop_pp", "cost_increase_pct", "x_excess_opex", "leakage_pct", "revenue_decline_pct", "x_branch_underperformance"],
    "leakage_pct": ["x_discount_leakage", "x_returns_leakage", "x_excess_opex"],
    "runway_months": ["dso_days", "overdue_ar_pct", "net_margin_pct", "x_cash_outflow_increase", "x_payroll_pressure", "x_supplier_payments"],
    "dso_days": ["overdue_ar_pct", "customer_concentration_pct"],
    "overdue_ar_pct": ["customer_concentration_pct"],
    "lost_customer_revenue_pct": ["stockout_items", "on_time_gap_pp", "x_price_increase", "defect_rate_pct", "x_order_errors"],
    "revenue_decline_pct": ["lost_customer_revenue_pct", "x_new_customer_decline", "x_price_increase", "stockout_items"],
    "repeat_rate_drop_pp": ["on_time_gap_pp", "x_price_increase", "stockout_items", "defect_rate_pct"],
    "stockout_items": ["supplier_late_pct", "supplier_concentration_pct"],
    "on_time_gap_pp": ["utilization_pct", "turnover_pct", "x_bottleneck", "stockout_items"],
    "defect_rate_pct": ["turnover_pct", "utilization_pct", "x_order_errors"],
    "supplier_late_pct": ["supplier_concentration_pct"],
    "invoice_error_count": [],
    "overdue_filings": ["invoice_error_count"],
}

RECO = {
    "cost_increase_pct": "راجع أسعار {top} الأعلى تأثيراً، وقارن أسعار الأصناف المتشابهة لدى موردين آخرين قبل اعتماد الشراء القادم",
    "supplier_concentration_pct": "قيّم مورداً بديلاً للأصناف الأساسية لدى {top} وحدّد حصة قصوى للمورد الواحد",
    "supplier_late_pct": "اتفق مع {top} على مواعيد تسليم ملزمة وارفع مخزون الأمان للأصناف المتأثرة مؤقتاً",
    "stockout_items": "أعد طلب {top} وراجع نقاط إعادة الطلب ومدة التوريد",
    "leakage_pct": "ابدأ من مركز استرداد الأموال بأكبر بند: {top}",
    "x_discount_leakage": "راجع سياسة الخصم في {top} وحدّد سقفاً للخصم بدون موافقة",
    "x_returns_leakage": "راجع أسباب المرتجعات في {top} وقارن بأفضل فرع داخلياً",
    "x_excess_opex": "راجع بند {top} مقابل خط الأساس أو الموازنة قبل الصرف القادم",
    "x_low_margin_products": "راجع تسعير أو تكلفة {top} أو قلّل الترويج لها",
    "x_branch_underperformance": "حلّل هامش {top} حسب الصنف والخصم والتكلفة",
    "gross_margin_drop_pp": "حدّد الأصناف التي انخفض هامشها وراجع تسعيرها أو تكلفة شرائها",
    "net_margin_pct": "راجع أكبر بنود المصروفات والأصناف منخفضة الهامش",
    "revenue_decline_pct": "حلّل الانخفاض حسب الفرع والقناة والصنف وتواصل مع العملاء المتوقفين",
    "lost_customer_revenue_pct": "تواصل مع {top} لمعرفة سبب التوقف وقدّم عرض استعادة",
    "repeat_rate_drop_pp": "راجع تجربة العميل وبرنامج الولاء للعملاء المتكررين",
    "customer_concentration_pct": "نوّع قاعدة العملاء وراجع شروط العقد مع {top}",
    "x_new_customer_decline": "راجع قنوات الاستقطاب والحملات في آخر 90 يوماً",
    "x_price_increase": "راجع أثر رفع الأسعار على الكميات للأصناف الأعلى ارتفاعاً",
    "dso_days": "راجع شروط الائتمان وابدأ متابعة أسبوعية لأكبر الذمم",
    "overdue_ar_pct": "ابدأ تحصيل المتأخر بدءاً بأكبر العملاء وأوقف الائتمان للمتأخرين جداً",
    "runway_months": "خفّض الاستنزاف الشهري وأجّل غير الضروري وجهّز خيار تمويل قصير",
    "x_cash_outflow_increase": "راجع أكبر بنود الصرف التي ارتفعت هذا الشهر",
    "x_upcoming_shortfall": "رتّب أولويات الدفع للأسبوعين القادمين وسرّع تحصيل المستحق",
    "x_payroll_pressure": "راجع توقيت التحصيل مقابل موعد الرواتب",
    "x_supplier_payments": "تفاوض على آجال دفع أطول مع الموردين الرئيسيين",
    "x_tax_liability": "خصّص النقد للالتزام الضريبي قبل موعد الإقرار",
    "x_low_cash": "ارفع الرصيد فوق الحد الأدنى بتسريع التحصيل أو تأجيل الصرف",
    "on_time_gap_pp": "عالج مرحلة الاختناق في {top} ووزّع الطاقة",
    "utilization_pct": "وفّر طاقة إضافية في {top} أو أعد توزيع الطلبات",
    "defect_rate_pct": "أضف نقطة تحقق للجودة في {top} وراجع أسباب الأخطاء",
    "x_bottleneck": "عالج مرحلة {top}",
    "x_order_errors": "راجع إجراءات التحقق من الطلب قبل التسليم",
    "turnover_pct": "راجع أسباب المغادرة في {top} وخطط بدائل للأدوار الحرجة",
    "invoice_error_count": "صحّح الفواتير الخاطئة والمكررة قبل موعد الإقرار",
    "overdue_filings": "سجّل الإقرارات المقدّمة أو قدّم المتأخر",
}
PRIORITY_AR = {"immediate": "فوري", "high": "مرتفع", "medium": "متوسط", "monitor": "مراقبة"}
PRIORITY_RULE_AR = ("فوري: مستوى حرج، أو مرتفع مع أثر فعلي/محتمل ≥ 2% من الإيراد · مرتفع: مستوى مرتفع، أو متوسط ومستمر · "
                    "متوسط: مستوى متوسط · مراقبة: منخفض لكنه يسوء، أو بيانات غير كافية")
SUFF_AR = {"sufficient": "كافية", "partial": "جزئية", "limited": "محدودة", "insufficient": "غير كافية"}
CONF_RULE_AR = "الثقة = 40% عمق التاريخ (حتى 6 أشهر) + 40% حجم السجلات مقابل الحد الأدنى + 20% تغطية الحقل · كافية ≥ 85 · جزئية ≥ 65 · محدودة ≥ 40"


# ═══════════════════════════════════════════════════════════
# أدوات
# ═══════════════════════════════════════════════════════════
_n, _g, _f, _pct, _avail, _imp, _na = RE._n, RE._g, RE._f, RE._pct, RE._avail, RE._imp, RE._na


def _money(v):
    d = to_decimal(_n(v))
    return None if d is None else float(round_money(d))


def _day(v):
    return RE._parse_day(v)


# ═══════════════════════════════════════════════════════════
# ملف البيانات لكل مصدر (أساس الثقة وكفاية البيانات)
# ═══════════════════════════════════════════════════════════
MIN_RECORDS = {"sales": 200, "purchases": 50, "cashflow": 30, "finance": 3, "leakage": 3, "inventory": 20, "operations": 100,
               "hr": 10, "tax": 50}


def data_profiles(mods, cust):
    """أشهر التاريخ وعدد السجلات والكيانات لكل وحدة — من نتائج الوحدات نفسها."""
    P = {}
    def put(k, months, records, entities_ar=None):
        P[k] = {"months": months, "records": records, "entities_ar": entities_ar}
    s = mods.get("sales")
    if _avail(s):
        put("sales", len(_g(s, "trend", "series") or []), _g(s, "source", "records"),
            f"{len((s.get('products') or {}).get('items') or [])} صنفاً" if isinstance(s.get("products"), dict) else None)
    elif (cust or {}).get("records"):
        put("sales", 6 if cust.get("revenue_previous") is not None else 3, cust["records"], None)
    pu = mods.get("purchases")
    if _avail(pu):
        sups = pu.get("suppliers") or []
        put("purchases", len(pu.get("periods") or []), sum(int(x.get("lines") or 0) for x in sups) or None, f"{len(sups)} موردين")
    cf = mods.get("cashflow")
    if _avail(cf):
        put("cashflow", len(cf.get("periods") or []), None,
            f"{_g(cf, 'collections', 'open_invoices') or 0} فاتورة ذمم مفتوحة")
    for k in ("finance", "leakage"):
        m = mods.get(k)
        if _avail(m):
            put(k, len(m.get("periods") or []), None, None)
    iv = mods.get("inventory")
    if _avail(iv):
        put("inventory", len(iv.get("periods") or []), iv.get("positions_total"), f"{iv.get('positions_total') or 0} صنف/فرع")
    op = mods.get("operations")
    if _avail(op):
        put("operations", len(op.get("periods") or []), _g(op, "orders", "total"), f"{len(op.get('branches') or [])} فروع")
    hr = mods.get("hr")
    if _avail(hr):
        put("hr", 12, _g(hr, "data_quality", "employees"), None)
    tx = mods.get("tax")
    if _avail(tx):
        put("tax", len(tx.get("periods") or []), _g(tx, "einvoice", "total"), None)
    return P


def confidence_of(meta_src, has_value, profile, coverage_pct=None):
    """ثقة رقمية لكل مسبب + كفاية البيانات + الأسباب (مثل: 6 أشهر · 1,200 سطر · 4 موردين)."""
    if not has_value:
        return {"pct": 0.0, "sufficiency": "insufficient", "sufficiency_ar": SUFF_AR["insufficient"], "reasons_ar": ["القيمة غير متاحة"]}
    p = profile.get(meta_src) or {}
    months, rec = p.get("months") or 0, p.get("records") or 0
    hist = min(1.0, months / 6.0)
    vol = min(1.0, rec / float(MIN_RECORDS.get(meta_src, 30))) if rec else hist     # الوحدة لا تعرض عدد السجلات → يُقاس بعمق التاريخ
    cov = 1.0 if coverage_pct is None else max(0.0, min(1.0, float(coverage_pct) / 100))
    pct = round(100 * (0.4 * hist + 0.4 * vol + 0.2 * cov), 0)
    lab = "sufficient" if pct >= 85 else "partial" if pct >= 65 else "limited" if pct >= 40 else "insufficient"
    reasons = [f"{months} شهراً من البيانات" if months else "تاريخ قصير"]
    if rec:
        reasons.append(f"{int(rec):,} سجل")
    if p.get("entities_ar"):
        reasons.append(p["entities_ar"])
    if coverage_pct is not None:
        reasons.append(f"تغطية الحقل {coverage_pct}%")
    return {"pct": pct, "sufficiency": lab, "sufficiency_ar": SUFF_AR[lab], "reasons_ar": reasons}


# ═══════════════════════════════════════════════════════════
# مؤشرات العملاء الإضافية (من نفس صفوف المبيعات)
# ═══════════════════════════════════════════════════════════
def customer_extras(rows, window_days=90):
    rows = rows or []
    xs = []
    for r in rows:
        d = _day(r.get("date"))
        if d is None:
            continue
        xs.append((d, (r.get("customer_name") or "").strip(), to_decimal(r.get("net_sales")), to_decimal(r.get("quantity")), r.get("product_sku")))
    if not xs:
        return {}
    end = max(x[0] for x in xs)
    c0, p0 = end - timedelta(days=window_days), end - timedelta(days=2 * window_days)
    out = {}
    first = {}
    for d, c, *_ in sorted(xs):
        if c and c not in first:
            first[c] = d
    named = sum(1 for x in xs if x[1])
    if named * 2 >= len(xs) and min(x[0] for x in xs) <= p0:
        new_c = sum(1 for d in first.values() if c0 < d <= end)
        new_p = sum(1 for d in first.values() if p0 < d <= c0)
        out["new_current"], out["new_previous"] = new_c, new_p
        out["new_decline_pct"] = RE._pct(new_p - new_c, new_p) if new_p else None
        if not new_p:
            out["new_reason_ar"] = "لا يوجد عملاء جدد في الـ90 يوماً السابقة للمقارنة"
    def avg_price(a, b):
        num = sum((x[2] for x in xs if a < x[0] <= b and x[2] is not None and x[3]), D0)
        den = sum((x[3] for x in xs if a < x[0] <= b and x[2] is not None and x[3]), D0)
        return (num / den) if den else None
    pc, pp = avg_price(c0, end), avg_price(p0, c0)
    if pc is not None and pp:
        out["price_current"], out["price_previous"] = float(round(pc, 2)), float(round(pp, 2))
        out["price_change_pct"] = RE._pct(pc - pp, pp)
        by = {}
        for d, c, ns, q, sku in xs:
            if sku and ns is not None and q:
                k = "c" if c0 < d <= end else "p" if p0 < d <= c0 else None
                if k:
                    e = by.setdefault(sku, {"c": [D0, D0], "p": [D0, D0]})
                    e[k][0] += ns; e[k][1] += q
        ch = []
        for sku, e in by.items():
            if e["c"][1] and e["p"][1]:
                a, b = e["c"][0] / e["c"][1], e["p"][0] / e["p"][1]
                if b:
                    ch.append({"product": sku, "current": float(round(a, 2)), "previous": float(round(b, 2)), "change_pct": RE._pct(a - b, b)})
        out["price_by_product"] = sorted(ch, key=lambda x: -(x["change_pct"] or 0))[:10]
    return out


# ═══════════════════════════════════════════════════════════
# استخراج المسببات المساندة من الوحدات (قراءة فقط)
# ═══════════════════════════════════════════════════════════
def extract_support(mods, extras, settings=None):
    lk, fn, cf, hr, tx, op = (mods.get(k) for k in ("leakage", "finance", "cashflow", "hr", "tax", "operations"))
    out = {}
    miss = lambda k: f"لا توجد بيانات في وحدة {RE.MODULE_AR.get(k, k)}"
    # ── خصومات / مرتجعات فوق المعتاد
    for key, part in (("x_discount_leakage", "discount"), ("x_returns_leakage", "returns")):
        if not _avail(lk):
            out[key] = _na(miss("leakage")); continue
        b = lk.get(part) or {}
        if b.get("available") and b.get("rate") is not None and b.get("baseline_rate") is not None:
            gap = round(float(b["rate"]) - float(b["baseline_rate"]), 2)
            brs = [x for x in b.get("by_branch") or [] if x.get("rate") is not None]
            opp = next((x for x in (lk.get("money") or {}).get("opportunity_lines") or [] if x.get("key") == part), None)
            out[key] = {"value": gap, "evidence": [f"المعدل {b['rate']}% مقابل خط أساس {b['baseline_rate']}% ({'هدف مُعدّ' if b.get('baseline_source') == 'configured' else 'تاريخ الشركة'})",
                                                   f"الفعلي {_money(b.get('actual')):,.0f} مقابل المتوقع {_money(b.get('expected')):,.0f}" if b.get("expected") is not None else ""],
                        "impacts": [i for i in [_imp("actual", b.get("leakage"), "الفعلي − (المعدل المرجعي × إجمالي المبيعات)"),
                                                _imp("recovery", (opp or {}).get("amount"), (opp or {}).get("basis_ar") or "")] if i],
                        "by_branch": {x["key"]: round(float(x["rate"]) - float(x.get("baseline_rate") or b["baseline_rate"]), 2) for x in brs},
                        "entities": {"branches": [x["key"] for x in sorted(brs, key=lambda x: -(x.get("leakage") or 0)) if (x.get("leakage") or 0) > 0][:5],
                                     "products": [x["key"] for x in sorted(b.get("by_product") or [], key=lambda x: -(x.get("leakage") or 0)) if (x.get("leakage") or 0) > 0][:5],
                                     "categories": [x["key"] for x in sorted(b.get("by_category") or [], key=lambda x: -(x.get("leakage") or 0)) if (x.get("leakage") or 0) > 0][:5]},
                        "series": [(t["period"], t.get(part)) for t in lk.get("trends") or [] if t.get(part) is not None], "series_unit": "SAR",
                        "baseline": float(b["baseline_rate"]), "current_raw": float(b["rate"]), "raw_unit": "%"}
        else:
            out[key] = _na(b.get("reason_ar") or SUPPORT[key]["needs_ar"])
    # ── مصروفات زائدة
    if not _avail(lk):
        out["x_excess_opex"] = _na(miss("leakage"))
    else:
        ox, rev = lk.get("opex") or {}, _n(_g(lk, "overview", "net_revenue"))
        if ox.get("available") and ox.get("leakage") is not None and rev:
            lines = sorted([l for l in ox.get("lines") or [] if (l.get("leakage") or 0) > 0], key=lambda l: -l["leakage"])
            opp = next((x for x in (lk.get("money") or {}).get("opportunity_lines") or [] if x.get("key") == "opex"), None)
            out["x_excess_opex"] = {"value": RE._pct(ox["leakage"], rev), "evidence": [f"مصروفات فوق خط الأساس {ox['leakage']:,.0f} من إيراد {rev:,.0f}"]
                                    + [f"{l['label']}: {l['actual']:,.0f} مقابل {l['baseline']:,.0f} ({l['change_pct']:+}%)" for l in lines[:3] if l.get("change_pct") is not None],
                                    "impacts": [i for i in [_imp("actual", ox["leakage"], "المصروف الفعلي − خط الأساس/الموازنة"),
                                                            _imp("recovery", (opp or {}).get("amount"), (opp or {}).get("basis_ar") or "")] if i],
                                    "entities": {"categories": [l["label"] for l in lines[:5]]},
                                    "series": [(t["period"], t.get("opex")) for t in lk.get("trends") or [] if t.get("opex") is not None], "series_unit": "SAR"}
        else:
            out["x_excess_opex"] = _na(ox.get("reason_ar") or SUPPORT["x_excess_opex"]["needs_ar"])
    # ── أصناف وفروع منخفضة الهامش
    if not _avail(fn):
        out["x_low_margin_products"] = _na(miss("finance")); out["x_branch_underperformance"] = _na(miss("finance"))
    else:
        gm = _g(fn, "summary", "profitability", "gross_margin")
        prods = [p for p in _g(fn, "revenue", "by_product") or [] if p.get("gross_margin") is not None]
        if gm is not None and prods:
            low = [p for p in prods if p["gross_margin"] <= gm - 5]
            tot = sum((to_decimal(p["revenue"]) for p in prods), D0)
            lr = sum((to_decimal(p["revenue"]) for p in low), D0)
            out["x_low_margin_products"] = {"value": RE._pct(lr, tot), "evidence": [f"{len(low)} أصناف بهامش أقل من هامش الشركة ({gm}%) بخمس نقاط أو أكثر"]
                                            + [f"{p['key']}: هامش {p['gross_margin']}% · إيراد {p['revenue']:,.0f}" for p in sorted(low, key=lambda p: p['gross_margin'])[:3]],
                                            "entities": {"products": [p["key"] for p in sorted(low, key=lambda p: p["gross_margin"])[:8]]}, "baseline": gm}
        else:
            out["x_low_margin_products"] = _na("هامش الأصناف غير متاح — يلزم تكلفة المنتجات")
        brs = [b for b in fn.get("branches") or [] if b.get("gross_margin") is not None and b.get("revenue")]
        if gm is not None and len(brs) >= 2:
            low = [b for b in brs if b["gross_margin"] <= gm - 5]
            tot = sum((to_decimal(b["revenue"]) for b in brs), D0)
            out["x_branch_underperformance"] = {"value": RE._pct(sum((to_decimal(b["revenue"]) for b in low), D0), tot),
                                                "evidence": [f"{len(low)} من {len(brs)} فروع بهامش أقل من الشركة ({gm}%) بخمس نقاط أو أكثر"]
                                                + [f"{b['key']}: هامش {b['gross_margin']}%" for b in low[:3]],
                                                "by_branch": {b["key"]: round(gm - b["gross_margin"], 1) for b in brs},
                                                "entities": {"branches": [b["key"] for b in low]}, "baseline": gm}
        else:
            out["x_branch_underperformance"] = _na("يلزم فرعان على الأقل بهامش محسوب")
    # ── السيولة
    if not _avail(cf):
        for k in ("x_cash_outflow_increase", "x_upcoming_shortfall", "x_supplier_payments", "x_low_cash"):
            out[k] = _na(miss("cashflow"))
    else:
        oc = _g(cf, "flows", "outflow_change_pct")
        mv = cf.get("movement") or []
        out["x_cash_outflow_increase"] = ({"value": _f(oc, 1), "evidence": [f"الخارج {_money(_g(cf, 'flows', 'outflow')):,.0f} مقابل {_money(_g(cf, 'flows', 'previous', 'outflow')):,.0f} في الشهر السابق"]
                                                                          + [f"{d['label']}: {d['change_pct']:+}%" for d in (cf.get('drivers') or []) if d.get('direction') == 'out' and d.get('change_pct')][:3],
                                           "series": [(m["period"], _n(m.get("outflow"))) for m in mv], "series_unit": "SAR",
                                           "impacts": [i for i in [_imp("actual", (_n(_g(cf, "flows", "outflow")) or 0) - (_n(_g(cf, "flows", "previous", "outflow")) or 0) if oc and oc > 0 else None, "زيادة الخارج عن الشهر السابق")] if i]}
                                          if oc is not None else _na("يلزم شهران من الحركات"))
        st = cf.get("short_term") or {}
        ein, net = _n(st.get("expected_inflow")), _n(st.get("net"))
        out["x_upcoming_shortfall"] = ({"value": RE._pct(-net, ein) if net < 0 else 0.0,
                                        "evidence": [f"خلال {st.get('days')} يوماً: داخل متوقع {ein:,.0f} · خارج متوقع {_n(st.get('expected_outflow')):,.0f} · صافي {net:,.0f}", st.get("basis_ar") or ""],
                                        "impacts": [i for i in [_imp("exposure", -net if net < 0 else None, "عجز صافي متوقع — تقدير وليس خسارة")] if i]}
                                       if ein and net is not None else _na("التوقع القصير غير متاح"))
        sp = next((d for d in cf.get("drivers") or [] if d.get("key") == "suppliers"), None)
        out["x_supplier_payments"] = ({"value": _f(sp["change_pct"], 1), "evidence": [f"مدفوعات الموردين {_money(sp.get('amount')):,.0f} مقابل {_money(sp.get('previous')):,.0f}"]
                                                                                  + [f"{i.get('counterparty') or i.get('description')}: {_money(i.get('amount')):,.0f}" for i in (sp.get("top_items") or [])[:3]]}
                                      if sp and sp.get("change_pct") is not None else _na("لا يوجد تصنيف «موردين» في الحركات أو شهر سابق"))
        bal = _n(_g(cf, "position", "balance"))
        mn = to_decimal(((settings or {}).get("cash") or {}).get("min_cash") or (cf.get("settings") or {}).get("min_cash"))
        out["x_low_cash"] = ({"value": RE._pct(bal, mn), "evidence": [f"الرصيد {bal:,.0f} مقابل الحد الأدنى {float(mn):,.0f}"],
                              "impacts": [i for i in [_imp("exposure", float(mn) - bal if bal < float(mn) else None, "الفرق عن الحد الأدنى للنقد")] if i]}
                             if bal is not None and mn else _na("حدد الحد الأدنى للنقد في إعدادات التدفق النقدي" if bal is not None else "الرصيد غير متاح"))
    # ── الرواتب مقابل الداخل
    if not (_avail(hr) and _avail(cf)):
        out["x_payroll_pressure"] = _na(miss("hr" if not _avail(hr) else "cashflow"))
    else:
        pay, inn = _n(_g(hr, "compensation", "monthly_total")), _n(_g(cf, "flows", "inflow"))
        out["x_payroll_pressure"] = ({"value": RE._pct(pay, inn), "evidence": [f"الرواتب الشهرية {pay:,.0f} مقابل الداخل النقدي {inn:,.0f}"]}
                                     if pay and inn else _na("تكلفة الموظفين أو الداخل النقدي غير متاح"))
    # ── الضريبة المستحقة مقابل النقد
    if not _avail(tx):
        out["x_tax_liability"] = _na(miss("tax"))
    else:
        npos = _n(_g(tx, "vat", "net_position"))
        bal = _n(_g(cf, "position", "balance")) if _avail(cf) else None
        if npos is None:
            out["x_tax_liability"] = _na(_g(tx, "vat", "input_reason_ar") or "صافي المركز الضريبي غير متاح — يلزم ضريبة المدخلات")
        elif not bal or bal <= 0:
            out["x_tax_liability"] = _na("الرصيد النقدي غير متاح أو سالب — لا تُحسب النسبة")
        else:
            out["x_tax_liability"] = {"value": RE._pct(max(npos, 0), bal), "evidence": [f"صافي ضريبة مستحقة {npos:,.0f} مقابل رصيد {bal:,.0f}"],
                                      "impacts": [i for i in [_imp("exposure", npos if npos > 0 else None, "التزام ضريبي مستحق الدفع — ليس غرامة")] if i]}
    # ── العملاء الجدد والسعر
    ex = extras or {}
    out["x_new_customer_decline"] = ({"value": ex["new_decline_pct"], "evidence": [f"عملاء جدد {ex['new_current']} آخر 90 يوماً مقابل {ex['new_previous']} قبلها"]}
                                     if ex.get("new_decline_pct") is not None else _na(ex.get("new_reason_ar") or SUPPORT["x_new_customer_decline"]["needs_ar"]))
    out["x_price_increase"] = ({"value": ex["price_change_pct"], "evidence": [f"متوسط سعر الوحدة {ex['price_current']} مقابل {ex['price_previous']} (آخر 90 يوماً مقابل السابقة)"]
                                + [f"{p['product']}: {p['previous']} ← {p['current']} ({p['change_pct']:+}%)" for p in ex.get("price_by_product", [])[:3] if p.get("change_pct")],
                                "entities": {"products": [p["product"] for p in ex.get("price_by_product", []) if (p.get("change_pct") or 0) >= 5][:8]}}
                               if ex.get("price_change_pct") is not None else _na(SUPPORT["x_price_increase"]["needs_ar"]))
    # ── العمليات
    if not _avail(op):
        out["x_bottleneck"] = _na(miss("operations")); out["x_order_errors"] = _na(miss("operations"))
    else:
        bns, tot = op.get("bottlenecks") or [], _g(op, "orders", "total")
        out["x_bottleneck"] = ({"value": RE._pct(sum(b.get("affected") or 0 for b in bns), tot),
                                "evidence": [f"«{b['stage']}»{' في ' + b['branch'] if b.get('branch') else ''}: {b['avg_min']} دقيقة مقابل {b['normal_min']} (+{b['delay_min']})" for b in bns[:3]] or ["لا اختناقات مكتشفة"],
                                "by_branch": {b["branch"]: RE._pct(b.get("affected") or 0, tot) for b in bns if b.get("branch")},
                                "entities": {"branches": [b["branch"] for b in bns if b.get("branch")], "stages": [b["stage"] for b in bns]}}
                               if tot else _na("لا توجد طلبات"))
        acc = _g(op, "kpis", "accuracy") or {}
        out["x_order_errors"] = ({"value": round(100 - float(acc["value"]), 1), "evidence": [f"دقة الطلبات {acc['value']}% (مستهدف {acc.get('target')}%)"],
                                  "coverage": acc.get("coverage_pct")}
                                 if acc.get("value") is not None else _na(acc.get("reason_ar") or SUPPORT["x_order_errors"]["needs_ar"]))
    for v in out.values():
        v["evidence"] = [e for e in v.get("evidence") or [] if e]
    return out


def primary_series(mods):
    """سلاسل شهرية لقيم المسببات الرئيسية من الوحدات نفسها (نفس وحدة المسبب — قابلة للمقارنة بالحدود)."""
    lk, fn, op = mods.get("leakage"), mods.get("finance"), mods.get("operations")
    S = {}
    if _avail(lk):
        S["leakage_pct"] = [(t["period"], t.get("rate_pct")) for t in lk.get("trends") or [] if t.get("rate_pct") is not None]
    if _avail(fn):
        tr = [t for t in fn.get("trends") or [] if t.get("gross_margin") is not None]
        S["net_margin_pct"] = [(t["period"], t.get("net_margin")) for t in fn.get("trends") or [] if t.get("net_margin") is not None]
        S["gross_margin_drop_pp"] = [(b["period"], round(a["gross_margin"] - b["gross_margin"], 1)) for a, b in zip(tr, tr[1:])]
    if _avail(op):
        tg = _g(op, "kpis", "on_time", "target")
        if tg is not None:
            S["on_time_gap_pp"] = [(t["period"], round(float(tg) - t["on_time"], 1)) for t in op.get("trend") or [] if t.get("on_time") is not None]
        S["defect_rate_pct"] = [(t["period"], round(100 - t["quality"], 1)) for t in op.get("trend") or [] if t.get("quality") is not None]
    return S


# ═══════════════════════════════════════════════════════════
# الاتجاه والاستمرارية
# ═══════════════════════════════════════════════════════════
TREND_AR = {"accelerating": "يتسارع (يسوء بوتيرة أعلى)", "worsening": "يسوء", "improving": "يتحسن", "stable": "مستقر",
            "insufficient": "بيانات غير كافية للحكم"}


def classify_trend(values, direction, tol):
    """على آخر 4 نقاط: «يتسارع» فقط إذا ساءت كل خطوة ومتوسط الزيادات اللاحقة أكبر من الأولى — لا حكم بأقل من 3 نقاط."""
    v = [x for x in values if x is not None][-4:]
    if len(v) < 3:
        return "insufficient"
    bad = v if direction == "above" else [-x for x in v]
    inc = [b - a for a, b in zip(bad, bad[1:])]
    if all(d > tol for d in inc) and len(inc) >= 2 and sum(inc[1:]) / len(inc[1:]) > inc[0]:
        return "accelerating"
    if bad[-1] - bad[0] > tol and sum(1 for d in inc if d > 0) >= len(inc) - 1:
        return "worsening"
    if bad[0] - bad[-1] > tol and sum(1 for d in inc if d < 0) >= len(inc) - 1:
        return "improving"
    return "stable"


def _monthly_last(points):
    by = {}
    for d, v in sorted(points):
        by[str(d)[:7]] = v
    return sorted(by.items())


def persistence(levels_by_month, current_level):
    """مستمر = مرتفع 3 أشهر متتالية فأكثر · متكرر = شهران · جديد = الشهر الحالي فقط."""
    if current_level not in RE.ELEVATED:
        return {"code": "none", "ar": "غير مرتفع حالياً", "months": 0}
    if not levels_by_month:
        return {"code": "new", "ar": "جديد (لا تاريخ سابق محفوظ)", "months": 1}
    run = 0
    for _, lv in reversed(levels_by_month):
        if lv in RE.ELEVATED:
            run += 1
        else:
            break
    run = max(run, 1)
    code = "persistent" if run >= 3 else "recurring" if run == 2 else "new"
    return {"code": code, "ar": {"persistent": f"مستمر ({run} أشهر متتالية)", "recurring": "متكرر (شهران متتاليان)", "new": "جديد/مؤقت (الشهر الحالي)"}[code],
            "months": run}


# ═══════════════════════════════════════════════════════════
# المساهمة — تفكيك دقيق لمعادلة الفئة في 3.3 (60% أسوأ + 40% متوسط)
# ═══════════════════════════════════════════════════════════
ADDITIVE = {"leakage_pct": ["x_discount_leakage", "x_returns_leakage", "x_excess_opex"]}
CONTRIB_RULE_AR = ("مساهمة المسبب = (40% × درجته ÷ عدد المسببات المقيّمة) + (60% × درجته إن كان الأسوأ). مجموع المساهمات = درجة الفئة بالضبط. "
                   "مكوّنات التسرب تُقسَم بنسبة مبالغها الفعلية. المسببات المتعذّرة لا تساهم ولا تُعامل كصفر.")


def contributions(risk):
    cats = {c["key"]: c for c in risk.get("categories") or []}
    idx_pts = {x["category"]: x["points"] for x in (risk.get("index") or {}).get("contributions") or []}
    out = {}
    for ck, c in cats.items():
        ds = [d for d in risk["drivers"] if d["category"] == ck and d["score"] is not None]
        if not ds or not c.get("score"):
            out[ck] = {"score": c.get("score"), "items": [], "unable": [d["name_ar"] for d in risk["drivers"] if d["category"] == ck and d["score"] is None]}
            continue
        worst = max(ds, key=lambda d: d["score"])
        items = []
        for d in ds:
            pts = 0.4 * d["score"] / len(ds) + (0.6 * d["score"] if d is worst else 0.0)
            items.append({"key": d["key"], "name_ar": d["name_ar"], "score": d["score"], "level": d["level"], "points": round(pts, 2),
                          "pct": round(100 * pts / c["score"], 1), "is_worst": d is worst,
                          "index_points": round(idx_pts.get(ck, 0) * pts / c["score"], 2) if idx_pts.get(ck) else 0.0})
        items.sort(key=lambda x: -x["points"])
        out[ck] = {"score": c["score"], "level": c.get("level"), "items": items,
                   "unable": [d["name_ar"] for d in risk["drivers"] if d["category"] == ck and d["score"] is None]}
    return out


def _components(key, support):
    parts = [(k, next((i["amount"] for i in support.get(k, {}).get("impacts") or [] if i["type"] == "actual"), None)) for k in ADDITIVE.get(key, [])]
    parts = [(k, a) for k, a in parts if a]
    tot = sum(a for _, a in parts)
    return [{"key": k, "name_ar": SUPPORT[k]["ar"], "amount": a, "share_pct": round(100 * a / tot, 1)} for k, a in sorted(parts, key=lambda x: -x[1])] if tot else []


def _entities(d):
    ent = {k: list(v) for k, v in (d.get("entities") or {}).items() if v}
    for x in d.get("drill") or []:
        kind = {"supplier": "suppliers", "product": "products", "customer": "customers", "branch": "branches"}.get(x.get("level"))
        name = x.get("key") or x.get("name")
        if kind and name and name not in ent.setdefault(kind, []):
            ent[kind].append(name)
        if x.get("branch") and x["branch"] not in ent.setdefault("branches", []):
            ent["branches"].append(x["branch"])
    bad = sorted([(k, v) for k, v in (d.get("by_branch") or {}).items() if v is not None], key=lambda kv: -kv[1] if d.get("direction") == "above" else kv[1])
    for k, v in bad[:5]:
        thr = d.get("thresholds") or [None]
        if thr[0] is not None and RE.base_score(v, tuple(d["thresholds"]), d.get("direction", "above")) >= 40 and k not in ent.setdefault("branches", []):
            ent["branches"].append(k)
    return {k: v[:10] for k, v in ent.items() if v}


def _flat(ent):
    return {x for v in (ent or {}).values() for x in v}


ENT_AR = {"suppliers": "الموردون", "products": "الأصناف", "branches": "الفروع", "categories": "الفئات/البنود", "customers": "العملاء", "stages": "المراحل"}


def _priority(d, revenue):
    lv = d.get("level")
    big = False
    rev = to_decimal(revenue)
    if rev and rev > 0:
        amt = sum((to_decimal(i["amount"]) for i in d.get("impacts") or [] if i["type"] in ("actual", "potential")), D0)
        big = amt * 100 >= rev * 2
    pers = (d.get("persistence") or {}).get("code")
    tr = (d.get("trend") or {}).get("direction")
    if lv == "critical" or (lv == "high" and big):
        p = "immediate"
    elif lv == "high" or (lv == "medium" and pers == "persistent"):
        p = "high"
    elif lv == "medium":
        p = "medium"
    elif d.get("score") is None or tr in ("worsening", "accelerating"):
        p = "monitor"
    else:
        return None
    return {"code": p, "ar": PRIORITY_AR[p], "big_impact": big}


def _recommendation(d, target_info):
    if d.get("score") is None:
        return {"text_ar": "أكمل البيانات أولاً: " + (d.get("needs_ar") or d.get("reason_ar") or ""), "expected_impact": {"ar": "يرفع الثقة ويسمح بالتقييم", "amount": None, "type": "data"},
                "entities_used": [], "source_link": "company-data-center.html"}
    ent = d.get("entities") or {}
    pick = {"cost_increase_pct": "suppliers", "supplier_concentration_pct": "suppliers", "supplier_late_pct": "suppliers", "stockout_items": "products",
            "lost_customer_revenue_pct": "customers", "customer_concentration_pct": "customers", "x_low_margin_products": "products",
            "x_excess_opex": "categories", "leakage_pct": None}.get(d["key"], "branches")
    names = (ent.get(pick) or ent.get("products") or ent.get("branches") or []) if pick else []
    if d["key"] == "cost_increase_pct" and not ent.get("suppliers"):
        names = ent.get("products") or []
    if d["key"] == "leakage_pct":
        comp = d.get("components") or []
        names = [c["name_ar"] for c in comp[:1]]
    top = "، ".join(names[:3]) if names else {"suppliers": "الموردين", "products": "الأصناف", "customers": "العملاء", "categories": "البنود",
                                             "branches": "الفروع المتأثرة"}.get(pick or "", "البند الأكبر")
    text = (RECO.get(d["key"]) or "راجع الأدلة واتخذ إجراءً على المصدر").format(top=top)
    rec = next((i for i in d.get("impacts") or [] if i["type"] == "recovery"), None)
    if rec:
        exp = {"ar": f"فرصة استرداد حتى {rec['amount']:,.0f} — {rec['basis_ar']}", "amount": rec["amount"], "type": "recovery"}
    elif target_info and target_info.get("gap") is not None and target_info.get("beyond"):
        exp = {"ar": f"إغلاق فجوة {abs(target_info['gap'])} {d['unit']} نحو {target_info['target_ar']} {target_info['target']}", "amount": None, "type": "gap"}
    else:
        exp = {"ar": "غير قابل للتقدير بالمبلغ من البيانات الحالية", "amount": None, "type": None}
    return {"text_ar": text, "expected_impact": exp, "entities_used": names[:3], "source_link": d.get("link")}


def _gap(d, targets, series):
    v = d.get("value")
    if v is None:
        return None
    tg, src = None, None
    if targets and targets.get(d["key"]) not in (None, ""):
        tg, src = float(targets[d["key"]]), "company"
    elif d.get("thresholds"):
        tg, src = float(d["thresholds"][0]), "limit"
    base = None
    vals = [x for _, x in series or [] if x is not None]
    if len(vals) >= 3:
        base = round(float(median(vals[:-1])), 2)
    elif d.get("baseline") is not None:
        base = d["baseline"]
    gap = round(float(v) - tg, 2) if tg is not None else None
    beyond = None if gap is None else (gap > 0 if d.get("direction", "above") == "above" else gap < 0)
    return {"current": v, "target": tg, "target_source": src, "target_ar": {"company": "هدف الشركة", "limit": "الحد المقبول (حد «متوسط»)"}.get(src),
            "gap": gap, "beyond": beyond, "baseline": base, "baseline_ar": "وسيط الأشهر السابقة" if len(vals) >= 3 else ("خط أساس الوحدة" if base is not None else None),
            "vs_baseline": round(float(v) - base, 2) if base is not None else None, "unit": d.get("unit")}


CAND_AR = {"high": "سبب مرجّح بثقة عالية", "likely": "سبب مرجّح", "possible": "سبب محتمل"}
CAND_RULE_AR = ("ثقة عالية: المسبب المرشّح مرتفع/حرج ويشترك مع المسبب في نفس الكيانات (مورد/صنف/فرع) · مرجّح: مرتفع/حرج أو يشترك في الكيانات · "
                "محتمل: مرتفع بمستوى متوسط فقط. كلها علاقات من البيانات — التأكيد في مرحلة الأسباب الجذرية 3.6.")
LV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
EVENT_AR = {"detected": "اكتُشف", "increased": "ارتفع", "escalated": "تصاعد إلى حرج", "decreased": "انخفض", "resolved": "عاد منخفضاً",
            "action": "اتُّخذ قرار/إجراء", "status": "تغيّرت الحالة", "improved": "تحسّن بعد الإجراء", "still_active": "ما زال نشطاً بعد الإجراء"}


def _down(lv):
    return {"critical": "high", "high": "medium", "medium": "low"}.get(lv, "low")


def support_thresholds(settings):
    out = {}
    for k, m in SUPPORT.items():
        t = tuple(m["t"])
        ov = ((settings or {}).get("thresholds") or {}).get(k)
        if ov and len(ov) == 3:
            try:
                vals = tuple(float(x) for x in ov)
                asc = m["dir"] == "above"
                if (asc and vals[0] <= vals[1] <= vals[2]) or (not asc and vals[0] >= vals[1] >= vals[2]):
                    t = vals
            except (TypeError, ValueError):
                pass
        out[k] = t
    return out


def driver_history(key, snaps, reg_item, current_level):
    ev, prev = [], None
    for s in snaps:
        lv = (s.get("driver_levels") or {}).get(key) or "low"
        if prev is None:
            if lv in RE.ELEVATED:
                ev.append({"date": s["date"], "code": "detected", "level": lv})
        elif lv != prev:
            if prev not in RE.ELEVATED and lv in RE.ELEVATED:
                code = "detected"
            elif lv == "critical":
                code = "escalated"
            elif LV_ORDER.get(lv, 0) > LV_ORDER.get(prev, 0):
                code = "increased"
            elif lv == "low":
                code = "resolved"
            else:
                code = "decreased"
            ev.append({"date": s["date"], "code": code, "level": lv})
        prev = lv
    for h in (reg_item or {}).get("history") or []:
        ev.append({"date": str(h.get("when", ""))[:10], "code": "action" if h.get("to") in ("decision", "approved", "action") else "status",
                   "level": None, "note_ar": f"{h.get('from') or ''} → {h.get('to')} · {h.get('who') or ''}".strip(" ·")})
    ev.sort(key=lambda e: e["date"] or "")
    state = None
    if reg_item and reg_item.get("decision_id"):
        ba = reg_item.get("before_after") or {}
        state = "improved" if ba.get("result") == "improved" or current_level == "low" else ("still_active" if current_level in RE.ELEVATED else None)
    for e in ev:
        e["ar"] = EVENT_AR.get(e["code"], e["code"])
    return {"events": ev[-30:], "state": state, "state_ar": EVENT_AR.get(state) if state else None}


def analyze_drivers(risk, mods=None, *, customer_rows=None, settings=None, history=None, today=None, currency="SAR", sector=None):
    """risk: نتيجة analyze_risk (3.3). mods: نتائج الوحدات. history: لقطات 3.3 مع driver_levels و driver_scores."""
    mods, settings = mods or {}, settings or {}
    today = today or date.today()
    if not risk or not risk.get("has_data"):
        return {"has_data": False, "version": f"drivers-v{DRIVERS_VERSION}", "message_ar": "لا توجد بيانات كافية — ارفع بيانات الوحدات أولاً"}
    scope = (risk.get("scope") or {}).get("categories") or RE.CAT_ORDER
    snaps = sorted([h for h in history or [] if h.get("date")], key=lambda h: h["date"])
    extras = customer_extras(customer_rows) if "customer" in scope else {}
    prof = data_profiles(mods, risk.get("customers"))
    sup_in = extract_support(mods, extras, settings)
    sth = support_thresholds(settings)
    revenue = _n(_g(mods.get("finance"), "summary", "revenue", "net_sales")) if _avail(mods.get("finance")) else (risk.get("customers") or {}).get("revenue_current")
    pser = primary_series(mods)
    targets = settings.get("targets") or {}
    reg = {r["risk_key"]: r for r in risk.get("register") or []}
    disabled = set(settings.get("disabled") or [])

    # 1) سجلات المسببات: الرئيسية من 3.3 + المساندة
    drivers = []
    for d in risk["drivers"]:
        drivers.append({**d, "kind": "primary", "kind_ar": "مسبب رئيسي (يدخل في المؤشر)", "series": pser.get(d["key"]) or [], "series_comparable": True})
    for k, m in SUPPORT.items():
        if m["cat"] not in scope or k in disabled:
            continue
        inp = sup_in.get(k) or _na("غير مقروء")
        hl = [ (s.get("driver_levels") or {}).get(k) for s in snaps if (s.get("driver_levels") or {}).get(k)]
        sc = RE.score_driver(k, inp, sth[k], revenue=revenue, history_levels=hl, meta={"dir": m["dir"], "unit": m["unit"]})
        drivers.append({"key": k, "id": RE._rid("drv", k), "kind": "support", "kind_ar": "مسبب مساند (يشرح ولا يدخل في المؤشر)", "category": m["cat"],
                        "category_ar": RE.CATS[m["cat"]]["ar"], "name_ar": m["ar"], "unit": m["unit"], "direction": m["dir"], "value": inp.get("value"),
                        "previous": inp.get("previous"), "display_ar": None, "thresholds": list(sth[k]), "threshold_source": "company" if sth[k] != tuple(m["t"]) else "default",
                        "score": sc["score"], "level": sc["level"], "level_ar": RE.LEVEL_AR[sc["level"]], "score_parts": sc["parts"], "status": sc["status"],
                        "evidence": inp.get("evidence") or [], "impacts": inp.get("impacts") or [], "by_branch": inp.get("by_branch") or {},
                        "by_department": {}, "drill": [], "entities": inp.get("entities") or {}, "root_causes": [], "source_module": m["src"],
                        "source_ar": RE.MODULE_AR.get(m["src"], m["src"]), "link": m["link"], "reason_ar": inp.get("reason_ar"),
                        "needs_ar": None if inp.get("value") is not None else m["needs_ar"], "parent": m["parent"], "coverage": inp.get("coverage"),
                        "baseline": inp.get("baseline"), "series": inp.get("series") or [], "series_comparable": inp.get("series_unit") in (None, m["unit"]) and not inp.get("series_unit")})
    by = {d["key"]: d for d in drivers}

    # 2) المساهمة
    contrib = contributions(risk)
    for ck, c in contrib.items():
        for it in c["items"]:
            if it["key"] in by:
                by[it["key"]]["contribution"] = {"pct": it["pct"], "points": it["points"], "index_points": it["index_points"], "is_worst": it["is_worst"]}
    for k in ADDITIVE:
        if k in by:
            comp = _components(k, {x: by[x] for x in ADDITIVE[k] if x in by})
            by[k]["components"] = comp
            base = (by[k].get("contribution") or {}).get("pct")
            for c in comp:
                if base is not None and c["key"] in by:
                    by[c["key"]]["contribution"] = {"pct": round(base * c["share_pct"] / 100, 1), "via": k, "via_ar": by[k]["name_ar"],
                                                    "share_of_parent_pct": c["share_pct"], "points": None, "index_points": None}

    # 3) لكل مسبب: كيانات، ثقة، فجوة، اتجاه، استمرارية
    for d in drivers:
        d["entities"] = _entities(d)
        d["confidence"] = confidence_of(d["source_module"], d["value"] is not None or bool(d.get("display_ar")), prof, d.get("coverage"))
        d["gap"] = _gap(d, targets, d["series"] if d.get("series_comparable") else [])
        sc_series = _monthly_last([(s["date"], (s.get("driver_scores") or {}).get(d["key"])) for s in snaps if (s.get("driver_scores") or {}).get(d["key"]) is not None])
        if d["score"] is not None and sc_series and sc_series[-1][0] != today.strftime("%Y-%m"):
            sc_series.append((today.strftime("%Y-%m"), d["score"]))
        span = abs(d["thresholds"][2] - d["thresholds"][0]) if d.get("thresholds") else 1
        if len(sc_series) >= 3:
            tr, basis, pts, unit = classify_trend([v for _, v in sc_series], "above", 1.0), "score", sc_series, "درجة"
        elif d.get("series") and d.get("series_comparable"):
            pts = [(p, v) for p, v in d["series"] if v is not None]
            tr, basis, unit = classify_trend([v for _, v in pts], d["direction"], 0.05 * span), "value", d["unit"]
        elif d.get("series"):
            pts = [(p, v) for p, v in d["series"] if v is not None]
            tr, basis, unit = classify_trend([v for _, v in pts], "above", 0.0), "amount", "SAR"
        else:
            tr, basis, pts, unit = "insufficient", None, [], None
        d["trend"] = {"direction": tr, "ar": TREND_AR[tr], "basis": basis, "unit": unit, "points": [{"period": p, "value": v} for p, v in pts[-12:]],
                      "rule_ar": "على آخر 4 نقاط شهرية — لا حكم بأقل من 3 نقاط"}
        lv_month = _monthly_last([(s["date"], (s.get("driver_levels") or {}).get(d["key"]) or "low") for s in snaps]) if snaps else []
        if not lv_month and d.get("series") and d.get("series_comparable") and d.get("thresholds"):
            lv_month = [(p, RE.level_of(RE.base_score(v, tuple(d["thresholds"]), d["direction"]))) for p, v in d["series"] if v is not None]
        d["persistence"] = persistence(lv_month, d["level"])

    # 4) أسباب مرشحة + توصية + أولوية + سجل + تاريخ
    for d in drivers:
        cands = []
        if d["level"] in RE.ELEVATED:
            for ck in CANDIDATES.get(d["key"], []) + (CHILDREN.get(d["key"]) or []):
                c = by.get(ck)
                if not c or c["level"] not in RE.ELEVATED or ck in [x["key"] for x in cands]:
                    continue
                overlap = sorted(_flat(c["entities"]) & _flat(d["entities"]))
                strong = c["level"] in ("high", "critical")
                conf = "high" if strong and overlap else "likely" if strong or overlap else "possible"
                cands.append({"key": ck, "name_ar": c["name_ar"], "level": c["level"], "category_ar": c["category_ar"], "confidence": conf,
                              "confidence_ar": CAND_AR[conf], "shared_entities": overlap[:5], "evidence": c["evidence"][:2], "source_ar": c["source_ar"]})
            cands.sort(key=lambda x: (["high", "likely", "possible"].index(x["confidence"]), -LV_ORDER[x["level"]]))
        d["candidates"] = cands
        d["recommendation"] = _recommendation(d, d["gap"]) if d["level"] in RE.ELEVATED or d["score"] is None else None
        d["priority"] = _priority(d, revenue)
        rk = d["key"] if d["kind"] == "primary" else "x:" + d["key"]
        d["register_key"] = rk
        d["register"] = reg.get(rk)
        d["history"] = driver_history(d["key"], snaps, d["register"], d["level"])

    # 5) العروض
    elevated = [d for d in drivers if d["level"] in RE.ELEVATED]
    top = sorted([d for d in elevated if d["kind"] == "primary"], key=lambda d: -((d.get("contribution") or {}).get("index_points") or 0))
    return {
        "has_data": True, "version": f"drivers-v{DRIVERS_VERSION}", "as_of": today.isoformat(), "currency": currency,
        "risk": {"index": risk["index"], "categories": risk["categories"], "confidence": risk["confidence"], "scope": risk.get("scope")},
        "drivers": [_public(d) for d in drivers],
        "top": [_card(d) for d in (top + sorted([d for d in elevated if d["kind"] == "support"], key=lambda d: -d["score"]))[:8]],
        "by_risk": by_risk(drivers, contrib, scope),
        "contributions": contrib, "contribution_rule_ar": CONTRIB_RULE_AR,
        "heatmap": heatmap(drivers, scope),
        "branches": by_branch(drivers), "departments": by_department(drivers),
        "profiles": cross_module_profiles(drivers, scope),
        "chains": driver_chains(drivers, risk),
        "recommendations": recommendations(drivers),
        "alerts": [a for a in risk.get("alerts") or []],
        "executive": executive(drivers, snaps, today),
        "narratives": narratives(drivers, contrib, scope),
        "sector": sector_drivers(sector, by, mods),
        "data": {"profiles": prof, "rule_ar": CONF_RULE_AR, "data_needed": [{"key": k, **v, "category_ar": RE.CATS[v["cat"]]["ar"]} for k, v in DATA_NEEDED.items() if v["cat"] in scope]},
        "rules": {"priority_ar": PRIORITY_RULE_AR, "candidate_ar": CAND_RULE_AR, "contribution_ar": CONTRIB_RULE_AR, "score_ar": RE.SCORE_RULE_AR,
                  "support_thresholds": {k: {"values": list(v), "default": list(SUPPORT[k]["t"]), "name_ar": SUPPORT[k]["ar"], "unit": SUPPORT[k]["unit"],
                                             "direction": SUPPORT[k]["dir"]} for k, v in sth.items()},
                  "targets": targets},
        "ai_questions": ai_questions(drivers, contrib),
        "disclaimer_ar": "المسببات والمساهمات محسوبة بقواعد معلنة من بياناتك. الأسباب المرشحة علاقات من البيانات وليست أسباباً مؤكدة — التأكيد في مرحلة الأسباب الجذرية.",
    }



# ═══════════════════════════════════════════════════════════
# العروض (Views)
# ═══════════════════════════════════════════════════════════
def _public(d):
    keep = ("key", "kind", "kind_ar", "category", "category_ar", "name_ar", "unit", "direction", "value", "previous", "display_ar", "thresholds",
            "threshold_source", "score", "level", "level_ar", "score_parts", "evidence", "impacts", "by_branch", "by_department", "drill", "entities",
            "source_module", "source_ar", "link", "reason_ar", "needs_ar", "parent", "contribution", "components", "confidence", "gap", "trend",
            "persistence", "candidates", "recommendation", "priority", "register_key", "register", "history")
    out = {k: d.get(k) for k in keep}
    out["entities_ar"] = {ENT_AR.get(k, k): v for k, v in (d.get("entities") or {}).items()}
    return out


def _card(d):
    c = d.get("contribution") or {}
    imp = next((i for i in d.get("impacts") or [] if i["type"] in ("actual", "potential")), None) or next(iter(d.get("impacts") or []), None)
    return {"key": d["key"], "kind": d["kind"], "name_ar": d["name_ar"], "category": d["category"], "category_ar": d["category_ar"], "level": d["level"],
            "level_ar": d["level_ar"], "score": d["score"], "contribution_pct": c.get("pct"), "contribution_via_ar": c.get("via_ar"),
            "impact": imp, "confidence_pct": d["confidence"]["pct"], "sufficiency_ar": d["confidence"]["sufficiency_ar"],
            "affected_branches": len((d.get("entities") or {}).get("branches") or []), "priority": d.get("priority"),
            "trend_ar": d["trend"]["ar"], "persistence_ar": d["persistence"]["ar"], "evidence": d["evidence"][:2]}


def by_risk(drivers, contrib, scope):
    out = []
    for ck in [c for c in RE.CAT_ORDER if c in scope]:
        c = contrib.get(ck) or {}
        prim = sorted([d for d in drivers if d["category"] == ck and d["kind"] == "primary"], key=lambda d: -((d.get("contribution") or {}).get("pct") or -1))
        sup = sorted([d for d in drivers if d["category"] == ck and d["kind"] == "support"], key=lambda d: -(d["score"] if d["score"] is not None else -1))
        cross = []
        for ch in RE.CHAINS:
            for i, k in enumerate(ch["nodes"]):
                if RE.DRIVERS[k]["cat"] != ck:
                    continue
                for up in ch["nodes"][:i]:
                    u = next((d for d in drivers if d["key"] == up), None)
                    if u and u["category"] != ck and u["level"] in RE.ELEVATED and up not in [x["key"] for x in cross]:
                        cross.append({"key": up, "name_ar": u["name_ar"], "category_ar": u["category_ar"], "level": u["level"], "via_ar": ch["ar"]})
        out.append({"category": ck, "ar": RE.CATS[ck]["ar"], "icon": RE.CATS[ck]["icon"], "score": c.get("score"), "level": c.get("level"),
                    "primary": [{"key": d["key"], "name_ar": d["name_ar"], "level": d["level"], "score": d["score"], "value": d["value"], "unit": d["unit"],
                                 "contribution": d.get("contribution"), "components": d.get("components") or []} for d in prim],
                    "support": [{"key": d["key"], "name_ar": d["name_ar"], "level": d["level"], "score": d["score"], "value": d["value"], "unit": d["unit"],
                                 "contribution": d.get("contribution"), "reason_ar": d.get("reason_ar")} for d in sup],
                    "cross": cross, "unable": c.get("unable") or [],
                    "data_needed": [{"name_ar": v["ar"], "needs_ar": v["needs_ar"]} for v in DATA_NEEDED.values() if v["cat"] == ck]})
    return out


def heatmap(drivers, scope):
    """مسبب × خطر: مباشر = مستوى المسبب في فئته · غير مباشر = عبر سلسلة نحو فئة أخرى (درجة أخف)."""
    rows = []
    for d in sorted([d for d in drivers if d["level"] in RE.ELEVATED], key=lambda d: -d["score"]):
        cells = {c: None for c in RE.CAT_ORDER if c in scope}
        cells[d["category"]] = {"level": d["level"], "type": "direct"}
        key = d["key"] if d["kind"] == "primary" else d.get("parent")
        for ch in RE.CHAINS:
            if key not in ch["nodes"]:
                continue
            i = ch["nodes"].index(key)
            for k in ch["nodes"][i + 1:]:
                node = next((x for x in drivers if x["key"] == k), None)
                cat = RE.DRIVERS[k]["cat"]
                if node and cat in cells and cat != d["category"] and node["level"] in RE.ELEVATED:
                    lv = _down(min(d["level"], node["level"], key=lambda x: LV_ORDER[x]))
                    if not cells[cat] or LV_ORDER[lv] > LV_ORDER[cells[cat]["level"]]:
                        cells[cat] = {"level": lv, "type": "indirect", "via_ar": ch["ar"]}
        rows.append({"key": d["key"], "name_ar": d["name_ar"], "kind": d["kind"], "cells": cells})
    return {"rows": rows, "categories": [{"key": c, "ar": RE.CATS[c]["ar"]} for c in RE.CAT_ORDER if c in scope],
            "rule_ar": "مباشر = مستوى المسبب في فئة خطره · غير مباشر = المسبب يسبق مؤشراً مرتفعاً من فئة أخرى في سلسلة (درجة أخف بمستوى) — علاقة لا سببية"}


def by_branch(drivers):
    names = sorted({b for d in drivers for b in (d.get("by_branch") or {})})
    out = []
    for b in names:
        items = []
        for d in drivers:
            v = (d.get("by_branch") or {}).get(b)
            if v is None or not d.get("thresholds"):
                continue
            s = RE.base_score(v, tuple(d["thresholds"]), d["direction"])
            items.append({"key": d["key"], "name_ar": d["name_ar"], "kind": d["kind"], "category_ar": d["category_ar"], "value": v, "unit": d["unit"],
                          "score": s, "level": RE.level_of(s)})
        items.sort(key=lambda x: -x["score"])
        out.append({"branch": b, "top": items[:3], "all": items, "elevated": sum(1 for x in items if x["level"] in RE.ELEVATED)})
    out.sort(key=lambda r: -(r["top"][0]["score"] if r["top"] else 0))
    return {"rows": out, "rule_ar": "قيمة المسبب في الفرع مقابل نفس الحدود — كل فرع له مسبباته الأعلى، لا تحليل واحد لكل الفروع"}


def by_department(drivers):
    by = {d["key"]: d for d in drivers}
    out = []
    for dep, keys in DEPARTMENTS.items():
        ds = [by[k] for k in keys if k in by]
        ev = sorted([d for d in ds if d["score"] is not None], key=lambda d: -d["score"])
        main = ev[0] if ev and ev[0]["level"] in RE.ELEVATED else None
        out.append({"department": dep, "main": {"key": main["key"], "name_ar": main["name_ar"], "level": main["level"], "score": main["score"],
                                                "evidence": main["evidence"][:1]} if main else None,
                    "elevated": [{"key": d["key"], "name_ar": d["name_ar"], "level": d["level"]} for d in ev if d["level"] in RE.ELEVATED],
                    "evaluated": len(ev), "total": len(ds)})
    out.sort(key=lambda r: -(r["main"]["score"] if r["main"] else -1))
    data_depts = {}
    for d in drivers:
        for k, v in (d.get("by_department") or {}).items():
            if v is not None and d.get("thresholds"):
                s = RE.base_score(v, tuple(d["thresholds"]), d["direction"])
                data_depts.setdefault(k, []).append({"name_ar": d["name_ar"], "value": v, "unit": d["unit"], "score": s, "level": RE.level_of(s)})
    return {"functional": out, "from_data": [{"department": k, "top": sorted(v, key=lambda x: -x["score"])[:3]} for k, v in sorted(data_depts.items())],
            "rule_ar": "كل مسبب مملوك لإدارة وظيفية (المشتريات، العمليات، المبيعات، المالية، الموارد البشرية، الامتثال). المسبب الرئيسي = الأعلى درجة إن كان مرتفعاً"}


MODULE_FLOW = ["purchases", "inventory", "operations", "hr", "sales", "leakage", "finance", "cashflow", "tax"]


def cross_module_profiles(drivers, scope):
    """ملف المسبب عبر الوحدات: يجمع أدلة الفئة من كل وحدة بترتيب تدفق العمل (المورد → … → المالية)."""
    out = []
    by = {d["key"]: d for d in drivers}
    for ck in [c for c in RE.CAT_ORDER if c in scope]:
        keys = {d["key"] for d in drivers if d["category"] == ck and d["level"] in RE.ELEVATED}
        for k in list(keys):
            keys |= {c["key"] for c in by[k].get("candidates") or []}
        steps = []
        for mod in MODULE_FLOW:
            ds = sorted([by[k] for k in keys if by[k]["source_module"] == mod], key=lambda d: -(d["score"] or 0))
            if ds:
                steps.append({"module": mod, "module_ar": RE.MODULE_AR.get(mod, mod), "items": [{"key": d["key"], "name_ar": d["name_ar"], "level": d["level"],
                                                                                              "value": d["value"], "unit": d["unit"], "evidence": (d["evidence"] or [""])[0]} for d in ds]})
        if steps:
            out.append({"category": ck, "ar": f"ملف {RE.CATS[ck]['ar']}", "steps": steps,
                        "note_ar": "أدلة من وحدات مختلفة مرتبة حسب تدفق العمل — تزامنها علاقة لا سببية"})
    return out


def driver_chains(drivers, risk):
    by = {d["key"]: d for d in drivers}
    out = []
    for ch in risk.get("chains") or []:
        if not ch.get("active"):
            continue
        path = [{"key": k, "name_ar": by[k]["name_ar"] if k in by else k, "level": by[k]["level"] if k in by else None,
                 "value": by[k]["value"] if k in by else None, "unit": by[k]["unit"] if k in by else ""} for k in ch["active_path"]]
        last = RE.DRIVERS[ch["active_path"][-1]]["cat"]
        out.append({"id": ch["id"], "ar": ch["ar"], "path": path, "risk_ar": RE.CATS[last]["ar"], "note_ar": RE.CHAIN_NOTE_AR})
    return out


def recommendations(drivers):
    order = {"immediate": 0, "high": 1, "medium": 2, "monitor": 3}
    xs = [d for d in drivers if d.get("recommendation") and d.get("priority") and d["score"] is not None]
    xs.sort(key=lambda d: (order[d["priority"]["code"]], -(d["score"] or 0)))
    return [{"key": d["key"], "name_ar": d["name_ar"], "category_ar": d["category_ar"], "priority": d["priority"], "level": d["level"],
             "evidence": d["evidence"][:2], "recommendation": d["recommendation"], "owner_ar": next((dep for dep, ks in DEPARTMENTS.items() if d["key"] in ks), None),
             "register_key": d["register_key"], "decision_id": (d.get("register") or {}).get("decision_id"),
             "status_ar": (d.get("register") or {}).get("status_ar")} for d in xs]


def executive(drivers, snaps, today):
    prim = [d for d in drivers if d["level"] in RE.ELEVATED]
    top5 = sorted([d for d in prim if d["kind"] == "primary"], key=lambda d: -((d.get("contribution") or {}).get("index_points") or 0))[:5]
    def money(d, kinds):
        return sum((i["amount"] for i in d.get("impacts") or [] if i["type"] in kinds), 0.0)
    fin = max(prim, key=lambda d: (money(d, ("actual", "potential")), money(d, ("exposure",))), default=None)
    if fin and not (money(fin, ("actual", "potential")) or money(fin, ("exposure",))):
        fin = None
    past = None
    for s in reversed(snaps):
        if s["date"] <= (today - timedelta(days=30)).isoformat():
            past = s; break
    emerging, rule = None, None
    if past:
        ps = past.get("driver_scores") or {}
        deltas = [(d, d["score"] - ps[d["key"]]) for d in prim if d["kind"] == "primary" and ps.get(d["key"]) is not None]
        deltas = [x for x in deltas if x[1] > 0]
        if deltas:
            emerging, rule = max(deltas, key=lambda x: x[1])[0], f"أكبر ارتفاع في الدرجة منذ {past['date']}"
    if not emerging:
        cand = [d for d in prim if d["trend"]["direction"] in ("accelerating", "worsening")] or [d for d in prim if d["persistence"]["code"] == "new"]
        if cand:
            emerging = max(cand, key=lambda d: d["score"])
            rule = "اتجاه يسوء" if emerging["trend"]["direction"] in ("accelerating", "worsening") else "ظهر حديثاً"
    imm = [d for d in prim if (d.get("priority") or {}).get("code") == "immediate" and not (d.get("register") or {}).get("decision_id")]
    act = max(imm, key=lambda d: d["score"], default=None)
    pick = lambda d: {"key": d["key"], "name_ar": d["name_ar"], "level": d["level"], "category_ar": d["category_ar"], "score": d["score"]} if d else None
    return {"top5": [{**pick(d), "contribution_pct": (d.get("contribution") or {}).get("pct"), "index_points": (d.get("contribution") or {}).get("index_points"),
                      "arrow": "↑" if d["direction"] == "above" else "↓"} for d in top5],
            "biggest_financial": {**pick(fin), "actual_potential": money(fin, ("actual", "potential")) or None, "exposure": money(fin, ("exposure",)) or None} if fin else None,
            "emerging": {**pick(emerging), "rule_ar": rule} if emerging else None,
            "immediate": {**pick(act), "action_ar": (act.get("recommendation") or {}).get("text_ar")} if act else None,
            "rule_ar": "أعلى 5 = أكبر نقاط في مؤشر المخاطر · الأكبر مالياً = أكبر أثر فعلي/محتمل ثم التعرض · الناشئ = أكبر ارتفاع درجة خلال 30 يوماً (أو اتجاه يسوء) · الفوري = أولوية «فوري» بلا قرار"}


def narratives(drivers, contrib, scope):
    """تفسير حتمي لكل فئة: المسبب الأكثر تأثيراً ثم الذي يليه، مع الدليل — بلا ذكاء اصطناعي."""
    by = {d["key"]: d for d in drivers}
    out = {}
    for ck in [c for c in RE.CAT_ORDER if c in scope]:
        items = [i for i in (contrib.get(ck) or {}).get("items") or [] if by.get(i["key"], {}).get("level") in RE.ELEVATED]
        if not items:
            out[ck] = {"text_ar": "لا يوجد مسبب مرتفع في هذه الفئة وفق القواعد الحالية." if (contrib.get(ck) or {}).get("items") else "تعذّر التحديد — بيانات غير كافية لهذه الفئة.", "actions": []}
            continue
        a = by[items[0]["key"]]
        txt = f"المسبب الأكثر تأثيراً حالياً في {RE.CATS[ck]['ar']} هو «{a['name_ar']}» (مساهمة {items[0]['pct']}%)"
        if len(items) > 1:
            b = by[items[1]["key"]]
            txt += f"، ويليه «{b['name_ar']}» ({items[1]['pct']}%)"
        ev = [by[i["key"]]["evidence"][0] for i in items[:2] if by[i["key"]]["evidence"]]
        if ev:
            txt += ". الدليل: " + "؛ ".join(ev)
        comp = a.get("components") or []
        if comp:
            txt += f". أكبر مكوّن: {comp[0]['name_ar']} ({comp[0]['share_pct']}% من المبلغ)"
        out[ck] = {"text_ar": txt + ".", "actions": [by[i["key"]]["recommendation"]["text_ar"] for i in items[:3] if by[i["key"]].get("recommendation")]}
    return out


def sector_drivers(sector, by, mods):
    cfg = SECTOR_DRIVERS.get(sector or "other") or []
    kpis = {k["code"]: k for k in (_g(mods.get("finance"), "sector", "kpis") or [])}
    out = []
    for label, ref in cfg:
        if ref.startswith("need:"):
            out.append({"label": label, "type": "data_needed", "needs_ar": SECTOR_NEEDS.get(ref[5:]), "status_ar": "بيانات غير متوفرة"})
        elif ref.startswith("kpi:"):
            k = kpis.get(ref[4:])
            if not k or k.get("value") is None:
                out.append({"label": label, "type": "kpi", "status_ar": "غير متاح", "needs_ar": "يحتاج المبيعات + التكلفة/الرواتب حسب المؤشر"})
            else:
                worse = ref[4:] in KPI_WORSE_HIGH
                gap = None if k.get("baseline") is None else round(k["value"] - k["baseline"], 2)
                bad = gap is not None and ((gap > 0) if worse else (gap < 0))
                out.append({"label": label, "type": "kpi", "value": k["value"], "baseline": k["baseline"], "baseline_source": k.get("baseline_source"),
                            "gap": gap, "status_ar": "أسوأ من خط الأساس" if bad else ("ضمن خط الأساس" if gap is not None else "بلا خط أساس"),
                            "elevated": bad, "formula_ar": k.get("formula_ar")})
        else:
            d = by.get(ref)
            out.append({"label": label, "type": "driver", "key": ref, "name_ar": d["name_ar"] if d else ref, "level": d["level"] if d else None,
                        "value": d["value"] if d else None, "unit": d["unit"] if d else "", "status_ar": (d["level_ar"] if d else "خارج صلاحيتك")})
    return {"sector": sector or "other", "ar": RE.sector_profile(sector)["ar"], "items": out,
            "note_ar": "نفس محرك المخاطر ونفس البيانات الموحّدة ونفس إطار الأدلة — القطاع يحدد أي المسببات تُبرز"}


def ai_questions(drivers, contrib):
    qs = []
    for ck, ar in (("profit", "ليش الربحية نزلت؟"), ("liquidity", "ليش السيولة تحت ضغط؟"), ("customer", "ليش نفقد العملاء؟"), ("operational", "ما سبب التأخير التشغيلي؟")):
        if any(by_lv for by_lv in [i for i in (contrib.get(ck) or {}).get("items") or [] if i["level"] in RE.ELEVATED]):
            qs.append(ar)
    top = sorted([d for d in drivers if d["level"] in RE.ELEVATED], key=lambda d: -d["score"])[:2]
    qs += [f"ما الدليل على «{d['name_ar']}» وكم يساهم؟" for d in top]
    qs += ["ما أول ثلاثة إجراءات تخفف أكبر المسببات؟", "ما البيانات الناقصة التي تقلل الثقة؟"]
    return qs
