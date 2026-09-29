"""Phase 2.4 tests — canonical model, periods, metric registry, ingestion, quality.
Run: python3 test_phase24.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import canonical_model as cm, period_model as pm, metric_registry as mr, ingestion as ing
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")

print("[GROUP] canonical model")
ct("9 entities defined (customers added in 2.5)", len(cm.DATASET_TYPES) == 9 and "customer" in cm.DATASET_TYPES)
ct("sale required fields", set(cm.required_fields("sale")) == {"date", "branch_id", "gross_sales"})
ct("payroll marked sensitive", cm.sensitive_fields("employee") == ["monthly_cost"])
ct("every entity has a table + duplicate key", all(cm.ENTITIES[t].get("table") and t in cm.DUP_KEYS for t in cm.DATASET_TYPES))

print("[GROUP] period model")
ct("month key", pm.period_key("2026-09-15", "month") == "2026-09")
ct("quarter key", pm.period_key("2026-09-15", "quarter") == "2026-Q3")
ct("year key", pm.period_key("2026-09-15", "year") == "2026")
ct("day key", pm.period_key("15/09/2026", "day") == "2026-09-15")
ct("week key", pm.period_key("2026-09-15", "week").startswith("2026-W"))
ct("invalid date -> None", pm.period_key("not-a-date", "month") is None)
ct("previous month across year", pm.previous_key("2026-01", "month") == "2025-12")
ct("previous quarter across year", pm.previous_key("2026-Q1", "quarter") == "2025-Q4")
ct("same period last year", pm.same_period_last_year("2026-09", "month") == "2025-09")
c = pm.build_comparison(["2026-07", "2026-08", "2026-09"], grain="month")
ct("comparison uses existing data", c["current"] == "2026-09" and c["previous"] == "2026-08")
ct("no last-year data -> None + gap", c["same_period_last_year"] is None and any(g["missing"] == "same_period_last_year" for g in c["gaps"]))
ct("empty input -> no fabrication", pm.build_comparison([])["has_data"] is False)

print("[GROUP] metric registry (source of truth)")
ct("module metrics registered", len(mr.MODULE_METRICS) >= 16)
ct("no formula conflicts with phase 2.2", mr.conflicts() == [])
ct("metric carries full contract", all(k in mr.get_metric("net_sales") for k in
    ("metric_code", "name_ar", "name_en", "definition", "formula", "source", "unit",
     "currency_behavior", "aggregation", "required_fields", "min_quality", "sensitive", "allowed_roles")))
ct("payroll hidden from staff", not mr.can_view("payroll_cost", "staff"))
ct("payroll visible to owner", mr.can_view("payroll_cost", "owner"))
ct("phase 2.2 metrics still reachable", mr.get_metric("gross_margin") is not None)
ct("unknown metric -> None", mr.get_metric("nope") is None)
ct("role filter shrinks list", len(mr.list_metrics(role="staff")) < len(mr.list_metrics()))

print("[GROUP] column mapping")
m = ing.map_columns(["التاريخ", "الفرع", "رقم الفاتورة", "المبيعات", "الخصم", "المرتجعات", "الضريبة"], "sale")
ct("Arabic headers mapped", set(m["mapping"].values()) >= {"date", "branch_id", "reference", "gross_sales", "discounts", "returns", "vat"})
m2 = ing.map_columns(["Date", "Branch", "Invoice No", "Sales", "Discount", "VAT"], "sale")
ct("English headers mapped", set(m2["mapping"].values()) >= {"date", "branch_id", "reference", "gross_sales", "discounts", "vat"})
ct("unknown column reported, not guessed", ing.map_columns(["التاريخ", "الفرع", "المبيعات", "عمود غريب"], "sale")["unmapped"][0]["header"] == "عمود غريب")
ct("missing required reported", "gross_sales" in ing.map_columns(["التاريخ", "الفرع"], "sale")["missing_required"])
ct("employee headers mapped", set(ing.map_columns(["الرقم الوظيفي", "الاسم", "الراتب"], "employee")["mapping"].values()) >= {"employee_code", "name", "monthly_cost"})

print("[GROUP] value parsing")
ct("thousands separator", ing.parse_value("gross_sales", "1,250.50")[0] == 1250.5)
ct("currency suffix", ing.parse_value("gross_sales", "1000 ر.س")[0] == 1000.0)
ct("non-numeric rejected", ing.parse_value("gross_sales", "abc")[1] == "قيمة غير رقمية")
ct("ISO date", ing.parse_value("date", "2026-09-15")[0] == "2026-09-15")
ct("DD/MM/YYYY date", ing.parse_value("date", "15/09/2026")[0] == "2026-09-15")
ct("bad date rejected", ing.parse_value("date", "31/31/2026")[1] == "تاريخ غير صالح")
ct("direction Arabic in", ing.parse_value("direction", "وارد")[0] == "in")
ct("direction English out", ing.parse_value("direction", "OUT")[0] == "out")
ct("unknown direction rejected", ing.parse_value("direction", "xyz")[1] is not None)
ct("empty stays empty (not zero)", ing.parse_value("gross_sales", "")[0] is None)

print("[GROUP] row validation")
rows = [["2026-09-01", "الرياض", "INV-1", "1000", "50", "0", "150"],
        ["2026-09-02", "جدة", "INV-2", "2000", "0", "100", "300"],
        ["2026-09-01", "الرياض", "INV-1", "1000", "50", "0", "150"],
        ["bad", "الرياض", "INV-3", "abc", "", "", ""],
        ["2026-09-03", "فرع مجهول", "INV-4", "500", "", "", ""],
        ["2026-09-04", "الرياض", "INV-5", "-100", "", "", ""]]
mp = ing.map_columns(["التاريخ", "الفرع", "رقم الفاتورة", "المبيعات", "الخصم", "المرتجعات", "الضريبة"], "sale")["mapping"]
v = ing.validate_rows(rows, mp, "sale", branch_names={"الرياض": 1, "جدة": 2})
ct("2 valid rows", v["valid_count"] == 2)
ct("4 rejected rows", v["rejected_count"] == 4)
ct("branch name resolved to id", v["valid"][0]["branch_id"] == 1)
ct("net_sales derived", v["valid"][0]["net_sales"] == 950.0)
ct("duplicate detected", any("مكرر" in e["error"] for r in v["rejected"] for e in r["errors"]))
ct("unknown branch rejected", any("فرع غير معروف" in e["error"] for r in v["rejected"] for e in r["errors"]))
ct("negative value rejected", any("سالبة" in e["error"] for r in v["rejected"] for e in r["errors"]))
ct("row numbers reported", all(r["row"] >= 2 for r in v["rejected"]))
ct("nothing imported by the validator", set(v) == {"valid", "rejected", "total", "valid_count", "rejected_count", "duplicate_keys", "warnings", "warning_count", "extra_columns"})

print("[GROUP] quality gate (existing trust layer)")
q = ing.assess_quality(v, "sale")
ct("quality uses trust layer fields", all(k in q for k in ("overall_score", "status", "checks", "gate")))
ct("gate is one of ALLOW/QUALIFY/BLOCK", q["gate"] in ("ALLOW", "QUALIFY", "BLOCK"))
ct("rejects lower the score", q["overall_score"] < 100 and q["rejected_ratio_pct"] > 0)
clean = ing.validate_rows(rows[:2], mp, "sale", branch_names={"الرياض": 1, "جدة": 2})
ct("clean file scores higher", ing.assess_quality(clean, "sale")["overall_score"] > q["overall_score"])
allbad = ing.validate_rows([rows[3]] * 4, mp, "sale", branch_names={"الرياض": 1})
ct("all-bad file is BLOCK", ing.assess_quality(allbad, "sale")["gate"] == "BLOCK")

print("[GROUP] lineage")
lin = ing.lineage(7, "sales.csv", "سعود", 12, "2026-09", 1)
ct("lineage carries source", all(lin[k] for k in ("dataset_id", "source_type", "source_file", "imported_by", "source_row", "period")))

print("[GROUP] other dataset types")
im = ing.map_columns(["الفترة", "الفرع", "رمز المنتج", "الرصيد"], "inventory")
iv = ing.validate_rows([["2026-09", "الرياض", "SKU1", "40"]], im["mapping"], "inventory", branch_names={"الرياض": 1})
ct("inventory row valid", iv["valid_count"] == 1 and iv["valid"][0]["period"] == "2026-09")
cmap = ing.map_columns(["التاريخ", "المبلغ", "الاتجاه"], "cash_movement")
cv = ing.validate_rows([["2026-09-01", "500", "صادر"]], cmap["mapping"], "cash_movement")
ct("cash movement direction normalised", cv["valid"][0]["direction"] == "out")
pmap = ing.map_columns(["التاريخ", "الفرع", "المورد", "إجمالي التكلفة"], "purchase")
pv = ing.validate_rows([["2026-09-01", "الرياض", "مورد أ", "900"]], pmap["mapping"], "purchase", branch_names={"الرياض": 1})
ct("purchase row valid", pv["valid_count"] == 1 and pv["valid"][0]["supplier_name"] == "مورد أ")

print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
