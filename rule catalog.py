"""
NABBAH 2.3 — Rule catalog (localized, user-facing texts for every rule code).

Separates: rule_code · technical_expression · threshold · kpi · title · description ·
impact_formula · action. Templates use {current} {threshold} {reference} {branch} {n} {months}.
Fallback order: requested language -> the other language -> the rule code itself.
"""
KPI = {
    "growth": ("نمو المبيعات", "Sales growth"), "gross_margin": ("هامش الربح", "Profit margin"),
    "expense_ratio": ("نسبة المصروفات", "Expense ratio"), "repeat_rate": ("نسبة العملاء المتكررين", "Repeat-customer rate"),
    "dio": ("أيام المخزون", "Days of inventory"), "net_sales": ("صافي المبيعات", "Net sales"),
    "data_quality": ("جودة البيانات", "Data quality"),
}

R = {
 # ── risks / opportunities ──
 "declining_sales": {"threshold": "-5%", "kpi": "growth",
   "ar": ("تراجع المبيعات", "انخفضت المبيعات {current}% مقارنة بالشهر السابق، وحدّ التنبيه {threshold}.", "الفرق بين مبيعات الشهرين", "حلّل التراجع: هل هو من عدد الفواتير أم من متوسط قيمتها؟"),
   "en": ("Declining sales", "Sales changed {current}% versus last month; the alert limit is {threshold}.", "Difference between the two months' sales", "Split the drop into number of invoices versus average invoice value.")},
 "sales_momentum": {"threshold": "+10%", "kpi": "growth",
   "ar": ("زخم نمو المبيعات", "نمت المبيعات {current}% عن الشهر السابق.", "الفرق بين مبيعات الشهرين", "حدّد الفروع والمنتجات التي قادت النمو ووسّع نطاقها."),
   "en": ("Sales momentum", "Sales grew {current}% versus last month.", "Difference between the two months' sales", "Identify the branches and products driving growth and scale them.")},
 "branch_decline": {"threshold": "-10%", "kpi": "growth",
   "ar": ("تراجع مبيعات فرع", "مبيعات {branch} تغيّرت {current}% عن الشهر السابق (حد التنبيه {threshold}).", "مبيعات الفرع الحالية ناقص السابقة", "راجع تشغيل الفرع مع مديره خلال هذا الأسبوع."),
   "en": ("Branch sales decline", "{branch} sales changed {current}% versus last month (alert limit {threshold}).", "Branch current minus previous sales", "Review the branch's operations with its manager this week.")},
 "negative_margin": {"threshold": "0%", "kpi": "gross_margin",
   "ar": ("الشركة تحقق خسارة", "هامش الربح {current}% — المصروفات أعلى من المبيعات.", "المبيعات ناقص المصروفات", "راجع أكبر ثلاثة بنود مصروفات فوراً."),
   "en": ("The company is making a loss", "Profit margin is {current}% — expenses exceed sales.", "Sales minus expenses", "Review the three largest expense lines immediately.")},
 "negative_margin_branch": {"threshold": "0%", "kpi": "gross_margin",
   "ar": ("فرع يحقق خسارة", "هامش {branch} {current}% — مصروفات الفرع أعلى من مبيعاته.", "مبيعات الفرع ناقص مصروفاته", "ضع خطة تصحيح للفرع خلال أسبوعين."),
   "en": ("Branch making a loss", "{branch} margin is {current}% — its expenses exceed its sales.", "Branch sales minus expenses", "Put a two-week recovery plan in place for the branch.")},
 "branch_underperformance": {"threshold": "-10 pts", "kpi": "gross_margin",
   "ar": ("فرع دون متوسط الهامش", "هامش {branch} {current}% مقابل {reference}% لمتوسط الشركة.", "مبيعات الفرع × فرق الهامش", "قارن تكاليف الفرع بتكاليف أفضل فرع."),
   "en": ("Branch below average margin", "{branch} margin is {current}% versus the company average of {reference}%.", "Branch sales × margin gap", "Benchmark the branch's costs against the best branch.")},
 "branch_margin_gap": {"threshold": "company average", "kpi": "gross_margin",
   "ar": ("فرصة رفع هامش فرع", "لو وصل {branch} إلى متوسط هامش الشركة ({reference}%) بدلاً من {current}%.", "مبيعات الفرع × فرق الهامش", "انقل ممارسات أفضل فرع إلى هذا الفرع."),
   "en": ("Lift a branch's margin", "If {branch} reached the company average margin ({reference}%) instead of {current}%.", "Branch sales × margin gap", "Transfer the best branch's practices to this branch.")},
 "high_expenses": {"threshold": "90%", "kpi": "expense_ratio",
   "ar": ("مصروفات مرتفعة", "المصروفات تمثل {current}% من المبيعات (الحد {threshold}).", "المبيعات × (نسبة المصروفات − 90%)", "راجع العقود والرواتب والإيجارات."),
   "en": ("High expenses", "Expenses are {current}% of sales (limit {threshold}).", "Sales × (expense ratio − 90%)", "Review contracts, payroll and rent.")},
 "expense_growth": {"threshold": "+10 pts", "kpi": "expense_ratio",
   "ar": ("المصروفات تنمو أسرع من المبيعات", "المصروفات نمت {current}% بينما المبيعات {reference}%.", "المصروفات الفعلية ناقص مصروفات تنمو بنسبة المبيعات", "حدّد البند الذي ارتفع أكثر من غيره."),
   "en": ("Expenses growing faster than sales", "Expenses grew {current}% while sales grew {reference}%.", "Actual expenses minus expenses grown at the sales rate", "Find the line item that rose the most.")},
 "margin_below_target": {"threshold": "target margin", "kpi": "gross_margin",
   "ar": ("الهامش أقل من المستهدف", "الهامش {current}% والمستهدف {reference}%.", "المبيعات × فرق الهامش", "حلّل فجوة الهامش حسب كل فرع."),
   "en": ("Margin below target", "Margin is {current}% against a target of {reference}%.", "Sales × margin gap", "Break the margin gap down by branch.")},
 "missing_data": {"threshold": "0", "kpi": "data_quality",
   "ar": ("بيانات ناقصة", "توجد بيانات ناقصة قد تغيّر نتائج التحليل.", "—", "أكمل البيانات الناقصة قبل اتخاذ قرارات مالية."),
   "en": ("Missing data", "Some data is missing and may change the analysis.", "—", "Complete the missing data before making financial decisions.")},
 "inconsistent_data": {"threshold": "0", "kpi": "data_quality",
   "ar": ("بيانات غير متسقة", "بعض السجلات تحتوي أرقاماً لا تتطابق مع بعضها.", "—", "راجع السجلات المتعارضة وصحّحها."),
   "en": ("Inconsistent data", "Some records contain figures that do not agree with each other.", "—", "Review and correct the conflicting records.")},
 "high_inventory": {"threshold": "60 days", "kpi": "dio",
   "ar": ("مخزون مرتفع", "المخزون الحالي يكفي {current} يوماً (الحد {threshold}).", "قيمة المخزون ناقص تكلفة 60 يوماً", "خفّض طلبات الأصناف بطيئة الحركة."),
   "en": ("High inventory", "Current inventory covers {current} days (limit {threshold}).", "Inventory value minus 60 days of cost", "Reduce orders for slow-moving items.")},
 "low_repeat": {"threshold": "20%", "kpi": "repeat_rate",
   "ar": ("تكرار شراء منخفض", "نسبة العملاء المتكررين {current}% (الحد {threshold}).", "—", "جرّب برنامج ولاء بسيطاً لمدة شهر."),
   "en": ("Low repeat purchases", "Repeat-customer rate is {current}% (limit {threshold}).", "—", "Trial a simple loyalty programme for one month.")},
 "branch_below_target": {"threshold": "70%", "kpi": "net_sales",
   "ar": ("فرع دون الهدف", "{branch} حقق مبيعات {current} من هدف {reference}.", "مبيعات الفرع ناقص الهدف", "راجع هدف الفرع وخطة المبيعات."),
   "en": ("Branch below target", "{branch} achieved sales of {current} against a target of {reference}.", "Branch sales minus target", "Review the branch target and sales plan.")},
 "target_overachievement": {"threshold": "110%", "kpi": "net_sales",
   "ar": ("فرع يتجاوز هدفه", "{branch} حقق {current} مقابل هدف {reference}.", "مبيعات الفرع ناقص الهدف", "وثّق ممارسات الفرع وعمّمها على البقية."),
   "en": ("Branch beating its target", "{branch} achieved {current} against a target of {reference}.", "Branch sales minus target", "Document the branch's practices and replicate them.")},
 # ── data quality ──
 "missing_values": {"ar": ("سجلات بلا مبيعات أو مصروفات", "{n} سجل في الفترة الحالية لا يحتوي مبيعات أو مصروفات.", "", "أدخل المبيعات والمصروفات لهذه الفروع."),
                    "en": ("Records without sales or expenses", "{n} current-period record(s) have no sales or expenses.", "", "Enter sales and expenses for these branches.")},
 "duplicate_entries": {"ar": ("إدخالات مكررة", "{n} إدخال مكرر لنفس الفرع والشهر، ويُعتمد الأحدث.", "", "احذف الإدخالات القديمة أو تأكد أن الأحدث صحيح."),
                       "en": ("Duplicate entries", "{n} duplicate entries for the same branch and month; the latest is used.", "", "Remove older entries or confirm the latest is correct.")},
 "invalid_values": {"ar": ("قيم سالبة غير صحيحة", "{n} سجل يحتوي قيماً سالبة في المبيعات أو المصروفات أو الأعداد.", "", "صحّح القيم السالبة."),
                    "en": ("Invalid negative values", "{n} record(s) contain negative sales, expenses or counts.", "", "Correct the negative values.")},
 "zero_customers_high_sales": {"ar": ("مبيعات بلا عدد عملاء", "{n} سجل فيه مبيعات لكن عدد العملاء صفر.", "", "أدخل عدد العملاء لتفعيل تحليل السلة والولاء."),
                               "en": ("Sales without customer counts", "{n} record(s) have sales but zero customers.", "", "Enter customer counts to enable basket and loyalty analysis.")},
 "incorrect_margin": {"ar": ("هامش مخزّن غير مطابق", "{n} سجل هامشه المحفوظ لا يطابق المبيعات والمصروفات.", "", "أعد حفظ هذه السجلات لإعادة احتساب الهامش."),
                      "en": ("Stored margin mismatch", "{n} record(s) have a stored margin that does not match sales and expenses.", "", "Re-save these records to recalculate the margin.")},
 "missing_branch_info": {"ar": ("سجلات لفروع غير نشطة", "{n} سجل مرتبط بفرع غير نشط أو غير موجود، ومستبعد من التحليل.", "", "أعد تفعيل الفرع أو انقل السجلات لفرع صحيح."),
                         "en": ("Records for inactive branches", "{n} record(s) are linked to an inactive or unknown branch and are excluded.", "", "Reactivate the branch or reassign the records.")},
 "inconsistent_totals": {"ar": ("إجماليات غير متسقة", "{n} سجل لا يتطابق فيه الربح أو الإيداع مع المبيعات.", "", "طابق الإيداعات مع المبيعات وأعد حفظ الربح."),
                         "en": ("Inconsistent totals", "{n} record(s) where profit or deposits do not match sales.", "", "Reconcile deposits with sales and re-save profit.")},
 "valid_period": {"ar": ("فترة غير مقروءة", "بعض السجلات لا تحمل شهراً صالحاً واستُبعدت.", "", "اكتب الفترة بصيغة سنة-شهر مثل 2026-09."),
                  "en": ("Unreadable period", "Some records have no valid month and were excluded.", "", "Use the year-month format, e.g. 2026-09.")},
 "comparison_period_data": {"ar": ("لا توجد بيانات للشهر السابق", "لا يمكن حساب النمو أو المقارنة بدون بيانات الشهر السابق.", "", "أدخل بيانات الشهر السابق للفروع."),
                            "en": ("No data for the previous month", "Growth and comparisons need the previous month's data.", "", "Enter the previous month's branch data.")},
 "current_period_data": {"ar": ("لا توجد بيانات للفترة الحالية", "لا توجد سجلات للشهر المختار.", "", "أدخل بيانات الفروع لهذا الشهر."),
                         "en": ("No data for the current period", "There are no records for the selected month.", "", "Enter branch data for this month.")},
 # ── forecast warnings ──
 "outlier_months": {"ar": ("أشهر غير اعتيادية", "تغيّر غير معتاد في الأشهر: {months}. لم تُحذف، والوسيط يحدّ من أثرها.", "", ""),
                    "en": ("Unusual months", "Unusual change in: {months}. Kept; the median limits their effect.", "", "")},
 "history_gap": {"ar": ("فجوة في السجل", "لم تُستخدم الأشهر السابقة لفجوة زمنية: {months}.", "", ""),
                 "en": ("Gap in history", "Months before a gap were not used: {months}.", "", "")},
 "negative_values": {"ar": ("قيم سالبة", "السلسلة تحتوي قيماً سالبة.", "", ""), "en": ("Negative values", "The series contains negative values.", "", "")},
 "insufficient_data": {"ar": ("بيانات غير كافية للتوقع", "يلزم 3 أشهر متتالية على الأقل.", "", "أدخل بيانات الأشهر الناقصة."),
                       "en": ("Not enough data to forecast", "At least 3 consecutive months are required.", "", "Enter the missing months.")},
}


def _fmt(tpl, **vals):
    try:
        return tpl.format(**{k: ("—" if v is None else v) for k, v in vals.items()})
    except (KeyError, IndexError):
        return tpl


def localize(rule_code, lang="ar", **vals):
    """Returns {title, description, impact_formula, action, kpi} for one language, with fallback."""
    r = R.get(rule_code)
    other = "en" if lang == "ar" else "ar"
    if not r:
        return {"title": rule_code, "description": "", "impact_formula": "", "action": "", "kpi": "", "fallback": True}
    t = r.get(lang) or r.get(other)
    kpi = KPI.get(r.get("kpi", ""), ("", ""))
    return {"title": t[0], "description": _fmt(t[1], threshold=r.get("threshold"), **vals),
            "impact_formula": t[2], "action": t[3], "kpi": kpi[0 if lang == "ar" else 1] or kpi[0],
            "fallback": lang not in r}


def texts(rule_code, **vals):
    """Both languages at once — the API returns this, the UI picks by locale."""
    return {"ar": localize(rule_code, "ar", **vals), "en": localize(rule_code, "en", **vals)}


def threshold(rule_code):
    return (R.get(rule_code) or {}).get("threshold")
