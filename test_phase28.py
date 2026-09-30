"""Phase 2.8 — Cash Flow Intelligence tests. Run: python3 test_phase28.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cashflow_engine import analyze_cashflow, classify
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def M(d, dr, amt, typ, br="جدة", acc="الراجحي", bal=None, cp=None):
    return {"date": d, "direction": dr, "amount": amt, "movement_type": typ, "branch_name": br, "account": acc, "balance": bal, "counterparty": cp}

print("[GROUP] honesty & classification")
e = analyze_cashflow([])
ct("no data → has_data False + requirements", e["has_data"] is False and e["required"]["required"])
ct("classify collections", classify({"direction": "in", "movement_type": "تحصيل فاتورة عميل"}) == "collections")
ct("classify payroll", classify({"direction": "out", "movement_type": "رواتب شهر يوليو"}) == "payroll")
ct("classify supplier via counterparty", classify({"direction": "out", "movement_type": "تحويل", "counterparty": "مورد البن"}) == "suppliers")
ct("unknown → other (no guessing)", classify({"direction": "out", "movement_type": "تحويل داخلي"}) == "other_out")

mv = []
for m, (col, sup, sal) in enumerate([(100, 40, 30), (110, 45, 30), (120, 50, 30), (115, 60, 30), (130, 55, 30), (125, 70, 30)], start=3):
    ym = f"2026-{m:02d}"
    mv += [M(f"{ym}-05", "in", col * 1000, "تحصيل مبيعات"), M(f"{ym}-10", "out", sup * 1000, "دفعة مورد"),
           M(f"{ym}-25", "out", sal * 1000, "رواتب"), M(f"{ym}-26", "out", 10000, "إيجار", br="الرياض")]
mv.append(M("2026-08-28", "in", 1000, "فائدة", bal=500000))            # الرصيد المُبلّغ في آخر حركة
r = analyze_cashflow(mv, settings={"current_liabilities": 200000, "min_cash": 100000})
f = r["flows"]
print("[GROUP] position, flows, drivers")
ct("current period = last month (2026-08)", r["period"] == "2026-08")
ct("inflow/outflow/net for the period", f["inflow"]["value"] == 126000 and f["outflow"]["value"] == 110000 and f["net"]["value"] == 16000)
ct("balance from reported statement balance", r["position"]["balance"]["value"] == 500000 and r["position"]["balance_source"] == "reported")
ct("previous month-end balance = balance − later net", r["position"]["previous"]["value"] == 500000 - 16000)
ct("change vs previous", r["position"]["change"]["value"] == 16000)
dk = {d["key"]: d for d in r["drivers"]}
ct("drivers: collections / suppliers / payroll / rent", {"collections", "suppliers", "payroll", "rent"} <= set(dk))
ct("driver delta vs previous (suppliers 70k vs 55k)", dk["suppliers"]["delta"]["value"] == 15000 and dk["suppliers"]["change_pct"] == round(15000 / 55000 * 100, 2))
ct("driver drill-down items", dk["suppliers"]["top_items"][0]["amount"]["value"] == 70000)
ct("variance main cause = suppliers (−15k on net)", "مدفوعات الموردين" in r["variance"]["main_cause_ar"])
ct("budget comparison honestly unavailable", r["variance"]["budget"]["available"] is False)
nob = analyze_cashflow([x for x in mv if x.get("balance") is None])
ct("no balance anywhere → unavailable + how to fix", nob["position"]["balance"] is None and "الرصيد الافتتاحي" in nob["position"]["balance_reason_ar"])
ob = analyze_cashflow([x for x in mv if x.get("balance") is None], settings={"opening_balance": 100000})
ct("opening balance + net movements", ob["position"]["balance"]["value"] == 100000 + sum((x["amount"] if x["direction"] == "in" else -x["amount"]) for x in mv if x.get("balance") is None))

print("[GROUP] forecast (median method, confidence, sufficiency)")
fc = r["forecast"]
ct("12-month forecast available", fc["available"] and len(fc["points"]) == 12)
ct("uses last 5 full months (Mar–Jul) medians", fc["basis_months"] == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07"])
ct("expected net = median(in) − median(out) = 115k − 100k... ", fc["expected_net_monthly"]["value"] == 115000 - (50000 + 30000 + 10000))
ct("balance path starts from current balance", fc["points"][0]["balance"]["value"] == 500000 + 25000)
ct("marked estimate + method published", fc["is_estimate"] and "وسيط" in fc["method_ar"])
short = analyze_cashflow(mv[:8])
ct("<3 months → forecast unavailable with reason", short["forecast"]["available"] is False and "3 أشهر" in short["forecast"]["reason_ar"])

print("[GROUP] liquidity indicators")
lq = {x["code"]: x for x in r["liquidity"]}
ct("current & quick ratio need liabilities → computed when given", lq["quick_ratio"]["value"] == 2.5)
nl = {x["code"]: x for x in analyze_cashflow(mv)["liquidity"]}
ct("no liabilities → ratios unavailable with need (not 0)", nl["current_ratio"]["value"] is None and "الالتزامات" in nl["current_ratio"]["needs_ar"])
ct("positive net → no runway/burn, explained", lq["runway"]["value"] is None and "لا استنزاف" in lq["runway"]["needs_ar"])
burnmv = [M(f"2026-0{m}-05", "out", 50000, "رواتب") for m in range(3, 9)] + [M(f"2026-0{m}-06", "in", 20000, "تحصيل") for m in range(3, 9)]
burnmv[-1]["balance"] = 90000
b = {x["code"]: x for x in analyze_cashflow(burnmv)["liquidity"]}
ct("burn 30k/month → runway = 90k ÷ 30k = 3 months", b["cash_burn"]["value"] == 30000 and b["runway"]["value"] == 3.0)
ct("short runway signal", any(s["code"] == "short_runway" for s in analyze_cashflow(burnmv)["signals"]))

print("[GROUP] receivables, DSO, aging, 14-day pressure")
ar = [{"invoice_date": "2026-06-01", "due_date": "2026-07-01", "amount": 40000, "customer_name": "شركة أ"},
      {"invoice_date": "2026-07-15", "due_date": "2026-08-14", "amount": 30000, "paid_amount": 30000, "paid_date": "2026-08-10", "customer_name": "شركة ب"},
      {"invoice_date": "2026-08-10", "due_date": "2026-09-09", "amount": 50000, "customer_name": "شركة ج"}]
rc = analyze_cashflow(mv, receivables=ar)
c = rc["collections"]
ct("open receivables = 40k + 50k", c["total_receivables"]["value"] == 90000)
ct("overdue = 40k (58 days past due at 28 Aug)", c["overdue"]["value"] == 40000 and next(a for a in c["aging"] if a["bucket"] == "31–60")["amount"]["value"] == 40000)
ct("DSO = open ÷ billed(90d) × 90", c["dso"] == round(90000 / 120000 * 90))
ct("collected this period", c["collected_this_period"]["value"] == 30000)
ct("overdue collection opportunity", any(s["code"] == "collect_overdue" for s in rc["signals"]))
ct("no receivables → data-quality note, not zero DSO", r["collections"] is None and any(s["code"] == "no_receivables" for s in r["signals"]))
pr = analyze_cashflow(mv, settings={"min_cash": 600000})
ct("14-day projected balance below minimum → high pressure signal", any(s["code"] == "cash_pressure" and s["severity"] == "high" for s in pr["signals"]))
ct("status critical when a high risk exists", pr["status"]["status"] == "critical")

print("[GROUP] branches, cross-module, safety")
br = {x["branch"]: x for x in r["branches"]}
ct("branch flows only from assigned movements", br["الرياض"]["outflow"]["value"] == 10000 and br["الرياض"]["net"]["value"] == -10000)
ct("negative branch signal", any(s["code"] == "branch_negative" and s["dimension"] == "الرياض" for s in r["signals"]))
x = analyze_cashflow(mv, sales_rows=[{"date": "2026-08-05", "net_sales": 150000}, {"date": "2026-07-05", "net_sales": 100000}],
                     inventory_value=120, inventory_value_prev=100, purchases_change_pct=14.0)
ct("cross-module: sales +50%, inventory +20%, purchases +14%", x["cross"]["sales_change_pct"] == 50.0 and x["cross"]["inventory_change_pct"] == 20.0 and x["cross"]["purchases_change_pct"] == 14.0)
ct("cash conversion = collections ÷ sales", {q["code"]: q for q in x["liquidity"]}["cash_conversion"]["value"] == round(125000 / 150000 * 100, 2))
ct("sales are not cash (wording)", "ليست نقداً" in x["cross"]["note_ar"])
ct("deterministic ids", [s["id"] for s in analyze_cashflow(mv)["signals"]] == [s["id"] for s in analyze_cashflow(mv)["signals"]])
ct("no NaN/Infinity", "NaN" not in json.dumps(rc, default=str) and "Infinity" not in json.dumps(rc, default=str))
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cashflow_engine.py"), encoding="utf-8").read().lower())
print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
