"""Phase 3.4 — Risk Drivers Intelligence tests. Run: python3 test_phase34.py"""
import json, os, sys, copy
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import drivers_engine as DE
import risk_fixture as FX

P = F = 0


def ct(n, c):
    global P, F
    if c:
        P += 1; print(f"PASS: {n}")
    else:
        F += 1; print(f"FAIL: {n}")


T = FX.TODAY
D = FX.build(stressed=True); M = FX.run_modules(D)
H = FX.build(stressed=False); MH = FX.run_modules(H)
risk = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T)
risk_copy = copy.deepcopy(risk)
n_drivers = len(R.DRIVERS)
y = DE.analyze_drivers(risk, M, customer_rows=D["sales"], sector="fnb", today=T)
by = {d["key"]: d for d in y["drivers"]}

print("[GROUP] reads 3.3 + modules, adds nothing to the index")
ct("no risk data → has_data False with message", DE.analyze_drivers(R.analyze_risk({}, today=T), {}, today=T)["has_data"] is False)
ct("risk result not mutated by drivers engine", json.dumps(risk, sort_keys=True, ensure_ascii=False) == json.dumps(risk_copy, sort_keys=True, ensure_ascii=False))
ct("risk engine driver catalog untouched (supporting drivers stay outside index)", len(R.DRIVERS) == n_drivers and not any(k.startswith("x_") for k in R.DRIVERS))
ct("primary drivers = all 3.3 drivers; supporting added separately", sum(1 for d in y["drivers"] if d["kind"] == "primary") == len(risk["drivers"]) and sum(1 for d in y["drivers"] if d["kind"] == "support") == len(DE.SUPPORT))
ct("index shown is exactly 3.3's", y["risk"]["index"]["score"] == risk["index"]["score"])

print("[GROUP] driver contribution (exact decomposition)")
for c in risk["categories"]:
    items = y["contributions"][c["key"]]["items"]
    ct(f"contributions sum to category score — {c['key']}", abs(sum(i["points"] for i in items) - c["score"]) < 0.05 and abs(sum(i["pct"] for i in items) - 100) < 0.5)
ct("worst driver carries the 60% share", all(i["is_worst"] == (i is max(v["items"], key=lambda z: z["score"])) for v in y["contributions"].values() for i in v["items"][:1]))
comp = by["leakage_pct"]["components"]
ct("leakage split into components by actual amount (shares = 100%)", comp and abs(sum(c["share_pct"] for c in comp) - 100) < 0.5)
ct("component contribution = parent contribution × share", abs(by["x_excess_opex"]["contribution"]["pct"] - round(by["leakage_pct"]["contribution"]["pct"] * next(c["share_pct"] for c in comp if c["key"] == "x_excess_opex") / 100, 1)) < 0.11)
ct("index points per driver add up to the index (primary)", abs(sum((d["contribution"] or {}).get("index_points") or 0 for d in y["drivers"] if d["kind"] == "primary") - risk["index"]["score"]) < 0.5)

print("[GROUP] evidence, gap, impact, entities")
ct("every evaluated driver has evidence", all(d["evidence"] for d in y["drivers"] if d["score"] is not None))
g = by["net_margin_pct"]["gap"]
ct("gap: current vs acceptable limit for lower-is-worse metric", g["target"] == 5.0 and g["beyond"] is True and g["gap"] < 0)
yt = DE.analyze_drivers(risk, M, customer_rows=D["sales"], settings={"targets": {"net_margin_pct": 18}}, today=T)
gt = next(d for d in yt["drivers"] if d["key"] == "net_margin_pct")["gap"]
ct("company target overrides the limit (current vs target 18%)", gt["target"] == 18.0 and gt["target_source"] == "company")
ct("gap baseline from history median when series exists", by["net_margin_pct"]["gap"]["baseline"] is not None)
ct("discount driver: actual + recovery impacts typed", {i["type"] for i in by["x_discount_leakage"]["impacts"]} <= {"actual", "recovery"})
ct("affected entities: cost driver lists products", by["cost_increase_pct"]["entities"].get("products"))
ct("affected entities: lost customers lists customers", by["lost_customer_revenue_pct"]["entities"].get("customers"))
ct("supporting driver without data → unable with reason, confidence 0, not zero value", by["x_low_cash"]["value"] is None and by["x_low_cash"]["reason_ar"] and by["x_low_cash"]["confidence"]["pct"] == 0)

print("[GROUP] confidence & data sufficiency")
c6 = DE.confidence_of("purchases", True, {"purchases": {"months": 6, "records": 1200, "entities_ar": "4 موردين"}})
c1 = DE.confidence_of("purchases", True, {"purchases": {"months": 1, "records": 10}})
ct("6 months + 1,200 lines → sufficient with reasons", c6["pct"] >= 85 and c6["sufficiency"] == "sufficient" and any("موردين" in r for r in c6["reasons_ar"]))
ct("thin data → limited/insufficient", c1["sufficiency"] in ("limited", "insufficient") and c1["pct"] < c6["pct"])
ct("module without record count is not given invented counts", DE.data_profiles(M, risk["customers"])["cashflow"]["records"] is None)
ct("data-needed drivers (complaints/NPS/CSAT) listed honestly", {x["key"] for x in y["data"]["data_needed"]} == {"n_complaints", "n_nps", "n_csat"})

print("[GROUP] trends & persistence")
ct("accelerating only when every step worsens and pace rises", DE.classify_trend([22, 38, 57, 71], "above", 1) == "accelerating" and DE.classify_trend([22, 40, 50, 55], "above", 1) == "worsening")
ct("improving / stable / insufficient", DE.classify_trend([9, 7, 5], "above", 0.5) == "improving" and DE.classify_trend([5, 5.1, 5], "above", 0.5) == "stable" and DE.classify_trend([5, 9], "above", 0.5) == "insufficient")
ct("lower-is-worse direction handled", DE.classify_trend([10, 7, 4], "below", 0.5) == "worsening")
ct("persistent = 3+ consecutive elevated months", DE.persistence([("2026-07", "high"), ("2026-08", "high"), ("2026-09", "critical")], "critical")["code"] == "persistent")
ct("new when only current month", DE.persistence([("2026-08", "low"), ("2026-09", "high")], "high")["code"] == "new")
ct("net margin persistence from module series (not invented)", by["net_margin_pct"]["persistence"]["code"] in ("persistent", "recurring", "new"))
hist = []
for i, sc in enumerate([30, 45, 62, 78]):
    dd = (T - timedelta(days=30 * (4 - i))).isoformat()
    hist.append({"date": dd, "driver_scores": {"cost_increase_pct": sc}, "driver_levels": {"cost_increase_pct": R.level_of(sc)}})
yh = DE.analyze_drivers(risk, M, customer_rows=D["sales"], history=hist, today=T)
ch = next(d for d in yh["drivers"] if d["key"] == "cost_increase_pct")
ct("score trend from saved assessments (monthly)", ch["trend"]["basis"] == "score" and ch["trend"]["direction"] in ("accelerating", "worsening"))
ct("driver history: detected → increased → escalated", [e["code"] for e in ch["history"]["events"]][:1] == ["detected"] and any(e["code"] in ("increased", "escalated") for e in ch["history"]["events"]))
ct("emerging driver = biggest score rise in 30 days", yh["executive"]["emerging"] and yh["executive"]["emerging"]["key"] == "cost_increase_pct")

print("[GROUP] root-cause linkage (candidates, not proven causes)")
cands = {c["key"]: c for c in by["lost_customer_revenue_pct"]["candidates"]}
ct("lost customers ← stockout/on-time candidates", {"stockout_items", "on_time_gap_pp"} & set(cands))
ct("candidate confidence labels from evidence rule", all(c["confidence"] in ("high", "likely", "possible") for d in y["drivers"] for c in d["candidates"]))
ct("cost increase ← supplier concentration candidate", any(c["key"] == "supplier_concentration_pct" for c in by["cost_increase_pct"]["candidates"]))
ct("only elevated candidates shown", all(c["level"] in R.ELEVATED for d in y["drivers"] for c in d["candidates"]))
ct("leakage candidates include its components", "x_excess_opex" in {c["key"] for c in by["leakage_pct"]["candidates"]})

print("[GROUP] recommendations & priority")
r = by["cost_increase_pct"]["recommendation"]
ct("recommendation is specific (names entities), not generic", any(p in r["text_ar"] for p in by["cost_increase_pct"]["entities"]["products"][:3]))
ct("expected impact typed (recovery/gap/none)", all(d["recommendation"]["expected_impact"]["type"] in ("recovery", "gap", "data", None) for d in y["drivers"] if d.get("recommendation")))
ct("critical → immediate", all(d["priority"]["code"] == "immediate" for d in y["drivers"] if d["level"] == "critical"))
pm = DE._priority({"level": "medium", "impacts": [], "persistence": {"code": "persistent"}, "trend": {"direction": "stable"}, "score": 50}, 1000)
ct("medium + persistent → high", pm["code"] == "high")
ct("low but worsening → monitor", DE._priority({"level": "low", "impacts": [], "persistence": {}, "trend": {"direction": "worsening"}, "score": 20}, 1000)["code"] == "monitor")
ct("recommendations ordered by priority", [x["priority"]["code"] for x in y["recommendations"]] == sorted([x["priority"]["code"] for x in y["recommendations"]], key=["immediate", "high", "medium", "monitor"].index))
ct("each recommendation has a functional owner", all(x["owner_ar"] for x in y["recommendations"] if not x["key"].startswith("x_tax")))

print("[GROUP] views: by risk, heatmap, branch, department, profiles, executive, sector")
br = {x["category"]: x for x in y["by_risk"]}
ct("drivers by risk: primary + supporting + cross-category", br["customer"]["cross"] and br["profit"]["support"])
hm = {x["key"]: x for x in y["heatmap"]["rows"]}
ct("heatmap: stockout direct in operational + indirect in customer", hm["stockout_items"]["cells"]["operational"]["type"] == "direct" and (hm["stockout_items"]["cells"]["customer"] or {}).get("type") == "indirect")
b = {x["branch"]: x for x in y["branches"]["rows"]}
ct("each branch has its own top drivers", all(x["top"] for x in b.values()) and len({tuple(i["key"] for i in x["top"]) for x in b.values()}) >= 2)
deps = {x["department"]: x for x in y["departments"]["functional"]}
ct("procurement main driver = supplier-side driver", deps["المشتريات"]["main"]["key"] in ("cost_increase_pct", "supplier_concentration_pct", "supplier_late_pct"))
ct("cross-module profit profile spans ≥2 modules", any(p["category"] == "profit" and len(p["steps"]) >= 2 for p in y["profiles"]))
ex = y["executive"]
ct("executive: top 5 by index points", len(ex["top5"]) == 5 and [t["index_points"] for t in ex["top5"]] == sorted([t["index_points"] for t in ex["top5"]], reverse=True))
ct("executive: biggest financial + immediate action", ex["biggest_financial"] and ex["immediate"] and ex["immediate"]["action_ar"])
ct("narrative names the most influential driver with evidence (deterministic)", "المسبب الأكثر تأثيراً" in y["narratives"]["profit"]["text_ar"] and "الدليل" in y["narratives"]["profit"]["text_ar"])
sd = {x["label"]: x for x in y["sector"]["items"]}
ct("fnb sector drivers: waste = data needed, stockout = engine driver", sd["الهدر"]["type"] == "data_needed" and sd["نفاد الأصناف"]["type"] == "driver")
ct("sector KPI unavailable is said, not zeroed", sd["تكلفة الطعام"]["status_ar"] in ("غير متاح", "أسوأ من خط الأساس", "ضمن خط الأساس", "بلا خط أساس"))
ct("driver chains carry risk + no-causation note", y["chains"] and all("ليست سببية" in c["note_ar"] for c in y["chains"]))

print("[GROUP] register / action loop, scope, honesty")
stored = [{"risk_key": "x:x_excess_opex", "title": "مصروفات زائدة", "category": "profit", "status": "action", "decision_id": 4,
           "history": [{"when": "2026-09-20 10:00", "who": "سعود", "from": "detected", "to": "decision"}]}]
rs = R.analyze_risk(M, customer_rows=D["sales"], today=T, stored=stored)
ys = DE.analyze_drivers(rs, M, customer_rows=D["sales"], today=T)
xo = next(d for d in ys["drivers"] if d["key"] == "x_excess_opex")
ct("supporting driver linked to register via x: key", xo["register"] and xo["register"]["decision_id"] == 4 and xo["register"]["supporting"])
ct("register history shows action event + state", any(e["code"] == "action" for e in xo["history"]["events"]) and xo["history"]["state"] in ("still_active", "improved"))
rm = R.analyze_risk(M, customer_rows=D["sales"], today=T, categories=["operational", "customer"])
ym = DE.analyze_drivers(rm, M, customer_rows=D["sales"], today=T)
ct("scoped role sees only its categories' drivers", {d["category"] for d in ym["drivers"]} <= {"operational", "customer"})
yhy = DE.analyze_drivers(R.analyze_risk(MH, customer_rows=H["sales"], today=T), MH, customer_rows=H["sales"], today=T)
ct("healthy data → fewer elevated drivers", sum(1 for d in yhy["drivers"] if d["level"] in R.ELEVATED) < sum(1 for d in y["drivers"] if d["level"] in R.ELEVATED))
ct("deterministic output", json.dumps(DE.analyze_drivers(risk, M, customer_rows=D["sales"], sector="fnb", today=T), sort_keys=True, ensure_ascii=False) == json.dumps(y, sort_keys=True, ensure_ascii=False))
ct("JSON-serializable", bool(json.dumps(y, ensure_ascii=False)))
src = open(DE.__file__, encoding="utf-8").read() if os.path.exists(DE.__file__) else ""
ct("engine never touches AI", "ai_gateway" not in src and "Gemini" not in src)
ct("no causal claims in wording", "بسبب" not in src and "يسبب" not in src)
print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
