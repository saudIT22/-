"""Phase 2.9 — People Intelligence tests. Run: python3 test_phase29.py"""
import json, os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hr_engine import analyze_hr
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def E(code, br, dep, role, hire, term=None, cost=None, perf=None, **kw):
    return dict(employee_code=code, name=f"موظف {code}", branch_name=br, department=dep, role=role, hire_date=hire,
                termination_date=term, monthly_cost=cost, performance_rating=perf, **kw)
T = date(2026, 9, 30)
print("[GROUP] honesty")
e = analyze_hr([])
ct("no data → has_data False + requirements", e["has_data"] is False and "رقم الموظف" in e["required"]["required"])
emps = [E("1", "A", "المبيعات", "مندوب", "2020-01-01", cost=8000, perf=4.5, absence_days=2, training_hours=10),
        E("2", "A", "المبيعات", "مندوب", "2025-09-01", cost=7000, perf=2.5, absence_days=12, training_hours=0),
        E("3", "B", "العمليات", "باريستا", "2021-05-01", cost=5000, perf=3.5, training_hours=6, critical_role="نعم", successors=0),
        E("4", "B", "العمليات", "باريستا", "2026-09-10", cost=5200),
        E("5", "B", "العمليات", "باريستا", "2024-01-01", "2026-03-15", cost=5000, termination_type="استقالة"),
        E("6", "B", "العمليات", "باريستا", "2023-01-01", "2026-06-20", cost=5000, termination_type="استقالة"),
        E("7", "A", "المالية", "محاسب", "2019-01-01", cost=12000, perf=90, critical_role=1, successors=2, last_promotion_date="2025-01-01"),
        E("8", "A", "المالية", "محاسب", "2022-01-01", employment_status="مستقيل")]
r = analyze_hr(emps, today=T, period="2026-09")
w = r["workforce"]
print("[GROUP] workforce & turnover")
ct("active headcount at 30 Sep = 5 (leavers & no-date leaver excluded)", w["active"] == 5)
ct("joiners this month = 1 (employee 4)", w["joiners"] == 1 and r["kpis"]["headcount"]["previous"] == 4)
ct("leaver without date reported, not guessed", w["leavers_no_date"] == 1 and "بلا تاريخ" in r["turnover"]["note_ar"])
ct("12-month leavers = 2", r["turnover"]["leavers_12m"] == 2)
ct("voluntary split from termination type", r["turnover"]["voluntary"] == 2)
br = {x["key"]: x for x in r["turnover"]["by_branch"]}
ct("turnover concentrated in branch B", br["B"]["leavers"] == 2 and br["A"]["leavers"] == 0)
ct("turnover method published", "متوسط عدد الموظفين" in r["turnover"]["method_ar"])
ct("headcount trend 12 months", len(w["trend"]) == 12 and w["trend"][-1]["headcount"] == 5)
ct("structure department → roles", any(s["department"] == "العمليات" and s["roles"][0]["role"] == "باريستا" for s in r["structure"]))
print("[GROUP] performance, compensation, learning, succession")
p = r["performance"]
ct("rating 1–5 normalised to 100 (4.5→90)", p["available"] and p["rated"] == 4)
ct("distribution high/average/low", {d["band"]: d["count"] for d in p["distribution"]} == {"high": 2, "average": 1, "low": 1})
c = r["compensation"]
ct("monthly payroll of active employees = 37,200", c["monthly_total"]["value"] == 37200)
ct("change vs previous month (+5,200 new hire)", c["change_pct"] == round(5200 / 32000 * 100, 2) and "عدد الموظفين" in c["change_note_ar"])
ct("learning: avg hours per active employee", r["learning"]["total_hours"] == 16.0 and r["learning"]["avg_per_employee"] == 3.2)
s = r["succession"]
ct("succession: 2 critical roles, 1 without successor, coverage 50%", s["critical_roles"] == 2 and s["without_successor"] == 1 and s["coverage_pct"] == 50.0)
print("[GROUP] flight risk (indicators, not predictions)")
fr = r["flight_risk"]
ct("employee 2: high absence + low performance → flagged", any(x["code"] == "2" for x in fr["employees"]))
ct("flags carry evidence + confidence + disclaimer", all(x["indicators"] and x["confidence"] for x in fr["employees"]) and "ليست تنبؤاً" in fr["disclaimer_ar"])
ct("employee 7 (recent promotion, high perf) not flagged", not any(x["code"] == "7" for x in fr["employees"]))
print("[GROUP] productivity, recruitment, signals")
sales = [{"date": "2026-05-01", "net_sales": 500000, "branch_name": "A"}, {"date": "2026-06-01", "net_sales": 100000, "branch_name": "B"}]
rp = analyze_hr(emps, today=T, period="2026-09", sales_rows=sales)
pr = rp["productivity"]
ct("revenue per employee = 12m sales ÷ avg headcount", pr["revenue_12m"]["value"] == 600000 and abs(pr["revenue_per_employee"]["value"] * pr["avg_headcount"] / 600000 - 1) < 0.02)
ct("profit per employee honestly unavailable", pr["profit_per_employee"] is None and "2.11" in pr["profit_note_ar"])
ct("branch productivity ranking A first", pr["by_branch"][0]["key"] == "A")
ops = [{"title": "مندوب مبيعات", "department": "المبيعات", "opened_date": "2026-08-01", "status": "مفتوحة", "applicants": 20, "offers": 2, "hires": 0},
       {"title": "باريستا", "department": "العمليات", "opened_date": "2026-07-01", "filled_date": "2026-07-24", "hiring_cost": 3000, "hires": 1, "offers": 1}]
rr = analyze_hr(emps, openings=ops, today=T, period="2026-09")["recruitment"]
ct("open positions + time to hire + cost per hire + acceptance", rr["open"] == 1 and rr["time_to_hire_days"] == 23 and rr["cost_per_hire"]["value"] == 3000 and rr["offer_acceptance_pct"] == round(1 / 3 * 100, 2))
ct("no openings file → recruitment unavailable + data-quality note", r["recruitment"]["available"] is False and any(x["code"] == "no_recruitment" for x in r["signals"]))
codes = {x["code"] for x in r["signals"]}
ct("signals: no successor, flight risk, cost increase", {"no_successor", "cost_increase"} <= codes and ("flight_risk" in codes or "flight_risk_high_performers" in codes))
ct("health status rule-based", r["health"]["status"] in ("attention", "critical") and "حرجة" in r["health"]["rule_ar"])
ct("units: branch deep-dive numbers", next(u for u in r["units"]["branches"] if u["key"] == "B")["employees"] == 2)
noperf = analyze_hr([E("1", "A", "x", "y", "2020-01-01")], today=T)
ct("no ratings → performance unavailable + note (not 0%)", noperf["performance"]["available"] is False and any(x["code"] == "no_performance" for x in noperf["signals"]))
ct("no cost → compensation unavailable", noperf["compensation"]["available"] is False)
ct("deterministic ids", [x["id"] for x in analyze_hr(emps, today=T, period="2026-09")["signals"]] == [x["id"] for x in r["signals"]])
ct("no NaN/Infinity", "NaN" not in json.dumps(rp, default=str) and "Infinity" not in json.dumps(rp, default=str))
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hr_engine.py"), encoding="utf-8").read().lower())
print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
