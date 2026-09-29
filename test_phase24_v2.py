"""Phase 2.4 v2 — comprehensive ingestion: customers, contacts, bank summaries, detection, no data loss."""
import os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import canonical_model as cm
from ingestion import map_columns, validate_rows, detect_type, parse_month, parse_value
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")

print("[GROUP] the exact file that was rejected (monthly bank summary)")
H = ["#", "الشهر", "إجمالي الإيداعات (SAR)", "إجمالي السحوبات (SAR)", "صافي الحركة (SAR)", "عدد المعاملات", "الرصيد الختامي (SAR)"]
R = [[1, "يناير 2026", 150000, 90000, 60000, 120, 500000], [2, "فبراير 2026", 0, 30000, -30000, 40, 470000],
     [3, "مارس 2026", 80000, None, 80000, 60, 550000]]
d = detect_type(H)
ct("detected as cash movements, not sales", d[0]["type"] == "cash_movement" and d[0]["complete"])
ct("sales flagged incomplete for this file", next(x for x in d if x["type"] == "sale")["complete"] is False)
m = map_columns(H, "cash_movement")
ct("month → period, deposits → inflow, withdrawals → outflow",
   set(m["mapping"].values()) == {"period", "inflow", "outflow"} and not m["missing_required"])
v = validate_rows(R, m["mapping"], "cash_movement", headers=H)
ct("3 months → 4 movements (zero sides skipped)", v["valid_count"] == 4 and v["rejected_count"] == 0)
jan_in = next(x for x in v["valid"] if x["date"] == "2026-01-01" and x["direction"] == "in")
ct("deposit becomes an 'in' movement dated the 1st", jan_in["amount"] == 150000.0 and jan_in["period"] == "2026-01")
ct("withdrawal becomes an 'out' movement", any(x["direction"] == "out" and x["amount"] == 90000.0 for x in v["valid"]))
ct("summary movements are labelled as such", jan_in["source"] == "bank_summary" and jan_in["movement_type"])
ct("unmapped columns kept, not dropped", jan_in["extra"]["الرصيد الختامي (SAR)"] == 500000 and "عدد المعاملات" in jan_in["extra"])
ct("'#' index column ignored", "#" not in jan_in["extra"])
ct("extra columns reported", "صافي الحركة (SAR)" in v["extra_columns"])

print("[GROUP] month parsing")
cases = {"2026-01": "2026-01", "2026/3": "2026-03", "03/2026": "2026-03", "يناير 2026": "2026-01", "مارس ٢٠٢٦": "2026-03",
         "Jan 2026": "2026-01", "September 2026": "2026-09", "أيلول 2026": "2026-09", "2026-05-17": "2026-05"}
for raw, exp in cases.items():
    ct(f"parse_month({raw})", parse_month(raw) == exp)
ct("excel datetime", parse_month(datetime(2026, 7, 3)) == "2026-07")
ct("garbage → None", parse_month("قريباً") is None and parse_month("2026-13") is None)

print("[GROUP] customers")
ct("customer entity exists", "customer" in cm.DATASET_TYPES and cm.required_fields("customer") == ["name"])
CH = ["اسم العميل", "البريد الإلكتروني", "الجوال", "المدينة", "الرقم الضريبي", "حد الائتمان", "نوع العميل", "ملاحظات", "تاريخ أول شراء"]
cm_ = map_columns(CH, "customer")
ct("customer columns map", {"name", "email", "phone", "city", "tax_number", "credit_limit", "customer_type", "notes"} <= set(cm_["mapping"].values()))
ct("customer file detected as customers", detect_type(CH)[0]["type"] == "customer")
cv = validate_rows([["شركة الأفق", " Info@Ofoq.SA ", "٠٥٥٩٩٨٦٥٥٣", "جدة", "300012345600003", "50,000", "شركات", "عميل مهم", "2025-02-01"],
                    ["مؤسسة النور", "bad-email", "0551112222", "الرياض", "", "", "أفراد", "", ""],
                    ["", "x@y.com", "", "", "", "", "", "", ""]], cm_["mapping"], "customer", headers=CH)
c0 = cv["valid"][0]
ct("email normalised to lowercase", c0["email"] == "info@ofoq.sa")
ct("Arabic-digit phone normalised", c0["phone"] == "0559986553")
ct("credit limit numeric", c0["credit_limit"] == 50000.0)
ct("unknown column kept in extra", c0["extra"]["تاريخ أول شراء"] == "2025-02-01")
ct("bad email does NOT reject the customer", cv["valid_count"] == 2 and cv["valid"][1].get("email") is None)
ct("bad email kept raw + warning", cv["valid"][1]["extra"]["email_raw"] == "bad-email" and cv["warning_count"] == 1)
ct("customer without a name rejected", cv["rejected_count"] == 1)

print("[GROUP] suppliers & employees with contact data")
SH = ["اسم المورد", "الشخص المسؤول", "الايميل", "رقم الهاتف", "شروط الدفع", "نشط"]
sm = map_columns(SH, "supplier")
ct("supplier contact columns map", {"name", "contact_person", "email", "phone", "payment_terms", "active"} == set(sm["mapping"].values()))
sv = validate_rows([["مورد أ", "خالد", "k@a.com", "0501234567", "30 يوم", "نعم"], ["مورد ب", "", "", "", "", "لا"]], sm["mapping"], "supplier", headers=SH)
ct("active 'نعم' → 1, 'لا' → 0 (no text in integer column)", sv["valid"][0]["active"] == 1 and sv["valid"][1]["active"] == 0)
ct("employee has email/phone fields", {"email", "phone"} <= set(cm.all_fields("employee")))

print("[GROUP] sales with customers + single-branch default")
SAH = ["التاريخ", "المبيعات", "اسم العميل", "طاولة"]
sam = map_columns(SAH, "sale")
ct("sale maps customer_name", "customer_name" in sam["mapping"].values())
sv2 = validate_rows([["2026-09-01", 500, "أحمد", "5"]], sam["mapping"], "sale", headers=SAH, default_branch_id=7)
ct("no branch column + one branch → default branch used", sv2["valid_count"] == 1 and sv2["valid"][0]["branch_id"] == 7)
sv3 = validate_rows([["2026-09-01", 500, "أحمد", "5"]], sam["mapping"], "sale", headers=SAH)
ct("no default → still rejected honestly", sv3["rejected_count"] == 1)

print("[GROUP] detection tie-breaks (regressions found in browser test)")
ct("customer file with emails is customers, not suppliers", detect_type(["اسم العميل", "البريد الإلكتروني", "الجوال", "المدينة"])[0]["type"] == "customer")
ct("supplier file is suppliers", detect_type(["اسم المورد", "البريد الإلكتروني", "الجوال", "شروط الدفع"])[0]["type"] == "supplier")
ct("sales without branch (single-branch company) is sales, not departments",
   detect_type(["التاريخ", "المبيعات", "اسم العميل"], branch_default=True)[0]["type"] == "sale")
_d = detect_type(["التاريخ", "المبيعات", "اسم العميل"])[0]
ct("sales without branch + several branches -> recognised as sales but flagged needs_branch (not imported as customers)",
   _d["type"] == "sale" and _d["needs_branch"] and not _d["complete"])
ct("employees file", detect_type(["رقم الموظف", "اسم الموظف", "الوظيفة", "الراتب", "الجوال"])[0]["type"] == "employee")
ct("products file", detect_type(["رمز المنتج", "اسم المنتج", "سعر البيع", "الفئة"])[0]["type"] == "product")
ct("bank summary still cash", detect_type(H)[0]["type"] == "cash_movement")

print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
