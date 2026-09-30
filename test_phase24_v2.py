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
ct("month → period, deposits → inflow, withdrawals → outflow, closing balance → balance",
   set(m["mapping"].values()) == {"period", "inflow", "outflow", "balance"} and not m["missing_required"])
v = validate_rows(R, m["mapping"], "cash_movement", headers=H)
ct("3 months → 4 movements (zero sides skipped)", v["valid_count"] == 4 and v["rejected_count"] == 0)
jan_in = next(x for x in v["valid"] if x["date"] == "2026-01-01" and x["direction"] == "in")
ct("deposit becomes an 'in' movement dated the 1st", jan_in["amount"] == 150000.0 and jan_in["period"] == "2026-01")
ct("withdrawal becomes an 'out' movement", any(x["direction"] == "out" and x["amount"] == 90000.0 for x in v["valid"]))
ct("summary movements are labelled as such", jan_in["source"] == "bank_summary" and jan_in["movement_type"])
ct("closing balance now mapped (2.8) + unmapped columns kept", jan_in["balance"] == 500000.0 and "عدد المعاملات" in jan_in["extra"])
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

print("[GROUP] real-world month/date formats (bank statement regression)")
from datetime import date as _d
from ingestion import parse_any_date, year_hint_from
M = {"يناير": ("2024-01", 2024), "Jan-24": ("2024-01", None), "يناير-24": ("2024-01", None), "2024M03": ("2024-03", None),
     "January, 2024": ("2024-01", None), "2024 فبراير": ("2024-02", None), "مارس ٢٠٢٤م": ("2024-03", None),
     "2024-01-01 00:00:00": ("2024-01", None), "45306": ("2024-01", None), "3": ("2024-03", 2024),
     "كانون الثاني 2024": ("2024-01", None), "رمضان 1445": ("2024-03", None), "1445-09": ("2024-03", None)}
for raw, (exp, yh) in M.items():
    ct(f"parse_month({raw!r}, year_hint={yh})", parse_month(raw, yh) == exp)
ct("Excel datetime cell", parse_month(datetime(2024, 5, 1)) == "2024-05")
ct("Excel serial number cell", parse_month(45292) == "2024-01")
ct("month name without any year -> None (no silent guess)", parse_month("يناير") is None)
ct("year from file name", year_hint_from("كشف_بنكي_2024.xlsx") == 2024 and year_hint_from("sales.xlsx") is None)
ct("date: datetime cell → date", parse_any_date(datetime(2024, 1, 15, 9, 30)) == _d(2024, 1, 15))
ct("date: Excel serial", parse_any_date(45306) == _d(2024, 1, 15))
ct("date: US MM/DD when day > 12", parse_any_date("01/15/2024") == _d(2024, 1, 15))
ct("date: Saudi DD/MM", parse_any_date("15/01/2024") == _d(2024, 1, 15))
ct("date: 'Jan 15, 2024'", parse_any_date("Jan 15, 2024") == _d(2024, 1, 15))
ct("date: '15 يناير 2024'", parse_any_date("15 يناير 2024") == _d(2024, 1, 15))
ct("numbers: '1,250.50 ر.س'", parse_value("amount", "1,250.50 ر.س") == (1250.5, None))
ct("numbers: Arabic digits '١٢٥٠٫٥'", parse_value("amount", "١٢٥٠٫٥") == (1250.5, None))
ct("numbers: accounting negative '(300)'", parse_value("discounts", "(300)") == (-300.0, None))
MH = ["الشهر", "إجمالي الإيداعات (SAR)", "إجمالي السحوبات (SAR)"]
mm = map_columns(MH, "cash_movement")["mapping"]
bad = validate_rows([["يناير", 100, 50], ["فبراير", 200, 70]], mm, "cash_movement", headers=MH)
ct("month-only rows without year: each row rejected ONCE (not twice)", bad["rejected_count"] == 2 and bad["valid_count"] == 0)
ct("…with a reason that says what to do", "بلا سنة" in bad["rejected"][0]["errors"][0]["error"])
good = validate_rows([["يناير", 100, 50], ["فبراير", 200, 70]], mm, "cash_movement", headers=MH, year_hint=2024)
ct("same rows with year from the file title → 4 movements", good["valid_count"] == 4 and good["valid"][0]["date"] == "2024-01-01")

tot = validate_rows([["يناير", 100, 50], ["الإجمالي", 100, 50]], mm, "cash_movement", headers=MH, year_hint=2024)
ct("'الإجمالي' row skipped (not imported, not counted as rejected)", tot["valid_count"] == 2 and tot["rejected_count"] == 0 and tot["skipped_totals"] == [3])

print("[GROUP] 2.8 file types")
ct("receivables file detected as receivables (not sales)", detect_type(["رقم الفاتورة", "العميل", "الفرع", "تاريخ الفاتورة", "تاريخ الاستحقاق", "المبلغ", "المبلغ المحصل", "تاريخ التحصيل"])[0]["type"] == "receivable")
ct("bank statement with debit/credit/balance → cash movements", detect_type(["التاريخ", "الوصف", "الفرع", "الطرف", "مدين", "دائن", "الرصيد", "الحساب"])[0]["type"] == "cash_movement")

print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
