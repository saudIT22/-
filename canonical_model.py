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
              "termination_date": OPTIONAL, "monthly_cost": SENSITIVE, "email": OPTIONAL, "phone": OPTIONAL}},
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
           "closing_value", "amount", "cost", "selling_price", "monthly_cost", "credit_limit", "inflow", "outflow", "received_qty", "rejected_qty", "balance", "paid_amount"}
DATE_FIELDS = {"date", "hire_date", "termination_date", "expected_date", "received_date", "invoice_date", "due_date", "paid_date"}
PERIOD_FIELDS = {"period"}
# مفاتيح كشف التكرار لكل نوع
DUP_KEYS = {"sale": ("date", "branch_id", "reference", "product_sku"),
            "purchase": ("date", "branch_id", "reference", "product_sku"),
            "inventory": ("period", "branch_id", "product_sku"),
            "cash_movement": ("date", "branch_id", "reference", "amount"),
            "employee": ("employee_code",), "product": ("sku",), "supplier": ("name",),
            "customer": ("name", "phone", "email"),
            "receivable": ("reference", "customer_name", "invoice_date", "amount"),
            "department": ("name", "branch_id")}
DIRECTIONS = {"in", "out"}


def required_fields(dtype):
    return [f for f, k in ENTITIES[dtype]["fields"].items() if k == REQUIRED]


def sensitive_fields(dtype):
    return [f for f, k in ENTITIES[dtype]["fields"].items() if k == SENSITIVE]


def all_fields(dtype):
    return list(ENTITIES[dtype]["fields"])
