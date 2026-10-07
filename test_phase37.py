"""Phase 3.7 — Performance Prediction tests. Run: python3 test_phase37.py"""
import copy, json, math, os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import drivers_engine as DE
import prediction_engine as PE
import risk_fixture as FX

P = F = 0


def ct(n, c):
    global P, F
    if c:
        P += 1; print(f"PASS: {n}")
    else:
        F += 1; print(f"FAIL: {n}")


ym = lambda i, start="2023-01": PE._next_ym(start, i)
TD = date(2025, 7, 5)

print("[GROUP] 3.7.1/3.7.3 data contract & preparation")
ct("< 3 months → no forecast, explicit reason (not zero)", PE.forecast_series([(ym(0), 10), (ym(1), 12)])["status"] == "insufficient")
f4 = PE.forecast_series([(ym(i), 100 + i) for i in range(4)], today=TD)
ct("3–5 months → forecast labelled insufficient, confidence capped ≤ 50", f4["status"] == "ok" and f4["sufficiency"] == "insufficient" and f4["confidence"]["score"] <= 50)
gap_pts = [(ym(i), 100.0) for i in range(5)] + [(ym(i), 200 + i) for i in range(7, 15)]
fg = PE.forecast_series(gap_pts, today=TD)
ct("gap in history → only consecutive tail used, missing months listed", fg["months_used"] == 8 and ym(5) in fg["missing_months"] and fg["warnings"])
rows = [{"date": "2025-01-15", "net_sales": 100, "branch_name": "A", "reference": "1"}, {"date": "2025-02-10", "net_sales": 50, "branch_name": "A", "reference": "2"},
        {"date": "2025-03-12", "net_sales": 70, "branch_name": "B", "reference": "3"}]
sm = PE.sales_monthly(rows)
ct("partial last month detected (data ends mid-month)", sm["partial_month"] == "2025-03")
ct("monthly series per company and branch", sm["company"]["2025-01"]["revenue"] == 100 and sm["branches"]["B"]["2025-03"]["revenue"] == 70)
ct("partial month excluded from fitting", [m for m, _ in PE.series_of(sm["company"], "revenue", exclude=sm["partial_month"])] == ["2025-01", "2025-02"])

print("[GROUP] 3.7.4 forecast engine")
seas = [(ym(i), 1000 + 20 * i + 150 * math.sin(2 * math.pi * i / 12)) for i in range(36)]
fs = PE.forecast_series(seas[:30], today=TD)
truth = [v for _, v in seas[30:36]]
mape = sum(abs(p["value"] - t) / t for p, t in zip(fs["points"], truth)) / 6 * 100
ct("seasonality applied with ≥ 24 months", fs["seasonality_applied"] and fs["seasonality"])
ct(f"seasonal series forecast close to truth (MAPE {mape:.1f}% < 6%)", mape < 6)
ct("no seasonality below 24 months (stated)", not PE.forecast_series(seas[:18], today=TD)["seasonality_applied"] and "24" in PE.forecast_series(seas[:18], today=TD)["seasonality_ar"])
lin = PE.forecast_series([(ym(i), 100 + 10 * i) for i in range(12)], today=TD)
inc1 = lin["points"][0]["value"] - 210
inc6 = lin["points"][5]["value"] - 210
ct("trend continues upward", lin["points"][5]["value"] > lin["points"][0]["value"] > 210)
ct("trend damped (6-month gain < 6 × first-month gain)", inc6 < 6 * inc1)
spike = [(ym(i), 100 + 5 * i) for i in range(12)]
spike[6] = (spike[6][0], 600)
fsp = PE.forecast_series(spike, today=TD)
ct("outlier flagged and robust slope (Theil–Sen ≈ 5)", spike[6][0] in fsp["outliers"] and abs(fsp["slope_per_month"] - 5) < 1)
ct("backtest computed from history only", fs["backtest"] and fs["backtest"]["n"] > 0 and 1 in fs["backtest"]["by_horizon"])
noisy = PE.forecast_series([(ym(i), 100 + (15 if i % 2 else -15)) for i in range(12)], today=TD)
ct("interval widens with horizon", noisy["points"][5]["upper"] - noisy["points"][5]["lower"] > noisy["points"][0]["upper"] - noisy["points"][0]["lower"])
ct("revenue lower bound never negative", all(p["lower"] >= 0 for p in PE.forecast_series([(ym(i), 10 + (9 if i % 2 else -9)) for i in range(8)], today=TD)["points"]))

print("[GROUP] 3.7.9/3.7.10 confidence & methodology")
ct("confidence decays 4% per month ahead", fs["points"][5]["confidence"] < fs["points"][0]["confidence"] and abs(fs["points"][5]["confidence"] - round(fs["confidence"]["score"] * 0.8)) <= 1)
ct("confidence built from published components", set(fs["confidence"]["components"]) == {"history", "completeness", "recency", "stability", "backtest", "seasonality", "outliers"})
old = PE.forecast_series([(ym(i), 100 + i) for i in range(12)], today=date(2026, 6, 1))
ct("stale data lowers confidence", old["confidence"]["components"]["recency"] < 1 and old["confidence"]["score"] < PE.forecast_series([(ym(i), 100 + i) for i in range(12)], today=date(2024, 1, 15))["confidence"]["score"])
ct("labels by bands", fs["confidence"]["label"] in ("high", "medium", "low"))

print("[GROUP] real fixture: profit, cash, branches, growth")
T = FX.TODAY
D = FX.build(stressed=True); M = FX.run_modules(D)
risk = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T)
drv = DE.analyze_drivers(risk, M, customer_rows=D["sales"], sector="fnb", today=T)
ST = {"targets": {"annual_revenue": 2_500_000}}
z = PE.analyze_prediction(M, sales_rows=D["sales"], risk=risk, drivers=drv, settings=ST, sector="fnb", today=T)
pr = z["profit"]
ct("6-month horizon for sales", len(z["sales"]["points"]) == 6)
ct("profit forecast: revenue × margin − opex − below EBITDA", all(abs(p["net_profit"] - (p["revenue"] * p["gross_margin"] / 100 - p["opex"] - pr["below_ebitda"])) < 1 for p in pr["points"]))
br = sum(x["amount"] for x in pr["bridge"])
ct("profit bridge sums exactly to monthly net change", abs(br - (pr["forecast_avg"]["net_profit"] - pr["now"]["net_profit"])) < 1)
c = z["cash"]
ct("cash path = start + cumulative forecast net", abs(c["points"][-1]["balance"] - (c["start_balance"] + sum(p["net"] for p in c["points"]))) < 1)
ct("current negative balance reported as existing deficit", any(r["code"] == "cash_shortfall" and "قائم" in r["ar"] for r in z["risks"]))
g = z["growth"]
ct("growth decomposition volume + AOV = growth (exact)", abs(sum(x["pct"] for x in g["components"]) - g["growth_pct"]) < 0.05)
ct("branch contributions + residual = growth", abs(sum(b["pct"] for b in g["branches"]) + g["branch_residual_pct"] - g["growth_pct"]) < 0.05)
ct("each branch: current vs month 3 with arrow + confidence", all({"current_avg", "forecast_m3", "arrow", "confidence"} <= set(b) for b in z["branches"] if b["status"] == "ok"))

print("[GROUP] 3.7.11–3.7.14 drivers, gap, future risk, opportunities")
ct("forecast drivers link 3.4 risk drivers with source", any(d["kind"] == "risk_driver" and d.get("link") for d in z["drivers"]))
ga = z["gap"]
ct("year-end projection = YTD actual + remaining forecast", abs(ga["projection"] - (ga["ytd_actual"] + ga["remaining_forecast"])) < 1)
ct("target gap + recovery with remaining gap", ga["gap"] > 0 and ga["recovery"]["remaining_gap"] == round(max(0.0, ga["gap"] - ga["recovery"]["total"]), 2))
rec = PE.target_gap(PE.sales_monthly(D["sales"]), PE.forecast_series(PE.series_of(PE.sales_monthly(D["sales"])["company"], "revenue"), 12, today=T),
                    {}, {"annual_revenue": 1e9}, 1, [{"key": "a", "group": "leak", "amount": 100}, {"key": "b", "group": "leak", "amount": 70}, {"key": "c", "group": "c", "amount": 5}])
ct("overlapping recovery items not double counted", rec["recovery"]["total"] == 105)
ct("no target → stated, no invented gap", PE.target_gap(PE.sales_monthly(D["sales"]), z["sales"], {}, {}, 1)["target"] is None)
ct("future risks carry level + horizon + evidence", z["risks"] and all(r["horizon_ar"] and r["evidence"] for r in z["risks"]))
ct("declining branch flagged with potential loss", any(r["code"].startswith("branch_decline") and r.get("impact") for r in z["risks"]))
ct("opportunities carry range + action", z["opportunities"] and all(o.get("range") is not None for o in z["opportunities"]))

print("[GROUP] 3.7.15/3.7.16 what-if (simulation only)")
base = z["scenario_baseline"]
base_copy = copy.deepcopy(base)
s0 = PE.simulate(base, {})
ct("base scenario reproduces the forecast", abs(s0["revenue"] - pr["total"]["revenue"]) < 1 and abs(s0["net_profit"] - pr["total"]["net_profit"]) < 1)
sp = PE.simulate(base, {"price_pct": 10})
sv = PE.simulate(base, {"volume_pct": 10})
ct("price +10% raises revenue without raising COGS (profit gain = 10% of revenue)", abs((sp["net_profit"] - s0["net_profit"]) - 0.1 * s0["revenue"]) < 1)
ct("volume +10% raises revenue and COGS (gain = 10% of gross profit)", abs((sv["net_profit"] - s0["net_profit"]) - 0.1 * (s0["gross_profit"])) < 1)
sc = PE.simulate(base, {"purchase_cost_pct": -5})
cogs = s0["revenue"] - s0["gross_profit"]
ct("purchase cost −5% adds 5% of COGS to profit", abs((sc["net_profit"] - s0["net_profit"]) - 0.05 * cogs) < 1)
sl = PE.simulate(base, {"leakage_recovery": 60000})
ct("leakage recovery adds exactly its amount", abs((sl["net_profit"] - s0["net_profit"]) - 60000) < 1)
ct("simulation never mutates baseline data", base == base_copy and s0["is_simulation"])
_, errs = PE.validate_assumptions({"volume_pct": 500, "purchase_cost_pct": "x"})
ct("out-of-range / non-numeric assumptions rejected", len(errs) == 2)
ct("preset scenarios: base, growth, cost, leakage, combined", [x["code"] for x in z["scenarios"]] == ["base", "growth", "cost", "leakage", "combined"])
ct("scenario comparison deltas vs base", all("delta_profit" in x for x in z["scenarios"]) and z["scenarios"][0]["delta_profit"] == 0)

print("[GROUP] 3.7.17–3.7.20 accuracy, history, decisions before/after")
stored = [{"metric": "revenue", "scope": "company", "period": "2025-01", "h": 1, "value": 110, "lower": 100, "upper": 120, "made_on": "2024-12-31"},
          {"metric": "revenue", "scope": "company", "period": "2025-02", "h": 2, "value": 90, "lower": 85, "upper": 95, "made_on": "2024-12-31"},
          {"metric": "revenue", "scope": "B", "period": "2025-01", "h": 1, "value": 50, "lower": 40, "upper": 60, "made_on": "2024-12-31"},
          {"metric": "revenue", "scope": "company", "period": "2025-03", "h": 3, "value": 100, "lower": 90, "upper": 110, "made_on": "2024-12-31"}]
acts = {("revenue", "company"): {"2025-01": 100, "2025-02": 100}, ("revenue", "B"): {"2025-01": 80}}
a = PE.accuracy(stored, acts)
ct("MAPE/bias/in-range from stored forecasts vs actuals", a["by_metric"]["revenue"]["n"] == 3 and a["overall"]["in_range_pct"] == 33)
ct("accuracy by horizon and by branch", 1 in a["by_horizon"] and "B" in a["by_branch"] and a["by_branch"]["B"]["accuracy"] < 70)
ct("future period not scored before it happens", all(r["period"] != "2025-03" for r in a["table"]))
ct("forecast vs actual table (h=1)", a["table"][0]["period"] == "2025-01" and a["table"][0]["actual"] == 100)
md = PE.measure_decisions([{"id": 1, "created_on": "2025-01-10", "metric": "revenue"}], stored, acts)
ct("before/after vs baseline forecast made before decision", md[0]["status"] == "measured" and md[0]["difference"] == 10 and "لا تُثبت" in md[0]["note_ar"])
ct("decision without prior forecast → no baseline (no invented comparison)", PE.measure_decisions([{"id": 2, "created_on": "2024-01-01"}], stored, acts)[0]["status"] == "no_baseline")
ct("snapshot holds predictions to persist (company, branches, profit)", {p["scope"] for p in z["snapshot"]["predictions"]} >= {"company"} and any(p["metric"] == "net_profit" for p in z["snapshot"]["predictions"]))

print("[GROUP] 3.7.21–3.7.25 sectors, RBAC, data quality, brief")
ct("11 sectors configured", all(k in PE.SECTOR_PRED for k in ("fnb", "retail", "ecommerce", "manufacturing", "contracting", "distribution", "services", "clinics", "hospitals", "logistics", "other")))
zc = PE.analyze_prediction(M, sales_rows=D["sales"], risk=risk, drivers=drv, sector="clinics", today=T)
ct("healthcare labels orders as visits", zc["sector"]["metrics"][0]["ar"] == "الزيارات")
ct("unavailable sector KPI says so (not zero)", any(m["forecast"]["status"] == "insufficient" for m in z["sector"]["metrics"] if m["key"].startswith("sector:")))
zm = PE.analyze_prediction(M, sales_rows=D["sales"], risk=risk, drivers=drv, today=T, categories=["operational", "customer"])
ct("manager scope: profit & cash restricted, not in snapshot", zm["profit"]["status"] == "restricted" and zm["cash"]["status"] == "restricted"
   and not any(p["metric"] == "net_profit" for p in zm["snapshot"]["predictions"]) and zm["scenarios"] == [])
few = [r for r in D["sales"] if r["date"] >= "2026-08-01"]
zf = PE.analyze_prediction({}, sales_rows=few, today=T)
ct("2 months of data → insufficient message, no forecast", zf["status"] == "insufficient" and "غير كافية" in zf["message_ar"])
ct("executive brief: path + confidence + causes + gap + scenario (deterministic)", z["brief"]["headline"].startswith("إذا استمرت") and len(z["brief"]["lines"]) >= 4)
ct("interventions: prediction → cause → impact → action → owner → due → measurement", z["plan"] and all({"cause", "action_ar", "owner_ar", "due", "measurement_ar"} <= set(p) for p in z["plan"]))
ct("methodology transparent (model, steps, backtest, updated)", z["methodology"]["model"] == PE.MODEL and len(z["methodology"]["steps_ar"]) >= 6)
ct("deterministic", json.dumps(PE.analyze_prediction(M, sales_rows=D["sales"], risk=risk, drivers=drv, settings=ST, sector="fnb", today=T), sort_keys=True, ensure_ascii=False)
   == json.dumps(z, sort_keys=True, ensure_ascii=False))
ct("JSON-serializable", bool(json.dumps(z, ensure_ascii=False)))
src = open(PE.__file__, encoding="utf-8").read() if os.path.exists(PE.__file__) else ""
ct("engine never touches AI", "ai_gateway" not in src and "Gemini" not in src)
print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
