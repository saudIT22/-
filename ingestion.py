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
 "name": ["الاسم", "اسم", "name", "full name", "اسم المنتج", "اسم المورد", "اسم القسم"],
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
        k = period_key(raw, "month") or (str(raw).strip() if re.match(r"^\d{4}-\d{2}$", str(raw).strip()) else None)
        return (k, None) if k else (None, "فترة غير صالحة")
    if field == "direction":
        s = _norm(raw)
        for key, val in (("in", "in"), ("داخل", "in"), ("وارد", "in"), ("قبض", "in"),
                         ("out", "out"), ("خارج", "out"), ("صادر", "out"), ("صرف", "out")):
            if key in s:
                return val, None
        return None, "اتجاه غير معروف (داخل/خارج)"
    return str(raw).strip(), None


def validate_rows(rows, mapping, dataset_type, *, branch_names=None, existing_keys=None):
    """يحوّل الصفوف ويتحقق منها. لا يستورد شيئاً: يُرجع الصالح والمرفوض مع الأسباب."""
    req = required_fields(dataset_type)
    dup_key = DUP_KEYS.get(dataset_type, tuple(req))
    branch_names = {(_norm(k)): v for k, v in (branch_names or {}).items()}
    seen, valid, rejected = dict(existing_keys or {}), [], []
    for n, row in enumerate(rows, start=2):  # 2 = أول صف بعد العناوين
        rec, errors = {}, []
        for idx, field in mapping.items():
            raw = row[idx] if idx < len(row) else None
            val, err = parse_value(field, raw)
            if err:
                errors.append({"field": field, "value": str(raw)[:40], "error": err})
            else:
                rec[field] = val
        if dataset_type == "sale" and rec.get("net_sales") is None and rec.get("gross_sales") is not None:
            rec["net_sales"] = round(rec["gross_sales"] - (rec.get("discounts") or 0) - (rec.get("returns") or 0), 2)
        if "branch_id" in rec and rec["branch_id"] is not None and not str(rec["branch_id"]).isdigit():
            bid = branch_names.get(_norm(rec["branch_id"]))
            if bid is None:
                errors.append({"field": "branch_id", "value": str(rec["branch_id"])[:40], "error": "فرع غير معروف"})
            else:
                rec["branch_id"] = bid
        for f in req:
            if rec.get(f) is None:
                errors.append({"field": f, "value": "", "error": "حقل مطلوب ناقص"})
        for f in ("quantity", "gross_sales", "total_cost", "amount", "closing_qty"):
            if isinstance(rec.get(f), (int, float)) and rec[f] < 0:
                errors.append({"field": f, "value": rec[f], "error": "قيمة سالبة غير مقبولة"})
        if rec.get("direction") and rec["direction"] not in DIRECTIONS:
            errors.append({"field": "direction", "value": rec["direction"], "error": "اتجاه غير صالح"})
        key = tuple(str(rec.get(k, "")) for k in dup_key)
        if not errors and key in seen:
            rejected.append({"row": n, "record": rec, "errors": [{"field": "—", "value": "", "error": f"مكرر مع الصف {seen[key]}"}]})
            continue
        if errors:
            rejected.append({"row": n, "record": rec, "errors": errors})
        else:
            seen[key] = n
            rec["_row"] = n
            valid.append(rec)
    return {"valid": valid, "rejected": rejected, "total": len(rows),
            "valid_count": len(valid), "rejected_count": len(rejected), "duplicate_keys": list(dup_key)}


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
