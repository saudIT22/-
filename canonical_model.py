"""
NABBAH 2.4 — Canonical enterprise entities (single definition for all modules).
Pure: no DB, no AI. The SQLModel tables in main.py mirror these definitions.
"""
REQUIRED, OPTIONAL, SENSITIVE = "required", "optional", "sensitive"

ENTITIES = {
 "department": {"ar": "الأقسام", "en": "Departments", "table": "companydepartment",
   "fields": {"name": REQUIRED, "branch_id": OPTIONAL, "code": OPTIONAL, "active": OPTIONAL}},
 "employee": {"ar": "الموظفون", "en": "Employees", "table": "companyemployee",
   "fields": {"employee_code": REQUIRED, "name": REQUIRED, "branch_id": OPTIONAL, "department_id": OPTIONAL,
              "role": OPTIONAL, "employment_status": OPTIONAL, "hire_date": OPTIONAL,
              "termination_date": OPTIONAL, "monthly_cost": SENSITIVE, "email": OPTIONAL, "phone": OPTIONAL,
              # Phase 2.9 — كلها اختيارية: غيابها = «غير متاح» في التحليل، لا صفر
              "employment_type": OPTIONAL, "manager": OPTIONAL, "termination_type": OPTIONAL,
              "basic_salary": SENSITIVE, "allowances": SENSITIVE, "benefits": SENSITIVE,
              "performance_rating": OPTIONAL, "last_promotion_date": OPTIONAL, "training_hours": OPTIONAL,
              "absence_days": OPTIONAL, "overtime_hours": OPTIONAL, "critical_role": OPTIONAL, "successors": OPTIONAL}},
 # ── Phase 3.2 — سجل الفواتير الضريبية/الإلكترونية (تصدير من نظام الفوترة)
 "tax_invoice": {"ar": "سجل الفواتير الضريبية", "en": "Tax invoice register", "table": "companytaxinvoice",
   "fields": {"invoice_number": REQUIRED, "issue_date": REQUIRED, "invoice_type": OPTIONAL, "branch_id": OPTIONAL,
              "buyer_name": OPTIONAL, "buyer_vat": OPTIONAL, "taxable_amount": OPTIONAL, "vat_amount": OPTIONAL,
              "total_amount": OPTIONAL, "currency": OPTIONAL, "original_invoice": OPTIONAL, "zatca_status": OPTIONAL}},
 # ── Phase 2.11 — المصروفات التشغيلية من النظام المحاسبي (اختياري: يحوّل المصروفات من الأساس النقدي إلى المسجّل)
 "expense": {"ar": "المصروفات", "en": "Expenses", "table": "companyexpense",
   "fields": {"date": REQUIRED, "amount": REQUIRED, "category": OPTIONAL, "description": OPTIONAL,
              "branch_id": OPTIONAL, "vendor": OPTIONAL}},
 # ── Phase 2.10 — العمليات (طلب/خدمة لكل صف، مراحل العملية، المشاكل التشغيلية)
 "operation_order": {"ar": "الطلبات التشغيلية", "en": "Operational orders", "table": "companyopsorder",
   "fields": {"reference": REQUIRED, "date": REQUIRED, "branch_id": OPTIONAL, "department_id": OPTIONAL, "service": OPTIONAL,
              "status": OPTIONAL, "created_time": OPTIONAL, "ready_time": OPTIONAL, "delivered_time": OPTIONAL,
              "due_time": OPTIONAL, "items": OPTIONAL, "accurate": OPTIONAL, "defect_type": OPTIONAL, "rework": OPTIONAL}},
 "process_event": {"ar": "مراحل العمليات", "en": "Process events", "table": "companyopsevent",
   "fields": {"reference": REQUIRED, "stage": REQUIRED, "start_time": REQUIRED, "end_time": OPTIONAL,
              "branch_id": OPTIONAL, "department_id": OPTIONAL}},
 "operational_issue": {"ar": "المشاكل والأعطال التشغيلية", "en": "Operational issues", "table": "companyopsissue",
   "fields": {"title": REQUIRED, "opened_time": REQUIRED, "branch_id": OPTIONAL, "department_id": OPTIONAL,
              "severity": OPTIONAL, "status": OPTIONAL, "resolved_time": OPTIONAL, "owner": OPTIONAL,
              "root_cause": OPTIONAL, "impact": OPTIONAL, "sla_hours": OPTIONAL}},
 "job_opening": {"ar": "الوظائف الشاغرة والتوظيف", "en": "Job openings", "table": "companyjobopening",
   "fields": {"title": REQUIRED, "opened_date": REQUIRED, "department_id": OPTIONAL, "branch_id": OPTIONAL,
              "status": OPTIONAL, "filled_date": OPTIONAL, "applicants": OPTIONAL, "interviews": OPTIONAL,
              "offers": OPTIONAL, "hires": OPTIONAL, "hiring_cost": OPTIONAL}},
 "product": {"ar": "المنتجات", "en": "Products", "table": "companyproduct",
   "fields": {"sku": REQUIRED, "name": REQUIRED, "category": OPTIONAL, "unit": OPTIONAL,
              "cost": OPTIONAL, "selling_price": OPTIONAL, "active": OPTIONAL}},
 "supplier": {"ar": "الموردون", "en": "Suppliers", "table": "companysupplier",
   "fields": {"name": REQUIRED, "supplier_code": OPTIONAL, "category": OPTIONAL, "payment_terms": OPTIONAL,
              "contact_person": OPTIONAL, "email": OPTIONAL, "phone": OPTIONAL, "city": OPTIONAL,
              "tax_number": OPTIONAL, "active": OPTIONAL}},
 "customer": {"ar": "العملاء", "en": "Customers", "table": "companycustomer",
   "fields": {"name": REQUIRED, "customer_code": OPTIONAL, "email": OPTIONAL, "phone": OPTIONAL, "city": OPTIONAL,
              "segment": OPTIONAL, "customer_type": OPTIONAL, "tax_number": OPTIONAL, "credit_limit": OPTIONAL,
              "branch_id": OPTIONAL, "active": OPTIONAL, "notes": OPTIONAL}},
 "sale": {"ar": "المبيعات التفصيلية", "en": "Sales", "table": "companysale",
   "fields": {"date": REQUIRED, "branch_id": REQUIRED, "reference": OPTIONAL, "channel": OPTIONAL,
              "product_sku": OPTIONAL, "category": OPTIONAL, "quantity": OPTIONAL, "gross_sales": REQUIRED,
              "discounts": OPTIONAL, "returns": OPTIONAL, "net_sales": OPTIONAL, "vat": OPTIONAL,
              "payment_method": OPTIONAL, "promotion": OPTIONAL, "customer_name": OPTIONAL}},
 "purchase": {"ar": "المشتريات", "en": "Purchases", "table": "companypurchase",
   "fields": {"date": REQUIRED, "branch_id": REQUIRED, "supplier_name": OPTIONAL, "reference": OPTIONAL,
              "product_sku": OPTIONAL, "category": OPTIONAL, "quantity": OPTIONAL, "unit_cost": OPTIONAL,
              "total_cost": REQUIRED, "status": OPTIONAL, "vat": OPTIONAL,
              # Phase 2.7: الأداء والتسليم والجودة (اختيارية — غيابها يعني «غير متاح» لا صفر)
              "expected_date": OPTIONAL, "received_date": OPTIONAL, "received_qty": OPTIONAL, "rejected_qty": OPTIONAL}},
 "inventory": {"ar": "المخزون", "en": "Inventory", "table": "companyinventory",
   "fields": {"period": REQUIRED, "branch_id": REQUIRED, "product_sku": REQUIRED, "opening_qty": OPTIONAL,
              "opening_value": OPTIONAL, "purchases_qty": OPTIONAL, "sold_qty": OPTIONAL,
              "adjustments_qty": OPTIONAL, "closing_qty": REQUIRED, "closing_value": OPTIONAL}},
 "cash_movement": {"ar": "الحركات النقدية", "en": "Cash movements", "table": "companycashmovement",
   "fields": {"date": REQUIRED, "branch_id": OPTIONAL, "movement_type": OPTIONAL, "category": OPTIONAL,
              "amount": REQUIRED, "direction": REQUIRED, "reference": OPTIONAL, "source": OPTIONAL,
              # ملخصات شهرية (كشف بنك): الشهر + الإيداعات + السحوبات تتحوّل تلقائياً إلى حركتين
              "period": OPTIONAL, "inflow": OPTIONAL, "outflow": OPTIONAL,
              # Phase 2.8: الحساب والطرف المقابل والرصيد المُبلّغ (كشف البنك)
              "account": OPTIONAL, "counterparty": OPTIONAL, "balance": OPTIONAL}},
 "receivable": {"ar": "الذمم المدينة", "en": "Receivables", "table": "companyreceivable",
   "fields": {"invoice_date": REQUIRED, "amount": REQUIRED, "customer_name": OPTIONAL, "reference": OPTIONAL,
              "due_date": OPTIONAL, "paid_amount": OPTIONAL, "paid_date": OPTIONAL, "branch_id": OPTIONAL}},
}
DATASET_TYPES = tuple(ENTITIES)
NUMERIC = {"quantity", "gross_sales", "discounts", "returns", "net_sales", "vat", "unit_cost", "total_cost",
           "opening_qty", "opening_value", "purchases_qty", "sold_qty", "adjustments_qty", "closing_qty",
           "closing_value", "amount", "cost", "selling_price", "monthly_cost", "credit_limit", "inflow", "outflow", "received_qty", "rejected_qty", "balance", "paid_amount", "basic_salary", "allowances", "benefits", "performance_rating", "training_hours", "absence_days", "overtime_hours", "successors", "applicants", "interviews", "offers", "hires", "hiring_cost", "items", "sla_hours", "taxable_amount", "vat_amount", "total_amount"}
DATE_FIELDS = {"date", "hire_date", "termination_date", "expected_date", "received_date", "invoice_date", "due_date", "paid_date", "last_promotion_date", "opened_date", "filled_date", "issue_date"}
PERIOD_FIELDS = {"period"}
# تاريخ + وقت (العمليات تُقاس بالدقائق). وقت بلا تاريخ يُدمج مع «التاريخ» في نفس الصف.
DATETIME_FIELDS = {"created_time", "ready_time", "delivered_time", "due_time", "start_time", "end_time",
                   "opened_time", "resolved_time"}
# مفاتيح كشف التكرار لكل نوع
DUP_KEYS = {"sale": ("date", "branch_id", "reference", "product_sku"),
            "purchase": ("date", "branch_id", "reference", "product_sku"),
            "inventory": ("period", "branch_id", "product_sku"),
            "cash_movement": ("date", "branch_id", "reference", "amount"),
            "employee": ("employee_code",), "product": ("sku",), "supplier": ("name",),
            "customer": ("name", "phone", "email"),
            "expense": ("date", "amount", "description", "branch_id"),
            "tax_invoice": ("invoice_number", "invoice_type", "issue_date", "total_amount", "branch_id"),
            "operation_order": ("reference", "branch_id"),
            "process_event": ("reference", "stage", "start_time"),
            "operational_issue": ("title", "opened_time", "branch_id"),
            "job_opening": ("title", "opened_date", "department_id", "branch_id"),
            "receivable": ("reference", "customer_name", "invoice_date", "amount"),
            "department": ("name", "branch_id")}
DIRECTIONS = {"in", "out"}


def required_fields(dtype):
    return [f for f, k in ENTITIES[dtype]["fields"].items() if k == REQUIRED]


def sensitive_fields(dtype):
    return [f for f, k in ENTITIES[dtype]["fields"].items() if k == SENSITIVE]


def all_fields(dtype):
    return list(ENTITIES[dtype]["fields"])
