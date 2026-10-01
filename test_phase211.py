"""Phase 2.11 — Financial Unit tests. Run: python3 test_phase211.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../phase21"))
from finance_engine import analyze_finance, build_expense_lines, classify_expense
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def S(d, br, sku, cat, q, gross, disc=0, ret=0, ch="نقاط البيع"):
    return {"date": d, "branch_name": br, "product_sku": sku, "category": cat, "quantity": q, "gross_sales": gross, "discount": disc, "returns": ret, "channel": ch}
print("[GROUP] honesty & classification")
ct("no sales → has_data False", analyze_finance([])["has_data"] is False)
ct("expense classification", classify_expense("رواتب شهر 7") == "payroll" and classify_expense("فاتورة كهرباء") == "utilities" and classify_expense("زكاة") == "tax" and classify_expense("شيء") == "other")
cash = [{"date": "2026-08-01", "direction": "out", "amount": 5000, "movement_type": "إيجار الفرع", "branch_name": "A"},
        {"date": "2026-08-27", "direction": "out", "amount": 20000, "movement_type": "رواتب شهر 8"},
        {"date": "2026-08-15", "direction": "out", "amount": 30000, "movement_type": "دفعة مورد — PO-1", "counterparty": "مورد البن"},
        {"date": "2026-08-20", "direction": "out", "amount": 3000, "movement_type": "مصروف تسويق"},
        {"date": "2026-08-05", "direction": "in", "amount": 90000, "movement_type": "إيداع مبيعات"},
        {"date": "2026-07-01", "direction": "out", "amount": 5000, "movement_type": "إيجار الفرع", "branch_name": "A"},
        {"date": "2026-07-27", "direction": "out", "amount": 20000, "movement_type": "رواتب شهر 7"},
        {"date": "2026-07-20", "direction": "out", "amount": 1000, "movement_type": "مصروف تسويق"}]
lines = build_expense_lines(None, cash)
ct("supplier payments excluded from OpEx (they are COGS) + inflows ignored", sum(1 for x in lines if x["ym"] == "2026-08") == 3)
exp_file = [{"date": "2026-08-10", "amount": 4000, "category": "إيجار", "branch_name": "A"}]
mixed = build_expense_lines(exp_file, cash)
ct("expense file wins for its month (no double counting with cash)", [x["source"] for x in mixed if x["ym"] == "2026-08"] == ["expense"] and any(x["source"] == "cash" for x in mixed if x["ym"] == "2026-07"))
sales = []
for m, mult in (("2026-06", 0.9), ("2026-07", 1.0), ("2026-08", 1.1)):
    sales += [S(f"{m}-05", "A", "لاتيه", "مشروبات", 1000 * mult, 21000 * mult, 1000 * mult), S(f"{m}-06", "B", "كيك", "حلويات", 500 * mult, 8500 * mult, 0, 500 * mult)]
cost = {"لاتيه": 7, "كيك": 6}
emps = [{"monthly_cost": 12000, "hire_date": "2020-01-01"}, {"monthly_cost": 9000, "hire_date": "2021-01-01"}]
r = analyze_finance(sales, cost_map=cost, expense_lines=lines, employees=emps, cash={"balance": 150000, "net_by_month": {"2026-08": 32000}},
                    receivables_total=20000, inventory_value=40000, inventory_value_prev=36000,
                    settings={"balance": {"accounts_payable": 25000, "fixed_assets": 100000, "paid_in_capital": 200000}})
st = {x["key"]: x for x in r["statement"]}
print("[GROUP] P&L")
ct("net revenue = gross − discounts − returns", st["net_revenue"]["actual"] == round((21000 + 8500 - 1000 - 500) * 1.1, 2))
ct("COGS = qty × product cost", st["cogs"]["actual"] == round((1000 * 7 + 500 * 6) * 1.1, 2))
ct("gross profit + margin", st["gross_profit"]["actual"] == round(st["net_revenue"]["actual"] - st["cogs"]["actual"], 2))
ct("OpEx from cash basis: rent + payroll + marketing = 28,000", st["opex_total"]["actual"] == 28000.0)
ct("EBITDA = GP − OpEx", st["ebitda"]["actual"] == round(st["gross_profit"]["actual"] - 28000, 2))
ct("net profit flagged incomplete (no depreciation/interest), not invented", r["quality"]["net_profit_missing"] == ["الإهلاك", "الفوائد"] and any(s["code"] == "net_profit_incomplete" for s in r["signals"]))
ct("variance vs previous month per line", st["net_revenue"]["var_prev_pct"] == 10.0 and st["marketing"]["var_prev_pct"] == 200.0)
v = r["variance"]
ct("variance contributions explain net-profit change", v and abs(sum(c["effect"] for c in v["contributions"]) - v["net_profit_change"]) < 0.05)
rd = analyze_finance(sales, cost_map=cost, expense_lines=lines, settings={"depreciation_monthly": 1000, "interest_monthly": 500})
ct("manual depreciation & interest complete net profit", {x["key"]: x for x in rd["statement"]}["net_profit"]["actual"] == round({x["key"]: x for x in rd["statement"]}["ebitda"]["actual"] - 1500, 2) and not rd["quality"]["net_profit_missing"])
print("[GROUP] balance sheet & ratios")
bs = r["balance_sheet"]
ct("assets from modules (cash 2.8, receivables, inventory 2.6) + manual", bs["total_assets"] == 150000 + 20000 + 40000 + 100000)
ct("A = L + E check shows difference and missing items (not hidden)", bs["balanced"] is False and bs["difference"] != 0 and "القروض" in bs["missing"])
ra = {x["code"]: x for x in r["ratios"]}
ct("gross margin ratio", ra["gross_margin"]["value"] == round(st["gross_profit"]["actual"] / st["net_revenue"]["actual"] * 100, 1))
ct("current ratio = (cash+AR+inv)/payables", ra["current_ratio"]["value"] == round(210000 / 25000, 2))
ct("ROA unavailable while balance sheet incomplete (with reason)", ra["roa"]["value"] is None and ra["roa"]["needs_ar"])
ct("interest coverage unavailable without interest", ra["interest_coverage"]["available"] is False)
ct("DSO days from receivables", ra["dso"]["value"] == round(20000 * 30 / st["net_revenue"]["actual"]))
print("[GROUP] revenue, expenses, branches, cash")
ct("revenue by category with share + margin", r["revenue"]["by_category"][0]["key"] == "مشروبات" and r["revenue"]["by_category"][0]["gross_margin"] is not None)
ct("expense drivers + % of revenue", r["expenses"]["lines"] and r["expenses"]["lines"][0]["pct_of_revenue"] is not None)
ct("payroll reconciliation with HR (recorded 20k vs file 21k)", r["expenses"]["payroll_reconciliation"]["hr_estimate"] == 21000.0)
b = {x["key"]: x for x in r["branches"]}
ct("branch contribution = GP − direct opex (no allocation)", b["A"]["direct_opex"] == 5000.0 and b["A"]["contribution"] == round(b["A"]["gross_profit"] - 5000, 2) and r["central_opex"] == 23000.0)
ct("profit vs cash conversion", r["cash_analysis"]["cash_conversion_pct"] is not None)
print("[GROUP] trends, forecast, signals, what-if")
ct("monthly trends with margins", len(r["trends"]) == 3 and r["trends"][-1]["gross_margin"] is not None)
ct("quarterly grain", analyze_finance(sales, cost_map=cost, grain="quarter")["trends"][0]["period"] == "2026-Q2")
ct("forecast available with method, confidence, sufficiency", r["forecast"]["available"] and r["forecast"]["confidence"] == "low" and "12 شهراً" in r["forecast"]["sufficiency"])
ct("<3 months → forecast unavailable with reason", analyze_finance(sales[-4:], cost_map=cost)["forecast"]["available"] is False)
ct("marketing spike signal", any(s["code"] == "expense_spike" and s["dimension"] == "التسويق" for s in r["signals"]))
ct("missing cost → COGS unavailable (not zero) + data-quality signal", analyze_finance(sales)["kpis"][1]["current"] is None and any(s["code"] == "no_cogs" for s in analyze_finance(sales)["signals"]))
ct("what-if base provided (simulation only)", r["whatif_base"]["revenue"] == st["net_revenue"]["actual"] and r["whatif_base"]["payroll"] == 20000.0)
ct("budget variance when budget entered", analyze_finance(sales, cost_map=cost, expense_lines=lines, settings={"budget": {"opex_monthly": {"marketing": 1000}}})["statement"] and any(s["code"] == "budget_overrun" for s in analyze_finance(sales, cost_map=cost, expense_lines=lines, settings={"budget": {"opex_monthly": {"marketing": 1000}}})["signals"]))
ct("deterministic ids", [s["id"] for s in analyze_finance(sales, cost_map=cost, expense_lines=lines)["signals"]] == [s["id"] for s in analyze_finance(sales, cost_map=cost, expense_lines=lines)["signals"]])
ct("no NaN/Infinity", "NaN" not in json.dumps(r, default=str) and "Infinity" not in json.dumps(r, default=str))
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance_engine.py"), encoding="utf-8").read().lower())
print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
