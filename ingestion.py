"""
NABBAH 2.4 — Ingestion foundation (pure: no DB, no AI, no file I/O).
Maps Arabic/English columns to canonical fields, parses numbers/dates, detects
duplicates and missing mandatory fields, validates relationships, and scores quality
through the EXISTING phase21 Data Trust layer (no second quality system).
Nothing is ever imported silently: rows are marked valid or rejected with reasons.
"""
import os, re, sys
from datetime import date, datetime, timedelta
from decimal import Decimal
_H = os.path.dirname(os.path.abspath(__file__))
for _d in ("..", "../phase21", "../phase22"):
    sys.path.insert(0, os.path.join(_H, _d))
from canonical_model import DATETIME_FIELDS
from canonical_model import (ENTITIES, DATASET_TYPES, NUMERIC, DATE_FIELDS, PERIOD_FIELDS,
                             DUP_KEYS, DIRECTIONS, required_fields, all_fields)
from period_model import parse_date, period_key
from nabbah_finance import to_decimal
from nabbah_trust import (check_completeness, check_validity, check_duplicates,
                          check_consistency, run_all_checks)

INGEST_VERSION = "1.0"

# مرادفات الأعمدة: عربية وإنجليزية -> الحقل المرجعي
SYNONYMS = {
 "date": ["التاريخ", "تاريخ", "date", "day", "trx date", "invoice date", "تاريخ الفاتورة", "تاريخ الطلب", "تاريخ أمر الشراء", "تاريخ الشراء", "order date", "po date", "purchase date"],
 "period": ["الفترة", "الشهر", "period", "month"],
 "branch_id": ["الفرع", "اسم الفرع", "branch", "branch name", "location", "الموقع"],
 "reference": ["رقم الفاتورة", "الفاتورة", "المرجع", "invoice", "invoice no", "reference", "ref", "order", "رقم الطلب", "po", "رقم أمر الشراء", "رقم أمر الشراء", "أمر الشراء", "po", "po number", "po no"],
 "channel": ["القناة", "channel", "قناة البيع", "sales channel"],
 "product_sku": ["رمز المنتج", "الصنف", "sku", "product", "product code", "item", "المنتج", "كود الصنف"],
 "category": ["التصنيف", "الفئة", "category", "group", "المجموعة", "بند المصروف", "نوع المصروف", "expense category", "account name", "اسم الحساب المحاسبي"],
 "quantity": ["الكمية", "العدد", "qty", "quantity", "units"],
 "gross_sales": ["المبيعات", "إجمالي المبيعات", "المبيعات الإجمالية", "sales", "gross sales", "amount", "المبلغ", "القيمة", "total sales"],
 "discounts": ["الخصم", "الخصومات", "discount", "discounts"],
 "returns": ["المرتجعات", "المرتجع", "returns", "refund"],
 "net_sales": ["صافي المبيعات", "net sales", "net"],
 "vat": ["الضريبة", "ضريبة القيمة المضافة", "vat", "tax"],
 "payment_method": ["طريقة الدفع", "الدفع", "payment", "payment method"],
 "email": ["البريد", "البريد الإلكتروني", "البريد الالكتروني", "الإيميل", "الايميل", "email", "e-mail", "mail", "email address"],
 "phone": ["الجوال", "رقم الجوال", "الهاتف", "رقم الهاتف", "الموبايل", "phone", "mobile", "tel", "telephone", "phone number"],
 "customer_code": ["رقم العميل", "كود العميل", "customer code", "customer id", "client id", "customer no"],
 "customer_name": ["العميل", "اسم العميل", "customer", "customer name", "client", "client name"],
 "supplier_code": ["رقم المورد", "كود المورد", "supplier code", "vendor code", "vendor id"],
 "contact_person": ["الشخص المسؤول", "جهة الاتصال", "اسم المسؤول", "contact", "contact person"],
 "city": ["المدينة", "city"],
 "segment": ["الشريحة", "القطاع", "segment"],
 "customer_type": ["نوع العميل", "فئة العميل", "customer type", "client type"],
 "tax_number": ["الرقم الضريبي", "رقم ضريبي", "رقم التسجيل الضريبي", "vat number", "vat no", "tax number", "tax id", "trn"],
 "credit_limit": ["حد الائتمان", "الحد الائتماني", "credit limit"],
 "notes": ["ملاحظات", "ملاحظة", "notes", "note", "remarks", "comment", "comments"],
 "inflow": ["الإيداعات", "إجمالي الإيداعات", "الايداعات", "اجمالي الايداعات", "إيداع", "ايداع", "deposits", "deposit",
            "total deposits", "credit", "دائن", "المقبوضات", "التحصيلات", "cash in", "inflow"],
 "outflow": ["السحوبات", "إجمالي السحوبات", "اجمالي السحوبات", "سحب", "withdrawals", "withdrawal", "total withdrawals",
             "debit", "مدين", "المدفوعات", "cash out", "outflow"],
 "expected_date": ["تاريخ التسليم المتوقع", "التسليم المتوقع", "موعد التسليم", "تاريخ الاستحقاق", "expected date",
                   "expected delivery", "due date", "promised date"],
 "received_date": ["تاريخ الاستلام", "تاريخ التسليم الفعلي", "تاريخ الاستلام الفعلي", "received date", "delivery date",
                   "receipt date", "actual delivery"],
 "received_qty": ["الكمية المستلمة", "المستلم", "received qty", "qty received", "received quantity"],
 "rejected_qty": ["الكمية المرفوضة", "المرفوض", "المعيب", "rejected qty", "rejected", "defective", "rejected quantity"],
 "employment_type": ["نوع التوظيف", "نوع العقد", "دوام", "employment type", "contract type"],
 "manager": ["المدير المباشر", "المدير", "المسؤول المباشر", "manager", "reports to", "line manager"],
 "termination_type": ["نوع المغادرة", "سبب المغادرة", "نوع انتهاء الخدمة", "termination type", "exit type", "leave reason"],
 "basic_salary": ["الراتب الأساسي", "basic salary", "base salary"],
 "allowances": ["البدلات", "allowances"],
 "benefits": ["المزايا", "التأمين والمزايا", "benefits"],
 "performance_rating": ["تقييم الأداء", "الأداء", "التقييم", "performance", "performance rating", "rating"],
 "last_promotion_date": ["تاريخ آخر ترقية", "آخر ترقية", "last promotion", "last promotion date"],
 "training_hours": ["ساعات التدريب", "التدريب", "training hours"],
 "absence_days": ["أيام الغياب", "الغياب", "absence days", "absences"],
 "overtime_hours": ["ساعات العمل الإضافي", "العمل الإضافي", "overtime", "overtime hours"],
 "critical_role": ["وظيفة حرجة", "دور حرج", "critical role", "key role"],
 "successors": ["عدد البدلاء", "البدلاء", "المرشحون للخلافة", "successors", "succession candidates"],
 "title": ["المسمى الوظيفي", "الوظيفة الشاغرة", "الوظيفة", "job title", "position", "opening",
           "المشكلة", "العطل", "وصف المشكلة", "البلاغ", "issue", "fault", "problem"],
 "opened_date": ["تاريخ الفتح", "تاريخ فتح الوظيفة", "تاريخ الإعلان", "opened date", "open date", "posted date"],
 "filled_date": ["تاريخ الشغل", "تاريخ التعيين الفعلي", "تاريخ الإغلاق", "filled date", "hire date", "closed date"],
 "applicants": ["المتقدمون", "عدد المتقدمين", "applicants"],
 "interviews": ["المقابلات", "عدد المقابلات", "interviews"],
 "offers": ["العروض", "عروض العمل", "offers"],
 "hires": ["المعيّنون", "عدد المعينين", "التعيينات", "hires", "hired"],
 "hiring_cost": ["تكلفة التوظيف", "hiring cost", "recruitment cost", "cost per hire"],
 "invoice_number": ["رقم الفاتورة الضريبية", "رقم الفاتورة", "invoice number", "invoice no", "invoice id"],
 "issue_date": ["تاريخ الإصدار", "تاريخ إصدار الفاتورة", "issue date", "invoice issue date"],
 "invoice_type": ["نوع الفاتورة", "نوع المستند", "invoice type", "document type"],
 "buyer_name": ["اسم المشتري", "المشتري", "buyer", "buyer name"],
 "buyer_vat": ["الرقم الضريبي للمشتري", "ضريبي المشتري", "buyer vat", "buyer vat number", "customer vat"],
 "taxable_amount": ["المبلغ الخاضع", "المبلغ قبل الضريبة", "المبلغ الخاضع للضريبة", "taxable amount", "amount before vat", "net amount"],
 "vat_amount": ["مبلغ الضريبة", "ضريبة القيمة المضافة", "الضريبة", "vat amount", "vat", "tax amount"],
 "total_amount": ["الإجمالي شامل الضريبة", "المبلغ الإجمالي", "الإجمالي مع الضريبة", "total amount", "total incl vat", "gross total"],
 "currency": ["العملة", "currency"],
 "original_invoice": ["الفاتورة الأصلية", "مرجع الفاتورة الأصلية", "original invoice", "reference invoice"],
 "zatca_status": ["حالة زاتكا", "حالة الهيئة", "حالة الإرسال", "zatca status", "clearance status", "reporting status"],
 "description": ["البيان", "الوصف", "تفاصيل المصروف", "description", "memo", "narration"],
 "vendor": ["الجهة", "المستفيد", "المورد/الجهة", "vendor", "payee"],
 "service": ["الخدمة", "نوع الخدمة", "نوع الطلب", "قناة الطلب", "service", "order type", "service type"],
 "created_time": ["وقت الطلب", "وقت الإنشاء", "وقت الاستلام من العميل", "created time", "order time", "created at"],
 "ready_time": ["وقت الجاهزية", "وقت التجهيز", "جاهز", "ready time", "prepared at"],
 "delivered_time": ["وقت التسليم", "وقت التسليم الفعلي", "delivered time", "delivered at", "completed at"],
 "due_time": ["الموعد المستهدف", "وقت التسليم المستهدف", "موعد SLA", "due time", "promised time", "sla deadline", "target time"],
 "items": ["عدد الأصناف", "الأصناف", "items", "item count"],
 "accurate": ["طلب صحيح", "الدقة", "صحيح", "accurate", "correct order"],
 "defect_type": ["نوع الخطأ", "نوع العيب", "الخطأ", "defect", "defect type", "error type"],
 "rework": ["إعادة عمل", "إعادة تجهيز", "rework", "redo"],
 "stage": ["المرحلة", "مرحلة العملية", "stage", "process step", "step"],
 "start_time": ["وقت البداية", "بداية المرحلة", "start time", "started at"],
 "end_time": ["وقت النهاية", "نهاية المرحلة", "end time", "ended at"],
 "opened_time": ["وقت الفتح", "تاريخ المشكلة", "وقت البلاغ", "تاريخ البلاغ", "opened at", "reported at", "issue date"],
 "resolved_time": ["وقت الحل", "تاريخ الحل", "resolved at", "resolved time", "closed at"],
 "severity": ["الخطورة", "الأولوية", "severity", "priority"],
 "owner": ["المسؤول", "owner", "assignee", "assigned to"],
 "root_cause": ["السبب الجذري", "السبب", "root cause", "cause"],
 "impact": ["الأثر", "impact"],
 "sla_hours": ["مدة SLA بالساعات", "SLA (ساعات)", "sla hours"],
 "promotion": ["الحملة", "العرض الترويجي", "الحملة الترويجية", "promotion", "promo", "campaign"],
 "supplier_name": ["المورد", "اسم المورد", "supplier", "vendor"],
 "unit_cost": ["سعر الوحدة", "تكلفة الوحدة", "unit cost", "unit price", "price"],
 "total_cost": ["التكلفة", "إجمالي التكلفة", "قيمة الشراء", "total cost", "total", "cost"],
 "status": ["الحالة", "status"],
 "opening_qty": ["كمية أول المدة", "الرصيد الافتتاحي", "opening qty", "opening quantity"],
 "opening_value": ["قيمة أول المدة", "opening value"],
 "purchases_qty": ["المشتريات", "الوارد", "purchases", "received"],
 "sold_qty": ["المباع", "المنصرف", "sold", "issued", "consumption"],
 "adjustments_qty": ["التسويات", "الهدر", "adjustments", "waste"],
 "closing_qty": ["كمية آخر المدة", "الرصيد", "closing qty", "closing quantity", "balance"],
 "closing_value": ["قيمة آخر المدة", "قيمة المخزون", "closing value", "inventory value"],
 "amount": ["المبلغ", "القيمة", "amount", "value"],
 "direction": ["الاتجاه", "نوع الحركة", "direction", "in/out", "type"],
 "movement_type": ["التصنيف النقدي", "الوصف", "البيان", "التفاصيل", "movement type", "cash type", "description", "narration", "details"],
 "account": ["الحساب", "اسم الحساب", "الحساب البنكي", "البنك", "الصندوق", "account", "bank account", "cash account"],
 "counterparty": ["الطرف", "الطرف المقابل", "المستفيد", "الجهة", "counterparty", "beneficiary", "payee", "payer"],
 "balance": ["الرصيد", "الرصيد بعد الحركة", "الرصيد الختامي", "balance", "running balance", "closing balance"],
 "invoice_date": ["تاريخ الفاتورة", "تاريخ الذمة", "invoice date", "bill date"],
 "due_date": ["تاريخ الاستحقاق", "الاستحقاق", "due date", "maturity date"],
 "paid_amount": ["المبلغ المحصل", "المحصل", "المسدد", "paid", "paid amount", "collected", "amount paid"],
 "paid_date": ["تاريخ التحصيل", "تاريخ السداد", "paid date", "payment date", "collection date"],
 "source": ["المصدر", "source"],
 "employee_code": ["رقم الموظف", "الرقم الوظيفي", "employee code", "employee id", "staff id"],
 "name": ["الاسم", "اسم", "name", "full name", "اسم المنتج", "اسم المورد", "اسم القسم", "اسم الموظف",
          "اسم العميل", "العميل", "customer name", "client name", "المورد", "supplier name", "vendor"],
 "role": ["الوظيفة", "المسمى", "role", "position", "job title"],
 "employment_status": ["حالة التوظيف", "الحالة الوظيفية", "employment status"],
 "hire_date": ["تاريخ التعيين", "hire date", "join date"],
 "termination_date": ["تاريخ انتهاء الخدمة", "termination date", "exit date"],
 "monthly_cost": ["الراتب", "التكلفة الشهرية", "salary", "monthly cost", "الأجر"],
 "department_id": ["القسم", "الإدارة", "department"],
 "sku": ["رمز المنتج", "sku", "code", "الرمز", "كود"],
 "unit": ["الوحدة", "unit", "uom"],
 "cost": ["التكلفة", "cost", "سعر التكلفة"],
 "selling_price": ["سعر البيع", "selling price", "price"],
 "payment_terms": ["شروط الدفع", "payment terms", "terms"],
 "active": ["نشط", "active", "الحالة"],
 "code": ["الرمز", "code"],
}


def _norm(s):
    s = str(s or "").strip().lower()
    s = re.sub(r"[\u064b-\u0652\u200f\u200e]", "", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"[\s_\-/\\.:()]+", " ", s).strip()


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_MONTHS = {"يناير": 1, "كانون الثاني": 1, "فبراير": 2, "شباط": 2, "مارس": 3, "اذار": 3, "ابريل": 4, "نيسان": 4,
           "مايو": 5, "ايار": 5, "يونيو": 6, "يونيه": 6, "حزيران": 6, "يوليو": 7, "يوليه": 7, "تموز": 7,
           "اغسطس": 8, "اب": 8, "سبتمبر": 9, "ايلول": 9, "اكتوبر": 10, "تشرين الاول": 10, "نوفمبر": 11,
           "تشرين الثاني": 11, "ديسمبر": 12, "كانون الاول": 12,
           "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
           "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
           "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}


_HIJRI_MONTHS = {"محرم": 1, "صفر": 2, "ربيع الاول": 3, "ربيع اول": 3, "ربيع الاخر": 4, "ربيع الثاني": 4, "ربيع ثاني": 4,
                 "جمادي الاولي": 5, "جمادي الاول": 5, "جمادي الاخره": 6, "جمادي الثانيه": 6, "جمادي الاخر": 6,
                 "رجب": 7, "شعبان": 8, "رمضان": 9, "شوال": 10, "ذو القعده": 11, "ذي القعده": 11,
                 "ذو الحجه": 12, "ذي الحجه": 12}
_EXCEL_EPOCH = date(1899, 12, 30)


def _hijri_to_gregorian(y, m, d=15):
    """تحويل هجري (التقويم الجدولي) → ميلادي. دقة ±يوم إلى يومين — كافية لتحديد الشهر عند منتصفه."""
    import math
    jd = d + math.ceil(29.5 * (m - 1)) + (y - 1) * 354 + (3 + 11 * y) // 30 + 1948440 - 1
    a = jd + 32044
    b = (4 * a + 3) // 146097
    c = a - 146097 * b // 4
    dd = (4 * c + 3) // 1461
    e = c - 1461 * dd // 4
    mm = (5 * e + 2) // 153
    return date(100 * b + dd - 4800 + mm // 10, mm + 3 - 12 * (mm // 10), e - (153 * mm + 2) // 5 + 1)


def parse_any_date(raw):
    """تاريخ من أي صيغة شائعة في ملفات الشركات: كائن Excel، رقم Excel التسلسلي، نص بصيغ متعددة. غير ذلك None."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return _EXCEL_EPOCH + timedelta(days=int(raw)) if 20000 <= raw <= 80000 else None
    s = str(raw).strip().translate(_AR_DIGITS)
    if not s:
        return None
    if re.fullmatch(r"\d{5}(\.0+)?", s) and 20000 <= float(s) <= 80000:
        return _EXCEL_EPOCH + timedelta(days=int(float(s)))
    s = re.sub(r"\s*(م|مـ|ميلادي)$", "", s)
    s = re.sub(r"[T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?$", "", s)
    d = parse_date(s)
    if isinstance(d, datetime):
        d = d.date()
    if d:
        return d
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)          # MM/DD/YYYY (نظام أمريكي)
    if m and int(m.group(2)) > 12 and 1 <= int(m.group(1)) <= 12:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    n = _norm(s).replace(",", " ")
    y = re.search(r"\b(\d{4})\b", n)
    dd = re.search(r"\b(\d{1,2})\b", re.sub(r"\b\d{4}\b", "", n))
    mon = _month_word(n)
    if y and dd and mon:
        try:
            return date(int(y.group(1)), mon, int(dd.group(1)))
        except ValueError:
            return None
    return None


def _month_word(n):
    for name in sorted(_MONTHS, key=len, reverse=True):
        k = _norm(name)
        if re.search(r"(^|[\s\-/])" + re.escape(k) + r"($|[\s\-/,])", n):
            return _MONTHS[name]
    return None


def parse_month(raw, year_hint=None):
    """الشهر من أي صيغة: «2026-01» «01/2026» «يناير 2026» «Jan-24» «2024M01» تاريخ كامل، كائن أو رقم Excel،
    «محرم 1446» (يُحوَّل للميلادي)، أو اسم شهر وحده مع سنة معروفة من عنوان الملف أو اسمه (year_hint).
    لا تخمين: اسم شهر بلا سنة وبلا year_hint → None."""
    if raw is None:
        return None
    if isinstance(raw, (datetime, date)) or (isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 20000):
        d = parse_any_date(raw)
        return f"{d.year:04d}-{d.month:02d}" if d else None
    s = str(raw).strip().translate(_AR_DIGITS)
    s = re.sub(r"\s*(م|مـ|ميلادي)$", "", s)
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})\s*[-/.mM]\s*(\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12 and int(m.group(1)) < 1600:
        g = _hijri_to_gregorian(int(m.group(1)), int(m.group(2)))
        return f"{g.year:04d}-{g.month:02d}"
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{1,2})\s*[-/.]\s*(\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    d = parse_any_date(s)
    if d:
        return f"{d.year:04d}-{d.month:02d}"
    n = _norm(s).replace(",", " ").replace("هـ", " هـ")
    for name in sorted(_HIJRI_MONTHS, key=len, reverse=True):          # هجري
        if _norm(name) in n:
            y = re.search(r"(1[34]\d{2})", n)
            if y:
                g = _hijri_to_gregorian(int(y.group(1)), _HIJRI_MONTHS[name])
                return f"{g.year:04d}-{g.month:02d}"
            return None
    mon = _month_word(n)
    if mon:
        y4 = re.search(r"\b(\d{4})\b", n)
        if y4:
            return f"{int(y4.group(1)):04d}-{mon:02d}"
        y2 = re.search(r"\b(\d{2})\b", n)                              # Jan-24 / يناير-24
        if y2:
            return f"{2000 + int(y2.group(1)):04d}-{mon:02d}"
        if year_hint:
            return f"{int(year_hint):04d}-{mon:02d}"
        return None
    m = re.fullmatch(r"(\d{1,2})", s)                                    # رقم الشهر وحده + سنة معروفة
    if m and year_hint and 1 <= int(m.group(1)) <= 12:
        return f"{int(year_hint):04d}-{int(m.group(1)):02d}"
    return None


def year_hint_from(*texts):
    """سنة من عنوان الملف/اسمه («كشف_بنكي_2024.xlsx» → 2024). غير ذلك None."""
    for t in texts:
        m = re.search(r"(?<!\d)(20\d{2})(?!\d)", str(t or "").translate(_AR_DIGITS))
        if m:
            return int(m.group(1))
    return None


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def map_columns(headers, dataset_type):
    """يطابق أعمدة الملف مع حقول النوع. يُرجع التطابق والمقترحات والأعمدة غير المطابقة."""
    fields = all_fields(dataset_type)
    lookup = {}
    for field in fields:
        for syn in SYNONYMS.get(field, []) + [field]:
            lookup.setdefault(_norm(syn), field)
    mapping, unmapped = {}, []
    for i, h in enumerate(headers):
        n = _norm(h)
        field = lookup.get(n)
        if not field:
            for key, f in lookup.items():
                if f in fields and (n.startswith(key) or key.startswith(n)) and len(n) > 2:
                    field = f
                    break
        if field and field not in mapping.values():
            mapping[i] = field
        else:
            unmapped.append({"index": i, "header": h})
    missing = [f for f in required_fields(dataset_type) if f not in mapping.values()]
    if dataset_type == "purchase" and {"quantity", "unit_cost"} <= set(mapping.values()):
        missing = [f for f in missing if f != "total_cost"]      # الإجمالي يُشتق = الكمية × سعر الوحدة
    if dataset_type == "cash_movement":
        vals = set(mapping.values())
        if vals & {"inflow", "outflow"}:          # ملخص: المبلغ والاتجاه يُشتقّان
            missing = [f for f in missing if f not in ("amount", "direction")]
        if "period" in vals:                       # الشهر يكفي كتاريخ (أول يوم في الشهر)
            missing = [f for f in missing if f != "date"]
    return {"mapping": mapping, "unmapped": unmapped, "missing_required": missing,
            "fields": fields, "required": required_fields(dataset_type)}


def parse_datetime(raw):
    """تاريخ+وقت → 'YYYY-MM-DDTHH:MM' · وقت فقط → 'THH:MM' (يُدمج مع تاريخ الصف لاحقاً)."""
    import datetime as _dt
    if isinstance(raw, _dt.datetime):
        return raw.strftime("%Y-%m-%dT%H:%M"), None
    if isinstance(raw, _dt.time):
        return raw.strftime("T%H:%M"), None
    if isinstance(raw, _dt.date):
        return raw.strftime("%Y-%m-%dT00:00"), None
    if isinstance(raw, float) and 0 <= raw < 1:                      # كسر يوم Excel
        mins = round(raw * 1440)
        return f"T{mins // 60:02d}:{mins % 60:02d}", None
    t = str(raw).translate(_AR_DIGITS).strip().replace("ص", "AM").replace("م", "PM")
    m = re.match(r"^(.*?)[ T]?(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?$", t, re.I)
    if m:
        hh, mm, ap = int(m.group(2)), int(m.group(3)), (m.group(4) or "").upper()
        if ap == "PM" and hh < 12:
            hh += 12
        if ap == "AM" and hh == 12:
            hh = 0
        if hh > 23 or mm > 59:
            return None, f"وقت غير مفهوم «{str(raw)[:25]}»"
        dpart = m.group(1).strip()
        if not dpart:
            return f"T{hh:02d}:{mm:02d}", None
        d = parse_any_date(dpart)
        return (f"{d.isoformat()}T{hh:02d}:{mm:02d}", None) if d else (None, f"تاريخ غير مفهوم «{str(raw)[:25]}»")
    d = parse_any_date(raw)
    return (f"{d.isoformat()}T00:00", None) if d else (None, f"تاريخ/وقت غير مفهوم «{str(raw)[:25]}»")


def parse_value(field, raw, year_hint=None):
    """يحوّل القيمة حسب نوع الحقل. يُرجع (value, error) — لا تخمين صامت."""
    if raw is None or str(raw).strip() == "":
        return None, None
    if field in NUMERIC:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw), None
        t = str(raw).translate(_AR_DIGITS).replace("٫", ".").replace("٬", ",")
        t = re.sub(r"(ر\.?\s?س\.?|ريال|SAR|SR|﷼)", "", t, flags=re.I).strip()
        neg = t.startswith("(") and t.endswith(")")
        t = t.strip("()").replace(" ", "")
        v = to_decimal(t)
        if v is not None and neg:
            v = -v
        return (float(v), None) if v is not None else (None, "قيمة غير رقمية")
    if field in DATETIME_FIELDS:
        return parse_datetime(raw)
    if field in ("accurate", "rework"):
        field = "active"
    if field in DATE_FIELDS:
        d = parse_any_date(raw)
        return (d.isoformat(), None) if d else (None, f"تاريخ غير مفهوم «{str(raw)[:25]}»")
    if field in PERIOD_FIELDS:
        k = parse_month(raw, year_hint)
        if k:
            return k, None
        if _month_word(_norm(str(raw))) and not year_hint:
            return None, f"«{str(raw)[:20]}» شهر بلا سنة — اكتب السنة في العمود أو في عنوان الملف"
        return None, f"شهر غير مفهوم «{str(raw)[:25]}»"
    if field == "critical_role":
        field = "active"
    if field == "active":
        s = _norm(raw)
        if s in ("1", "نعم", "yes", "y", "true", "active", "نشط", "فعال", "مفعل", "ساري"):
            return 1, None
        if s in ("0", "لا", "no", "n", "false", "inactive", "غير نشط", "موقوف", "متوقف", "غير فعال"):
            return 0, None
        return None, "قيمة غير مفهومة (نشط/غير نشط)"
    if field == "email":
        e = str(raw).strip().lower()
        return (e, None) if _EMAIL_RE.match(e) else (None, "بريد إلكتروني غير صالح")
    if field == "phone":
        p = re.sub(r"[^\d+]", "", str(raw).translate(_AR_DIGITS))
        return (p, None) if len(p.lstrip("+")) >= 7 else (None, "رقم جوال غير صالح")
    if field == "direction":
        s = _norm(raw)
        for key, val in (("in", "in"), ("داخل", "in"), ("وارد", "in"), ("قبض", "in"),
                         ("out", "out"), ("خارج", "out"), ("صادر", "out"), ("صرف", "out")):
            if key in s:
                return val, None
        return None, "اتجاه غير معروف (داخل/خارج)"
    return str(raw).strip(), None


_TOTAL_WORDS = {_n for _n in ("الاجمالي", "اجمالي", "المجموع", "المجموع الكلي", "الاجمالي العام", "الاجمالي الكلي",
                              "total", "grand total", "totals", "sum")}
SOFT_FIELDS = {"email", "phone"}   # خطأ في بيانات التواصل لا يرفض الصف: يُحفظ الأصل في extra مع تنبيه


def validate_rows(rows, mapping, dataset_type, *, branch_names=None, existing_keys=None,
                  headers=None, default_branch_id=None, year_hint=None):
    """يحوّل الصفوف ويتحقق منها. لا يستورد شيئاً: يُرجع الصالح والمرفوض مع الأسباب.
    الأعمدة غير المطابقة لا تُهمل: تُحفظ كما هي في extra لكل صف."""
    req = required_fields(dataset_type)
    dup_key = DUP_KEYS.get(dataset_type, tuple(req))
    branch_names = {(_norm(k)): v for k, v in (branch_names or {}).items()}
    seen, valid, rejected = dict(existing_keys or {}), [], []
    extra_cols = [(i, str(h).strip()) for i, h in enumerate(headers or [])
                  if i not in mapping and str(h or "").strip() not in ("", "#", "م", "no", "No")]
    summary_cash = dataset_type == "cash_movement" and bool(set(mapping.values()) & {"inflow", "outflow"})
    warnings = []
    skipped_totals = []
    for n, row in enumerate(rows, start=2):  # 2 = أول صف بعد العناوين
        if any(_norm(c) in _TOTAL_WORDS for c in row if isinstance(c, str)):
            skipped_totals.append(n)          # صف «الإجمالي» في آخر الكشف: ليس بيانات — لا يُستورد ولا يُعدّ مرفوضاً
            continue
        rec, errors = {}, []
        extra = {}
        for idx, field in mapping.items():
            raw = row[idx] if idx < len(row) else None
            val, err = parse_value(field, raw, year_hint)
            if err and field in SOFT_FIELDS:
                extra[field + "_raw"] = str(raw)[:120]
                warnings.append({"row": n, "field": field, "value": str(raw)[:40], "warning": err})
            elif err:
                errors.append({"field": field, "value": str(raw)[:40], "error": err})
            else:
                rec[field] = val
        for idx, h in extra_cols:
            raw = row[idx] if idx < len(row) else None
            if raw is not None and str(raw).strip() != "":
                extra[h[:60]] = raw if isinstance(raw, (int, float)) else str(raw)[:500]
        if extra:
            rec["extra"] = extra
        if (default_branch_id is not None and "branch_id" in ENTITIES[dataset_type]["fields"]
                and rec.get("branch_id") in (None, "")):
            rec["branch_id"] = default_branch_id
        if dataset_type == "cash_movement" and rec.get("date") is None and rec.get("period"):
            rec["date"] = rec["period"] + "-01"
        if dataset_type == "sale" and rec.get("net_sales") is None and rec.get("gross_sales") is not None:
            rec["net_sales"] = round(rec["gross_sales"] - (rec.get("discounts") or 0) - (rec.get("returns") or 0), 2)
        if "branch_id" in rec and rec["branch_id"] is not None and not str(rec["branch_id"]).isdigit():
            bid = branch_names.get(_norm(rec["branch_id"]))
            if bid is None:
                errors.append({"field": "branch_id", "value": str(rec["branch_id"])[:40], "error": "فرع غير معروف"})
            else:
                rec["branch_id"] = bid
        for _f in DATETIME_FIELDS:
            if isinstance(rec.get(_f), str) and rec[_f].startswith("T"):
                base = rec.get("date") or (rec.get("opened_time") or "")[:10] or (rec.get("start_time") or "")[:10]
                rec[_f] = (base + rec[_f]) if base and not base.startswith("T") else None
        if dataset_type in ("process_event",) and rec.get("start_time") is None:
            pass
        if (dataset_type == "purchase" and rec.get("total_cost") is None
                and rec.get("quantity") is not None and rec.get("unit_cost") is not None):
            rec["total_cost"] = round(float(rec["quantity"]) * float(rec["unit_cost"]), 2)
            rec["total_cost_derived"] = True
        subs = [rec]
        if summary_cash and errors:            # خطأ في الصف نفسه: يُرفض مرة واحدة لا مرتين
            rejected.append({"row": n, "record": rec, "errors": errors})
            continue
        if summary_cash:
            base = {k: v for k, v in rec.items() if k not in ("inflow", "outflow")}
            subs = []
            for direction, fld in (("in", "inflow"), ("out", "outflow")):
                v = rec.get(fld)
                if v:
                    sub = dict(base, amount=v, direction=direction)
                    sub.setdefault("movement_type", "ملخص شهري")
                    sub["movement_type"] = sub.get("movement_type") or "ملخص شهري"
                    sub["source"] = sub.get("source") or "bank_summary"
                    sub["reference"] = sub.get("reference") or f"{rec.get('period') or rec.get('date') or n}-{direction}"
                    subs.append(sub)
            if not subs and not errors:
                errors.append({"field": "inflow/outflow", "value": "", "error": "لا توجد إيداعات ولا سحوبات في هذا الصف"})
                subs = [base]
        for sub in subs:
            errs = list(errors)
            for f in req:
                if sub.get(f) is None:
                    errs.append({"field": f, "value": "", "error": "حقل مطلوب ناقص"})
            for f in ("quantity", "gross_sales", "total_cost", "amount", "closing_qty"):
                if isinstance(sub.get(f), (int, float)) and sub[f] < 0:
                    errs.append({"field": f, "value": sub[f], "error": "قيمة سالبة غير مقبولة"})
            if sub.get("direction") and sub["direction"] not in DIRECTIONS:
                errs.append({"field": "direction", "value": sub["direction"], "error": "اتجاه غير صالح"})
            key = tuple(str(sub.get(k, "")) for k in dup_key)
            if not errs and key in seen:
                rejected.append({"row": n, "record": sub, "errors": [{"field": "—", "value": "", "error": f"مكرر مع الصف {seen[key]}"}]})
                continue
            if errs:
                rejected.append({"row": n, "record": sub, "errors": errs})
            else:
                seen[key] = n
                sub["_row"] = n
                valid.append(sub)
    return {"valid": valid, "rejected": rejected, "total": len(rows),
            "valid_count": len(valid), "rejected_count": len(rejected), "duplicate_keys": list(dup_key),
            "warnings": warnings[:500], "warning_count": len(warnings), "skipped_totals": skipped_totals,
            "extra_columns": [h for _, h in extra_cols]}


# كلمات تدل على نوع الملف من عناوين أعمدته (تفصل بين أنواع تتشارك حقل «الاسم»)
TYPE_HINTS = {
    "customer": ("عميل", "العملاء", "customer", "client"),
    "supplier": ("مورد", "الموردين", "supplier", "vendor"),
    "employee": ("موظف", "الموظفين", "وظيف", "employee", "staff", "راتب", "salary"),
    "product": ("منتج", "صنف", "product", "sku", "item", "سعر البيع"),
    "department": ("قسم", "الاقسام", "اداره", "department"),
    "tax_invoice": ("ضريب", "زاتكا", "اصدار", "المشتري", "zatca", "vat", "مدين", "دائن"),
    "expense": ("مصروف", "مصاريف", "المصروفات", "expense", "opex", "بند المصروف"),
    "operation_order": ("تسليم", "تجهيز", "جاهز", "sla", "ready", "delivered", "طلب صحيح", "خطأ"),
    "process_event": ("مرحله", "stage", "بدايه", "نهايه", "start", "end"),
    "operational_issue": ("مشكله", "عطل", "بلاغ", "خطوره", "issue", "fault", "severity", "سبب جذري"),
    "job_opening": ("شاغر", "شاغره", "المتقدم", "مقابل", "عروض", "opening", "applicant", "vacanc"),
    "sale": ("مبيعات", "المبيعات", "sales", "فاتوره", "invoice"),
    "purchase": ("مشتريات", "شراء", "purchase", "po"),
    "inventory": ("مخزون", "رصيد", "inventory", "stock"),
    "cash_movement": ("ايداع", "سحب", "نقد", "بنك", "deposit", "withdraw", "cash", "bank", "مدين", "دائن", "الرصيد"),
    "receivable": ("ذمم", "ذمه", "استحقاق", "محصل", "تحصيل", "مستحق", "receivable", "due", "collected", "outstanding"),
}


def detect_type(headers, *, branch_default=False):
    """يقترح نوع الملف من أعمدته: الاكتمال أولاً، ثم عدد الأعمدة المطابقة، ثم دلالات العناوين.
    branch_default=True: للشركة فرع واحد، فلا يُعدّ غياب عمود الفرع نقصاً."""
    normed = [_norm(h) for h in headers or []]
    out = []
    for t in DATASET_TYPES:
        m = map_columns(headers, t)
        missing = [f for f in m["missing_required"] if not (f == "branch_id" and branch_default)]
        complete = not missing
        hints = sum(1 for h in normed for k in TYPE_HINTS.get(t, ()) if _norm(k) and _norm(k) in h)
        # ملف معاملات ينقصه عمود الفرع فقط: هو النوع الصحيح غالباً، لكن لا يُعتمد بدون الفرع
        needs_branch = missing == ["branch_id"] and len(m["mapping"]) >= 2
        base = 1000 if complete else (995 if needs_branch else 0)
        score = base + 10 * len(m["mapping"]) - (0 if needs_branch else 25 * len(missing)) + 15 * hints
        m = dict(m, missing_required=missing, needs_branch=needs_branch)
        out.append({"type": t, "ar": ENTITIES[t]["ar"], "en": ENTITIES[t]["en"], "complete": complete,
                    "needs_branch": m["needs_branch"],
                    "mapped": len(m["mapping"]), "missing_required": m["missing_required"], "score": score})
    out.sort(key=lambda x: -x["score"])
    return out


def assess_quality(validation, dataset_type, last_updated=None):
    """يستخدم طبقة الموثوقية القائمة (phase21) — لا نظام جودة ثانٍ."""
    rows = validation["valid"]
    req = required_fields(dataset_type)
    checks = [check_completeness(rows, req),
              check_validity(rows, [f for f in ("quantity", "gross_sales", "total_cost", "amount", "closing_qty")
                                    if f in all_fields(dataset_type)],
                             numeric_fields=[f for f in all_fields(dataset_type) if f in NUMERIC]),
              check_duplicates(rows, [k for k in DUP_KEYS.get(dataset_type, req) if k in all_fields(dataset_type)])]
    if dataset_type == "sale":
        checks.append(check_consistency(sum((r.get("net_sales") or 0) for r in rows),
                                        [sum((r.get("gross_sales") or 0) for r in rows),
                                         -sum((r.get("discounts") or 0) for r in rows),
                                         -sum((r.get("returns") or 0) for r in rows)], label="صافي المبيعات"))
    report = run_all_checks(checks)
    if validation["rejected_count"]:
        ratio = validation["rejected_count"] / max(validation["total"], 1)
        report["overall_score"] = max(0, round(report["overall_score"] * (1 - ratio)))
        report["status"] = "fail" if report["overall_score"] < 50 else ("warning" if report["overall_score"] < 80 else report["status"])
    report["gate"] = "ALLOW" if report["status"] == "pass" else ("QUALIFY" if report["status"] == "warning" else "BLOCK")
    report["rejected_ratio_pct"] = round(validation["rejected_count"] / max(validation["total"], 1) * 100, 1)
    return report


def lineage(dataset_id, source_file, imported_by, row_no, period=None, branch_id=None):
    return {"dataset_id": dataset_id, "source_type": "upload", "source_file": source_file,
            "source_row": row_no, "imported_by": imported_by, "period": period, "branch_id": branch_id,
            "ingest_version": INGEST_VERSION}
