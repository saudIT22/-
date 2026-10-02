"""
NABBAH — Sector Intelligence Layer (Phase 3 foundation).
One engine, one canonical data model, 11 sector profiles. Each profile declares: terminology, sector KPIs, which
leakage types apply, how each is computed from canonical data, what data it needs when unavailable, overlaps with the
core leakage types (so nothing is counted twice), how much of it is recoverable (and why), drill paths and actions.
Every module (Financial, Leakage, Risk, Operations, Forecasting…) asks this layer — no per-sector copies of pages.
No benchmark numbers are invented: thresholds default to the company's own history unless the user configures one.
"""
from statistics import median

SECTOR_VERSION = "1.0"
CORE_KPIS = ["revenue", "gross_margin", "net_margin", "opex_ratio", "cash_balance", "dso"]

# مصادر الحساب المتاحة في الطبقة (من البيانات الموحدة): نسبة تكلفة إلى إيراد، أثر أسعار الموردين، فاقد المخزون
KPI_DEFS = {
    "food_cost_pct": {"name_ar": "تكلفة الطعام %", "num": "cogs", "den": "revenue", "formula_ar": "تكلفة المبيعات ÷ صافي المبيعات"},
    "cogs_pct": {"name_ar": "تكلفة البضاعة %", "num": "cogs", "den": "revenue", "formula_ar": "تكلفة المبيعات ÷ صافي المبيعات"},
    "labor_pct": {"name_ar": "تكلفة العمالة %", "num": "payroll", "den": "revenue", "formula_ar": "الرواتب ÷ صافي المبيعات"},
    "delivery_commission_pct": {"name_ar": "عمولات التوصيل %", "num": "delivery_exp", "den": "delivery_rev", "formula_ar": "مصروف التوصيل/العمولات ÷ مبيعات قناة التوصيل"},
    "discount_pct": {"name_ar": "نسبة الخصم", "num": "discounts", "den": "gross", "formula_ar": "الخصومات ÷ إجمالي المبيعات"},
    "returns_pct": {"name_ar": "نسبة المرتجعات", "num": "returns", "den": "gross", "formula_ar": "المرتجعات ÷ إجمالي المبيعات"},
    "revenue_per_employee": {"name_ar": "الإيراد لكل موظف", "num": "revenue", "den": "headcount", "formula_ar": "صافي المبيعات ÷ عدد الموظفين", "unit": "money"},
    "shrinkage_pct": {"name_ar": "فاقد المخزون %", "num": "shrinkage", "den": "revenue", "formula_ar": "قيمة التسويات السالبة ÷ صافي المبيعات"},
}


# هدف بند التسرب ينطبق على مؤشره (مصدر واحد للهدف)
KPI_ITEM = {"food_cost_pct": "food_cost", "cogs_pct": "food_cost", "labor_pct": "labor_cost",
            "delivery_commission_pct": "delivery_commission", "shrinkage_pct": "shrinkage"}


def _ratio_leak(code, name, group, num, den, drill, action, needs, supersedes=(), extra=None):
    return {"code": code, "name_ar": name, "group": group, "kind": "ratio", "num": num, "den": den, "drill": drill,
            "action_ar": action, "needs_ar": needs, "supersedes": list(supersedes), "recoverability": "policy", **(extra or {})}


def _needs(code, name, group, needs, drill=None):
    return {"code": code, "name_ar": name, "group": group, "kind": "data_needed", "needs_ar": needs, "drill": drill}


FOOD_COST = _ratio_leak("food_cost", "ارتفاع تكلفة الطعام", "تكلفة الطعام", "cogs", "revenue",
                        [("المشتريات", "company-purchases-intelligence.html#cost"), ("المخزون", "company-inventory-intelligence.html")],
                        "راجع أسعار الموردين ووصفات الأصناف (Portion) والهدر", "يحتاج تكلفة المنتجات أو المخزون مع المبيعات")
LABOR = lambda grp="العمالة": _ratio_leak("labor_cost", "ارتفاع تكلفة العمالة", grp, "payroll", "revenue",
                                          [("الموارد البشرية", "company-hr-intelligence.html#productivity")],
                                          "طابق جدول الورديات مع ساعات الذروة وراجع العمل الإضافي", "يحتاج الرواتب (مصروفات أو كشف بنكي) مع المبيعات",
                                          supersedes=("payroll",))
DELIVERY = _ratio_leak("delivery_commission", "ارتفاع عمولات التوصيل", "التوصيل", "delivery_exp", "delivery_rev",
                       [("المبيعات — قناة التوصيل", "company-sales-intelligence.html"), ("العمليات", "company-operations-intelligence.html#sla")],
                       "فاوض نسب العمولة وحوّل جزءاً من الطلبات لقناة الاستلام/التطبيق الخاص", "يحتاج مصروف التوصيل/العمولات وقناة «توصيل» في المبيعات",
                       supersedes=("delivery",))
SHRINK = {"code": "shrinkage", "name_ar": "فاقد المخزون (تسويات سالبة)", "group": "المخزون", "kind": "ratio", "num": "shrinkage", "den": "revenue",
          "drill": [("المخزون", "company-inventory-intelligence.html")], "action_ar": "جرد دوري للأصناف الأعلى فاقداً وضبط الاستلام والصرف",
          "needs_ar": "يحتاج عمود «التسويات» وتكلفة الوحدة في ملف المخزون", "supersedes": [], "recoverability": "policy"}
SUPPLIER = {"code": "supplier_price", "name_ar": "ارتفاع أسعار الموردين", "group": "المشتريات", "kind": "supplier",
            "drill": [("المشتريات ← المورد ← الصنف", "company-purchases-intelligence.html#cost")],
            "action_ar": "فاوض المورد أو حوّل الكمية للمورد الأقل سعراً لنفس الصنف", "needs_ar": "يحتاج مشتريات نفس الأصناف في فترتين مع سعر الوحدة",
            "recoverability": "market", "part_of": None}
OBSOLETE = {"code": "obsolete_stock", "name_ar": "مخزون متقادم / منتهي", "group": "المخزون", "kind": "exposure",
            "drill": [("المخزون — المتقادم", "company-inventory-intelligence.html")], "action_ar": "تصريف بعرض محدود أو إرجاع للمورد قبل الإتلاف",
            "needs_ar": "يحتاج أرصدة المخزون وحركته"}

PROFILES = {
    "fnb": {"name_ar": "مطاعم وكافيهات", "terms": {"unit": "الطلب", "customer": "الضيف", "cogs": "تكلفة الطعام"},
            "kpis": ["food_cost_pct", "labor_pct", "delivery_commission_pct", "discount_pct", "returns_pct"],
            "leakage": [dict(FOOD_COST, part_of=None), dict(SUPPLIER, part_of="food_cost"), LABOR(), DELIVERY, SHRINK, OBSOLETE,
                        _needs("waste", "هدر المواد (Waste)", "تكلفة الطعام", "سجل الهدر اليومي (الصنف، الكمية، السبب)"),
                        _needs("portion_variance", "فرق الحصص (Portion Variance)", "تكلفة الطعام", "الوصفات القياسية مقابل الاستهلاك الفعلي"),
                        _needs("voids", "الإلغاءات والمستردات (Voids/Refunds)", "المبيعات", "سجل الإلغاءات من نقاط البيع")],
            "watch_ar": "تكلفة الطعام والعمالة وعمولات التوصيل هي أكبر ضغط على هامش المطعم"},
    "retail": {"name_ar": "تجارة تجزئة", "terms": {"unit": "الفاتورة", "customer": "العميل", "cogs": "تكلفة البضاعة"},
               "kpis": ["cogs_pct", "discount_pct", "returns_pct", "shrinkage_pct", "revenue_per_employee"],
               "leakage": [SHRINK, dict(SUPPLIER), OBSOLETE, LABOR("إنتاجية المتجر"),
                           _needs("stockout_lost_sales", "مبيعات ضائعة بسبب النفاد", "المخزون", "أرصدة يومية أو سجل طلبات غير ملبّاة")],
               "watch_ar": "الفاقد والتخفيضات والنفاد"},
    "ecommerce": {"name_ar": "تجارة إلكترونية", "terms": {"unit": "الطلب", "customer": "العميل", "cogs": "تكلفة البضاعة"},
                  "kpis": ["cogs_pct", "discount_pct", "returns_pct", "delivery_commission_pct"],
                  "leakage": [DELIVERY, dict(SUPPLIER), OBSOLETE,
                              _needs("failed_orders", "الطلبات الفاشلة والمرتجعة من الشحن", "الشحن", "حالة الشحن لكل طلب"),
                              _needs("payment_fees", "رسوم بوابات الدفع", "المدفوعات", "كشف رسوم بوابة الدفع")],
                  "watch_ar": "المرتجعات وتكلفة الشحن والخصومات"},
    "manufacturing": {"name_ar": "تصنيع", "terms": {"unit": "أمر الإنتاج", "customer": "العميل", "cogs": "تكلفة الإنتاج"},
                      "kpis": ["cogs_pct", "labor_pct", "shrinkage_pct"],
                      "leakage": [dict(SUPPLIER), SHRINK, LABOR(), OBSOLETE,
                                  _needs("scrap", "التالف وإعادة التصنيع", "الإنتاج", "سجل التالف لكل أمر إنتاج"),
                                  _needs("downtime", "توقف خطوط الإنتاج", "الإنتاج", "سجل التوقفات (المشاكل التشغيلية) مع المدة")],
                      "watch_ar": "تكلفة المواد والتالف والتوقف"},
    "contracting": {"name_ar": "مقاولات", "terms": {"unit": "المشروع", "customer": "المالك", "cogs": "تكلفة المشروع"},
                    "kpis": ["cogs_pct", "labor_pct"],
                    "leakage": [dict(SUPPLIER), LABOR("العمالة"),
                                _needs("project_overrun", "تجاوز تكلفة المشاريع", "المشاريع", "موازنة وتكلفة فعلية لكل مشروع"),
                                _needs("material_variance", "فرق المواد", "المشاريع", "الكميات المخططة مقابل المصروفة لكل مشروع"),
                                _needs("change_orders", "أوامر التغيير غير المفوترة", "المشاريع", "سجل أوامر التغيير وحالة فوترتها"),
                                _needs("project_delay", "غرامات/تكلفة تأخر المشاريع", "المشاريع", "مواعيد التسليم المخططة والفعلية")],
                    "watch_ar": "تجاوز التكلفة وأوامر التغيير والتحصيل"},
    "distribution": {"name_ar": "توزيع", "terms": {"unit": "الشحنة", "customer": "العميل", "cogs": "تكلفة البضاعة"},
                     "kpis": ["cogs_pct", "discount_pct", "returns_pct", "shrinkage_pct"],
                     "leakage": [dict(SUPPLIER), SHRINK, OBSOLETE, _needs("route_cost", "تكلفة المسارات والتوصيل", "التوزيع", "تكلفة الأسطول لكل مسار")],
                     "watch_ar": "هوامش الأصناف والتحصيل والمخزون الراكد"},
    "services": {"name_ar": "خدمات", "terms": {"unit": "الخدمة", "customer": "العميل", "cogs": "تكلفة تقديم الخدمة"},
                 "kpis": ["labor_pct", "revenue_per_employee", "discount_pct"],
                 "leakage": [LABOR("العمالة والإنتاجية"), _needs("utilization", "انخفاض استغلال الموظفين", "الإنتاجية", "ساعات قابلة للفوترة لكل موظف"),
                             _needs("unbilled", "خدمات منفذة غير مفوترة", "الفوترة", "سجل الأعمال المنجزة مقابل الفواتير")],
                 "watch_ar": "تكلفة العمالة مقابل الإيراد والاستغلال"},
    "clinics": {"name_ar": "عيادات", "terms": {"unit": "الزيارة", "customer": "المراجع", "cogs": "المستهلكات"},
                "kpis": ["labor_pct", "revenue_per_employee", "discount_pct"],
                "leakage": [LABOR("إنتاجية الأطباء والطاقم"), dict(SUPPLIER, name_ar="ارتفاع أسعار المستهلكات"),
                            _needs("no_show", "عدم حضور المواعيد (No-show)", "المواعيد", "سجل المواعيد وحالة الحضور"),
                            _needs("appointment_utilization", "انخفاض استغلال المواعيد", "المواعيد", "الطاقة المتاحة لكل طبيب والمواعيد المحجوزة"),
                            _needs("insurance_rejections", "رفض مطالبات التأمين", "التحصيل", "سجل المطالبات وحالتها")],
                "watch_ar": "عدم الحضور ورفض التأمين والمستهلكات"},
    "hospitals": {"name_ar": "مستشفيات", "terms": {"unit": "الحالة", "customer": "المريض", "cogs": "المستهلكات الطبية"},
                  "kpis": ["labor_pct", "revenue_per_employee"],
                  "leakage": [LABOR("الطاقم الطبي"), dict(SUPPLIER, name_ar="ارتفاع أسعار المستهلكات الطبية"), OBSOLETE,
                              _needs("insurance_rejections", "رفض مطالبات التأمين", "التحصيل", "سجل المطالبات وحالتها"),
                              _needs("bed_utilization", "انخفاض إشغال الأسرّة", "الطاقة", "إشغال الأسرّة اليومي")],
                  "watch_ar": "مطالبات التأمين والإشغال والمستهلكات"},
    "logistics": {"name_ar": "لوجستيات", "terms": {"unit": "الشحنة", "customer": "العميل", "cogs": "تكلفة التشغيل"},
                  "kpis": ["labor_pct", "revenue_per_employee"],
                  "leakage": [LABOR("السائقون والعمالة"), _needs("fuel", "تكلفة الوقود لكل شحنة", "الأسطول", "استهلاك الوقود لكل مركبة/رحلة"),
                              _needs("idle_fleet", "الأسطول المتوقف", "الأسطول", "ساعات تشغيل المركبات"),
                              _needs("failed_delivery", "التوصيل الفاشل وإعادة المحاولة", "التشغيل", "حالة كل شحنة")],
                  "watch_ar": "الوقود والتوصيل الفاشل واستغلال الأسطول"},
    "other": {"name_ar": "أخرى", "terms": {"unit": "المعاملة", "customer": "العميل", "cogs": "تكلفة المبيعات"},
              "kpis": ["cogs_pct", "labor_pct", "discount_pct", "returns_pct"], "leakage": [dict(SUPPLIER), LABOR()],
              "watch_ar": "الهوامش والمصروفات"},
}
ALIASES = {"restaurant": "fnb", "cafe": "fnb", "restaurants": "fnb"}


def get_profile(sector):
    key = ALIASES.get(sector or "", sector or "other")
    key = key if key in PROFILES else "other"
    p = dict(PROFILES[key])
    p["key"] = key
    p["core_kpis"] = CORE_KPIS
    return p


def compute_kpis(sector, monthly, cur, baseline_months, targets=None):
    """مؤشرات القطاع لكل شهر + خط أساس (الهدف المُعدّ وإلا وسيط الأشهر السابقة). غياب البسط أو المقام = غير متاح."""
    p, targets = get_profile(sector), targets or {}
    out = []
    for code in p["kpis"]:
        d = KPI_DEFS[code]
        def val(m):
            row = monthly.get(m) or {}
            a, b = row.get(d["num"]), row.get(d["den"])
            return None if a is None or not b else (a / b if d.get("unit") == "money" else a / b * 100)
        v = val(cur)
        hist = [val(m) for m in baseline_months if val(m) is not None]
        tg = targets.get(code) if targets.get(code) not in (None, "") else targets.get(KPI_ITEM.get(code, ""))
        base = tg if tg not in (None, "") else (median(hist) if hist else None)
        out.append({"code": code, "name_ar": d["name_ar"], "formula_ar": d["formula_ar"], "unit": d.get("unit", "percent"),
                    "value": None if v is None else round(v, 2), "baseline": None if base is None else round(float(base), 2),
                    "baseline_source": "configured" if tg not in (None, "") else ("historical" if hist else None),
                    "available": v is not None, "series": [{"period": m, "value": None if val(m) is None else round(val(m), 2)} for m in sorted(monthly)][-12:]})
    return out


def sector_leakage(sector, monthly, cur, baseline_months, *, supplier_items=None, obsolete_value=None, targets=None):
    """بنود التسرب الخاصة بالقطاع. نوع «ratio»: (النسبة الحالية − النسبة المرجعية) × المقام الحالي."""
    p, targets, supplier_items = get_profile(sector), targets or {}, supplier_items or []
    items = []
    for t in p["leakage"]:
        base_item = {"code": t["code"], "name_ar": t["name_ar"], "group": t["group"], "drill": t.get("drill") or [],
                     "action_ar": t.get("action_ar"), "sector": p["key"]}
        if t["kind"] == "data_needed":
            items.append({**base_item, "available": False, "in_total": False, "reason_ar": f"غير متاح — {t['needs_ar']}"})
            continue
        if t["kind"] == "ratio":
            def ratio(m):
                r = monthly.get(m) or {}
                a, b = r.get(t["num"]), r.get(t["den"])
                return None if a is None or not b else a / b
            def leak(m, base):
                r, x = ratio(m), (monthly.get(m) or {}).get(t["den"])
                return None if r is None or base is None else max(0.0, (r - base) * x)
            hist = [ratio(m) for m in baseline_months if ratio(m) is not None]
            tgt = targets.get(t["code"])
            base = (float(tgt) / 100) if tgt not in (None, "") else (median(hist) if hist else None)
            cur_r = ratio(cur)
            if cur_r is None or base is None:
                items.append({**base_item, "available": False, "in_total": False,
                              "reason_ar": "غير متاح — " + (t["needs_ar"] if cur_r is None else "لا يوجد خط أساس (شهر سابق أو هدف مُعدّ)")})
                continue
            amt = leak(cur, base)
            series = [{"period": m, "amount": None if leak(m, base) is None else round(leak(m, base), 2)} for m in sorted(monthly)[-6:]]
            recent = [x["amount"] for x in series[-3:] if x["amount"] is not None]
            items.append({**base_item, "available": True, "in_total": True, "kind": "ratio", "amount": round(amt, 2),
                          "rate_pct": round(cur_r * 100, 2), "baseline_pct": round(base * 100, 2),
                          "baseline_source": "configured" if tgt not in (None, "") else "historical",
                          "money_ar": f"{t['name_ar'].replace('ارتفاع ', '')}: {cur_r * 100:.1f}% مقابل {base * 100:.1f}% — أثر {amt:,.0f} ريال على الربح" if amt else f"ضمن المستوى المرجعي ({cur_r * 100:.1f}%)",
                          "method_ar": f"(النسبة الحالية − المرجعية) × {('صافي المبيعات' if t['den'] == 'revenue' else 'مبيعات قناة التوصيل')} الحالية",
                          "supersedes": t.get("supersedes", []), "recoverable": round(amt, 2),
                          "recoverable_basis_ar": "قابل للاسترداد كاملاً: مستوى سبق للشركة تحقيقه أو هدف مُعتمد",
                          "recurring": sum(1 for a in recent if a and a > 0) >= 2 and bool(amt), "series": series})
        elif t["kind"] == "supplier":
            inc = [s for s in supplier_items if (s.get("impact") or 0) > 0]
            if not supplier_items:
                items.append({**base_item, "available": False, "in_total": False, "reason_ar": "غير متاح — " + t["needs_ar"]})
                continue
            amt = sum(s["impact"] for s in inc)
            pot = sum(s.get("potential_saving") or 0 for s in supplier_items)
            part_of = t.get("part_of")
            items.append({**base_item, "available": True, "in_total": part_of is None, "part_of": part_of, "kind": "supplier",
                          "amount": round(amt, 2), "recoverable": round(min(pot, amt) if amt else pot, 2),
                          "recoverable_basis_ar": "سعر السوق لا يُسترد كاملاً — المتاح هو الفرق عن أقل سعر مماثل لدى مورد آخر لنفس الصنف",
                          "money_ar": f"ارتفاع أسعار الشراء كلّف {amt:,.0f} ريال هذه الفترة" if amt else "لا ارتفاع في أسعار الأصناف المقارنة",
                          "method_ar": "(سعر الوحدة الحالي − المتوسط التاريخي لنفس الصنف) × الكمية المشتراة",
                          "evidence": sorted(inc, key=lambda s: -s["impact"])[:6], "recurring": False,
                          "note_ar": "جزء من «تكلفة الطعام» — يُعرض كسبب ولا يُضاف مرتين" if part_of else None})
        elif t["kind"] == "exposure":
            if obsolete_value is None:
                items.append({**base_item, "available": False, "in_total": False, "reason_ar": "غير متاح — " + t["needs_ar"]})
                continue
            items.append({**base_item, "available": True, "in_total": False, "kind": "exposure", "amount": round(obsolete_value, 2),
                          "recoverable": None, "recoverable_basis_ar": "قيمة التصريف غير معروفة — لا يُقدّر المسترد",
                          "money_ar": f"{obsolete_value:,.0f} ريال مخزون متقادم معرّض للإتلاف", "method_ar": "قيمة الأصناف المصنفة متقادمة في ذكاء المخزون",
                          "note_ar": "تعرّض (رأس مال محتجز) — لا يُضاف لإجمالي التسرب", "recurring": False})
    return {"sector": p["key"], "sector_name_ar": p["name_ar"], "items": items, "watch_ar": p.get("watch_ar"), "terms": p.get("terms")}


# ── طبقة القطاع للضرائب (3.2): نفس محرك الضريبة — أنواع المعاملات وملاحظات التصنيف تختلف حسب القطاع
TAX_PROFILES = {
    "fnb": {"transactions": ["مبيعات نقاط البيع (فواتير مبسطة)", "التوصيل وتطبيقات الطلب", "المستردات والإلغاءات", "الخصومات", "مبيعات الجملة/الشركات (فواتير ضريبية)"],
            "notes_ar": "عمولات التطبيقات فواتير مشتريات بضريبة مدخلات؛ تأكد أن المبيعات عبر التطبيقات مسجلة بقيمتها الكاملة"},
    "retail": {"transactions": ["مبيعات المتجر", "المبيعات الإلكترونية", "المرتجعات (إشعارات دائنة)", "العروض والتخفيضات"], "notes_ar": "المرتجعات يجب أن تقابلها إشعارات دائنة"},
    "ecommerce": {"transactions": ["طلبات المتجر الإلكتروني", "الشحن", "المرتجعات", "رسوم بوابات الدفع"], "notes_ar": "رسوم الشحن ضمن الأساس الخاضع عند تحصيلها من العميل"},
    "manufacturing": {"transactions": ["فواتير مبيعات بين منشآت", "مشتريات المواد الخام", "الصادرات"], "notes_ar": "الصادرات قد تكون بنسبة صفرية حسب الشروط — صنّفها"},
    "contracting": {"transactions": ["فواتير المستخلصات (الفوترة المرحلية)", "المحتجزات", "الإشعارات الدائنة/المدينة", "أوامر التغيير"],
                    "notes_ar": "توقيت استحقاق الضريبة في المستخلصات والمحتجزات يحتاج تصنيفاً دقيقاً"},
    "distribution": {"transactions": ["فواتير ضريبية للعملاء", "المرتجعات", "خصومات الكميات"], "notes_ar": "خصومات ما بعد البيع تحتاج إشعارات"},
    "services": {"transactions": ["فواتير الخدمات", "العقود الدورية", "الإشعارات"], "notes_ar": "الخدمات المقدمة لغير المقيمين قد تختلف معالجتها — صنّفها"},
    "clinics": {"transactions": ["خدمات المراجعين", "فواتير شركات التأمين", "المستهلكات"], "notes_ar": "بعض الخدمات الصحية قد تختلف معالجتها حسب المستفيد — صنّف المعاملات"},
    "hospitals": {"transactions": ["خدمات المرضى", "مطالبات التأمين", "الأدوية والمستلزمات"], "notes_ar": "تصنيف المعاملات حسب الدافع والخدمة ضروري للمطابقة"},
    "logistics": {"transactions": ["فواتير الشحن", "النقل الدولي", "الإشعارات"], "notes_ar": "النقل الدولي قد يختلف تصنيفه — صنّفه"},
    "other": {"transactions": ["فواتير المبيعات", "المشتريات", "الإشعارات"], "notes_ar": ""},
}


def get_tax_profile(sector):
    key = get_profile(sector)["key"]
    return {"sector": key, "name_ar": PROFILES[key]["name_ar"], **TAX_PROFILES.get(key, TAX_PROFILES["other"]),
            "disclaimer_ar": "ملاحظات تصنيف للمراجعة — ليست فتوى ضريبية"}
