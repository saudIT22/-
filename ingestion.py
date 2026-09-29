"""
NABBAH 2.4 — Ingestion foundation (pure: no DB, no AI, no file I/O).
Maps Arabic/English columns to canonical fields, parses numbers/dates, detects
duplicates and missing mandatory fields, validates relationships, and scores quality
through the EXISTING phase21 Data Trust layer (no second quality system).
Nothing is ever imported silently: rows are marked valid or rejected with reasons.
"""
import os, re, sys
from decimal import Decimal
_H = os.path.dirname(os.path.abspath(__file__))
for _d in ("..", "../phase21", "../phase22"):
    sys.path.insert(0, os.path.join(_H, _d))
from canonical_model import (ENTITIES, DATASET_TYPES, NUMERIC, DATE_FIELDS, PERIOD_FIELDS,
                             DUP_KEYS, DIRECTIONS, required_fields, all_fields)
from period_model import parse_date, period_key
from nabbah_finance import to_decimal
from nabbah_trust import (check_completeness, check_validity, check_duplicates,
                          check_consistency, run_all_checks)

INGEST_VERSION = "1.0"

# مرادفات الأعمدة: عربية وإنجليزية -> الحقل المرجعي
SYNONYMS = {
 "date": ["التاريخ", "تاريخ", "date", "day", "trx date", "invoice date", "تاريخ الفاتورة"],
 "period": ["الفترة", "الشهر", "period", "month"],
 "branch_id": ["الفرع", "اسم الفرع", "branch", "branch name", "location", "الموقع"],
 "reference": ["رقم الفاتورة", "الفاتورة", "المرجع", "invoice", "invoice no", "reference", "ref", "order", "رقم الطلب", "po", "رقم أمر الشراء"],
 "channel": ["القناة", "channel", "قناة البيع", "sales channel"],
 "product_sku": ["رمز المنتج", "الصنف", "sku", "product", "product code", "item", "المنتج", "كود الصنف"],
 "category": ["التصنيف", "الفئة", "category", "group", "المجموعة"],
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
 "movement_type": ["نوع الحركة", "التصنيف النقدي", "movement type", "cash type"],
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


def parse_month(raw):
    """«2026-01» «01/2026» «يناير 2026» «Jan 2026» «٢٠٢٦-٠١» أو تاريخ كامل → YYYY-MM. غير ذلك None."""
    if raw is None:
        return None
    if hasattr(raw, "year") and hasattr(raw, "month"):
        return f"{raw.year:04d}-{raw.month:02d}"
    s = str(raw).strip().translate(_AR_DIGITS)
    if not s:
        return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})$", s) or None
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{4})$", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return f"{int(m.group(2)):04d}-{int(m.group(1)):02d}"
    n = _norm(s)
    y = re.search(r"(\d{4})", n)
    if y:
        word = _norm(n.replace(y.group(1), "")).strip()
        for name in sorted(_MONTHS, key=len, reverse=True):
            if word == _norm(name) or word.startswith(_norm(name) + " ") or word.endswith(" " + _norm(name)):
                return f"{int(y.group(1)):04d}-{_MONTHS[name]:02d}"
    k = period_key(s, "month")
    return k or None


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
    if dataset_type == "cash_movement":
        vals = set(mapping.values())
        if vals & {"inflow", "outflow"}:          # ملخص: المبلغ والاتجاه يُشتقّان
            missing = [f for f in missing if f not in ("amount", "direction")]
        if "period" in vals:                       # الشهر يكفي كتاريخ (أول يوم في الشهر)
            missing = [f for f in missing if f != "date"]
    return {"mapping": mapping, "unmapped": unmapped, "missing_required": missing,
            "fields": fields, "required": required_fields(dataset_type)}


def parse_value(field, raw):
    """يحوّل القيمة حسب نوع الحقل. يُرجع (value, error) — لا تخمين صامت."""
    if raw is None or str(raw).strip() == "":
        return None, None
    if field in NUMERIC:
        v = to_decimal(str(raw).replace("ر.س", "").replace("SAR", ""))
        return (float(v), None) if v is not None else (None, "قيمة غير رقمية")
    if field in DATE_FIELDS:
        d = parse_date(raw)
        return (d.isoformat(), None) if d else (None, "تاريخ غير صالح")
    if field in PERIOD_FIELDS:
        k = parse_month(raw)
        return (k, None) if k else (None, "فترة غير صالحة")
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


SOFT_FIELDS = {"email", "phone"}   # خطأ في بيانات التواصل لا يرفض الصف: يُحفظ الأصل في extra مع تنبيه


def validate_rows(rows, mapping, dataset_type, *, branch_names=None, existing_keys=None,
                  headers=None, default_branch_id=None):
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
    for n, row in enumerate(rows, start=2):  # 2 = أول صف بعد العناوين
        rec, errors = {}, []
        extra = {}
        for idx, field in mapping.items():
            raw = row[idx] if idx < len(row) else None
            val, err = parse_value(field, raw)
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
        subs = [rec]
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
            "warnings": warnings[:500], "warning_count": len(warnings),
            "extra_columns": [h for _, h in extra_cols]}


# كلمات تدل على نوع الملف من عناوين أعمدته (تفصل بين أنواع تتشارك حقل «الاسم»)
TYPE_HINTS = {
    "customer": ("عميل", "العملاء", "customer", "client"),
    "supplier": ("مورد", "الموردين", "supplier", "vendor"),
    "employee": ("موظف", "الموظفين", "وظيف", "employee", "staff", "راتب", "salary"),
    "product": ("منتج", "صنف", "product", "sku", "item", "سعر البيع"),
    "department": ("قسم", "الاقسام", "اداره", "department"),
    "sale": ("مبيعات", "المبيعات", "sales", "فاتوره", "invoice"),
    "purchase": ("مشتريات", "شراء", "purchase", "po"),
    "inventory": ("مخزون", "رصيد", "inventory", "stock"),
    "cash_movement": ("ايداع", "سحب", "نقد", "بنك", "deposit", "withdraw", "cash", "bank"),
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
