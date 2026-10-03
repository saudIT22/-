"""Phase 3.3 — Risk Intelligence Engine tests. Run: python3 test_phase33.py"""
import json, os, sys, copy
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import risk_fixture as FX

P = F = 0


def ct(n, c):
    global P, F
    if c:
        P += 1; print(f"PASS: {n}")
    else:
        F += 1; print(f"FAIL: {n}")


D = FX.build(stressed=True)
M = FX.run_modules(D)
H = FX.build(stressed=False)
MH = FX.run_modules(H)
T = FX.TODAY
x = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T)
drv = {d["key"]: d for d in x["drivers"]}

print("[GROUP] 3.3.1 data contract & missing ≠ zero")
e = R.analyze_risk({}, today=T)
ct("no data → has_data False, index unable (not 0)", e["has_data"] is False and e["index"]["score"] is None and e["index"]["level_ar"] == "تعذّر التحديد")
ct("every category unable with reason + needed data", all(c["status"] == "unable" and c["unable_reasons"] and all(u["needs_ar"] for u in c["unable_reasons"]) for c in e["categories"]))
only_cash = R.analyze_risk({"cashflow": M["cashflow"]}, today=T)
cats = {c["key"]: c for c in only_cash["categories"]}
ct("only cash data → liquidity scored, customer 'unable to determine'", cats["liquidity"]["score"] is not None and cats["customer"]["score"] is None and cats["customer"]["level_ar"] == "تعذّر التحديد")
ct("index uses determinable categories only + says what was excluded", only_cash["index"]["score"] == cats["liquidity"]["score"] and "مخاطر فقدان العملاء" in only_cash["index"]["excluded_note_ar"])
ct("confidence reflects missing drivers (limited/insufficient)", only_cash["confidence"]["pct"] < 50 and only_cash["confidence"]["sufficiency"] == "insufficient")
nocust = [dict(r, customer_name="") for r in D["sales"]]
xn = R.analyze_risk(M, customer_rows=nocust, today=T)
dn = {d["key"]: d for d in xn["drivers"]}
ct("no customer column → lost-customer unable with reason; revenue decline still measured", dn["lost_customer_revenue_pct"]["score"] is None and "العميل" in dn["lost_customer_revenue_pct"]["reason_ar"] and dn["revenue_decline_pct"]["score"] is not None)
ct("all drivers carry source module + evidence link", all(d["source_module"] and d["link"] for d in x["drivers"]))
ct("money dicts from modules unwrapped (DSO exposure is a number)", isinstance(drv["dso_days"]["impacts"][0]["amount"], float))

xh = R.analyze_risk(MH, customer_rows=H["sales"], today=T)
rwh = next(d for d in xh["drivers"] if d["key"] == "runway_months")
ct("no cash burn → runway measured as safe (low), not 'missing'", rwh["level"] == "low" and rwh["display_ar"] and rwh["needs_ar"] is None)
print("[GROUP] 3.3.3/3.3.4 rules & score")
ct("score bands: medium threshold = 40, high = 65, critical = 85", R.base_score(5, (5, 10, 20), "above") == 40 and R.base_score(10, (5, 10, 20), "above") == 65 and R.base_score(20, (5, 10, 20), "above") == 85)
ct("lower-is-worse metrics mirrored (runway 3 months = high)", R.base_score(3, (6, 3, 1.5), "below") == 65 and R.base_score(12, (6, 3, 1.5), "below") == 0)
ct("score monotonic in value", all(R.base_score(v, (5, 10, 20), "above") <= R.base_score(v + 1, (5, 10, 20), "above") for v in range(0, 40)))
ct("score bounded 0..100", all(0 <= (d["score"] or 0) <= 100 for d in x["drivers"]))
ct("each score explained by parts (threshold first)", all(d["score_parts"][0]["code"] == "threshold" for d in x["drivers"] if d["score"] is not None))
up = R.score_driver("dso_days", {"value": 70, "previous": 50}, (45, 60, 90))
dn_ = R.score_driver("dso_days", {"value": 70, "previous": 90}, (45, 60, 90))
ct("trend: worsening +5 / improving −5", round(up["score"] - dn_["score"], 1) == 10)
dur = R.score_driver("dso_days", {"value": 70}, (45, 60, 90), history_levels=["high", "high"])
ct("duration: persisted 2 prior assessments → +4", any(p["code"] == "duration" and p["points"] == 4 for p in dur["parts"]))
ct("impact ≥ 5% of revenue adds +5", any(p["code"] == "impact" for p in R.score_driver("revenue_decline_pct", {"value": 12, "impacts": [{"type": "actual", "amount": 100}]}, (5, 10, 20), revenue=1000)["parts"]))
ct("levels from published bands", R.level_of(85) == "critical" and R.level_of(64.9) == "medium" and R.level_of(39) == "low")
c0 = {c["key"]: c for c in x["categories"]}["liquidity"]
lv = [d["score"] for d in x["drivers"] if d["category"] == "liquidity" and d["score"] is not None]
ct("category = 60% worst + 40% mean", abs(c0["score"] - round(0.6 * max(lv) + 0.4 * sum(lv) / len(lv), 1)) < 0.05)
ct("index explained: contributions add up to the score ('how did we get N?')", abs(sum(c["points"] for c in x["index"]["contributions"]) - x["index"]["score"]) < 0.3 and "=" in x["index"]["explanation_ar"])
ct("deterministic: same inputs → same output", json.dumps(R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T), sort_keys=True, ensure_ascii=False) == json.dumps(x, sort_keys=True, ensure_ascii=False))

print("[GROUP] 3.3.2 config layer & 3.3.18 sectors")
th, src = R.thresholds_for({"dso_days": [30, 40, 50]}, "contracting")
ct("company override beats sector beats default", th["dso_days"] == (30.0, 40.0, 50.0) and src["dso_days"] == "company" and R.thresholds_for(None, "contracting")[1]["dso_days"] == "sector")
ct("invalid (non-monotonic) override ignored", R.thresholds_for({"dso_days": [90, 60, 45]})[1]["dso_days"] == "default")
ct("all 11 sectors have a risk profile on the same engine", all(k in R.SECTOR_RISK for k in ("fnb", "retail", "ecommerce", "manufacturing", "contracting", "distribution", "services", "clinics", "hospitals", "logistics", "other")))
ct("sector weights change the index, not the engine", R.analyze_risk(M, customer_rows=D["sales"], sector="contracting", today=T)["index"]["score"] != x["index"]["score"])
ct("fnb leakage threshold from sector profile", drv["leakage_pct"]["threshold_source"] == "sector" and drv["leakage_pct"]["thresholds"] == [2, 5, 10])
xd = R.analyze_risk(M, customer_rows=D["sales"], settings={"disabled": ["turnover_pct"]}, today=T)
ct("disabled rule removed from evaluation and confidence base", all(d["key"] != "turnover_pct" for d in xd["drivers"]) and xd["confidence"]["total"] == len(R.DRIVERS) - 1)
ct("rules published with default + effective + source", all({"values", "source", "default"} <= set(v) for v in x["rules"]["thresholds"].values()))

print("[GROUP] 3.3.5–3.3.7 drivers, evidence, typed impact")
ct("stressed fixture detects the 4 pasted drivers (cost, collection, stockout, customers)", all(drv[k]["level"] in R.ELEVATED for k in ("cost_increase_pct", "dso_days", "stockout_items", "lost_customer_revenue_pct")))
ct("supplier concentration detected with supplier drill", drv["supplier_concentration_pct"]["level"] in R.ELEVATED and drv["supplier_concentration_pct"]["drill"][0]["level"] == "supplier")
ct("cost driver drills to products with current vs baseline price", drv["cost_increase_pct"]["drill"] and {"current", "baseline", "variance_pct"} <= set(drv["cost_increase_pct"]["drill"][0]))
ct("evidence present for every evaluated driver", all(d["evidence"] for d in x["drivers"] if d["score"] is not None))
types = {i["type"] for d in x["drivers"] for i in d["impacts"]}
ct("impacts typed (actual/potential/exposure/recovery)", types <= set(R.IMPACT_TYPES) and {"actual", "exposure"} <= types)
ct("totals per type separately (no mixed grand total)", [i["type"] for i in x["impacts"]] == [t for t in R.IMPACT_TYPES if t in {i["type"] for i in x["impacts"]}] and "total" not in json.dumps(list(x["impacts"][0].keys())))
exp = next(i for i in x["impacts"] if i["type"] == "exposure")
ct("overlapping receivables not double-counted (overdue ⊂ total AR)", exp["amount"] < sum(s["amount"] for s in exp["sources"]) and exp["overlap_ar"])
ct("lost-customer revenue is 'potential' not 'actual'", drv["lost_customer_revenue_pct"]["impacts"][0]["type"] == "potential")
ct("overdue AR is exposure, not loss", drv["overdue_ar_pct"]["impacts"][0]["type"] == "exposure")
ct("no zero-amount impacts", all(i["amount"] > 0 for d in x["drivers"] for i in d["impacts"]))

print("[GROUP] 3.3.8/3.3.9 correlation & root cause")
act = [c for c in x["chains"] if c["active"]]
ct("supply→customer chain active in stressed data", any(c["id"] == "supply_to_customer" for c in act))
ct("chains/graph say relationship not causation", "ليست سببية" in x["graph"]["note_ar"])
ct("dependency graph edges flag active links", any(e["active"] for e in x["graph"]["edges"]))
rc = {r["driver"]: r for r in x["root_causes"]}
ct("root cause for lost customers lists upstream stockout/supplier drivers", {"stockout_items"} & {u["key"] for u in rc["lost_customer_revenue_pct"]["upstream"]})
ct("profit root cause links to 3.1 leakage causes", any(m["module"] == "leakage" for m in rc["net_margin_pct"]["module_causes"]))
ct("compliance root cause links to 3.2 tax exposures", any(m["module"] == "tax" for r in x["root_causes"] if r["driver"] in ("invoice_error_count", "overdue_filings") for m in r["module_causes"]))
ct("healthy data → fewer active chains", sum(c["active"] for c in R.analyze_risk(MH, customer_rows=H["sales"], today=T)["chains"]) < len(act))

print("[GROUP] 3.3.10/3.3.11 branch & department")
br = {b["key"]: b for b in x["branches"]["rows"]}
ct("branch heatmap covers all branches", set(br) == set(FX.BRANCHES))
ct("Jeddah (slow + high discounts) worst operational branch", max(br, key=lambda k: (br[k]["cells"]["operational"] or {}).get("score", 0)) == "جدة")
ct("liquidity not split by branch (shown —, explained)", all(b["cells"]["liquidity"] is None for b in br.values()) and x["branches"]["company_level_ar"])
ct("branch row lists top drivers and module signals", all("top" in b and "module_signals" in b for b in br.values()))
ct("departments scored from turnover/on-time/defects", len(x["departments"]["rows"]) >= 3)

print("[GROUP] 3.3.12 timeline & trends")
hist = [{"date": (T - timedelta(days=40)).isoformat(), "index": 60.0, "categories": {"liquidity": 70.0}, "driver_levels": {"dso_days": "high"}},
        {"date": (T - timedelta(days=100)).isoformat(), "index": 50.0, "categories": {}, "driver_levels": {"dso_days": "medium"}}]
xt = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T, history=hist)
w = {w["window"]: w for w in xt["trends"]["windows"]}
ct("30d change = now − last snapshot before window", w["30d"]["available"] and w["30d"]["change"] == round(xt["index"]["score"] - 60.0, 1))
ct("90d uses the older snapshot", w["90d"]["base_index"] == 50.0)
ct("12m unavailable with reason (no snapshot) — not zero", w["12m"]["available"] is False and w["12m"]["reason_ar"])
ct("series built from snapshots", len(xt["trends"]["series"]) == 2)
ct("snapshot returned for persistence", {"date", "index", "categories", "driver_levels"} <= set(x["snapshot"]))
ct("detected date from history (first elevated)", next(a for a in xt["alerts"] if a["risk_key"] == "dso_days")["detected"] == (T - timedelta(days=100)).isoformat())

print("[GROUP] 3.3.13–3.3.17 alerts, register, mitigation, decision, before/after")
a0 = x["alerts"][0]
ct("alert carries risk/severity/evidence/impact/detected/source/action/owner/due/status", all(a0.get(k) is not None for k in ("risk", "severity", "evidence", "impacts", "detected", "source_ar", "action_ar", "owner", "due_date", "status")))
ct("alerts sorted by score", [a["score"] for a in x["alerts"]] == sorted([a["score"] for a in x["alerts"]], reverse=True))
stored = [{"risk_key": "dso_days", "status": "action", "owner": "سارة", "mitigation": "متابعة أسبوعية", "decision_id": 7,
           "baseline_score": 99.0, "baseline_date": "2026-08-01"},
          {"risk_key": "manual-fx", "title": "تقلب سعر الصرف", "category": "profit", "level": "medium", "status": "monitoring"},
          {"risk_key": "utilization_pct", "status": "measurement", "baseline_score": 70.0}]
xr = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T, stored=stored)
reg = {r["risk_key"]: r for r in xr["register"]}
ct("register keeps stored owner/status/mitigation/decision", reg["dso_days"]["owner"] == "سارة" and reg["dso_days"]["status"] == "action" and reg["dso_days"]["decision_id"] == 7)
ct("manual risk kept in register", reg["manual-fx"]["manual"] and reg["manual-fx"]["title"] == "تقلب سعر الصرف")
ct("before/after measured on same rule with no-causation note", reg["dso_days"]["before_after"]["result"] == "improved" and "لا يُثبت" in reg["dso_days"]["before_after"]["note_ar"])
ct("risk now low → resolved candidate", reg["utilization_pct"]["resolved_candidate"] is True)
ct("default owner by risk type when unassigned", reg["supplier_concentration_pct"]["owner"] == "المشتريات" and reg["supplier_concentration_pct"]["owner_is_default"])
ct("due date from severity (critical = 3 days)", reg["overdue_ar_pct"]["due_date"] == (T + timedelta(days=3)).isoformat())
wf = {c["status"]: c for c in xr["workflow"]}
ct("workflow board Detected→…→Reassessment", [c["status"] for c in xr["workflow"]][:7] == ["detected", "reviewed", "decision", "approved", "action", "measurement", "reassessment"] and wf["action"]["count"] == 1)
ct("closed risks drop out of alerts", "dso_days" not in {a["risk_key"] for a in R.analyze_risk(M, customer_rows=D["sales"], today=T, stored=[{"risk_key": "dso_days", "status": "closed"}])["alerts"]})

print("[GROUP] 3.3.19 RBAC scope (role → categories)")
xm = R.analyze_risk(M, customer_rows=D["sales"], today=T, categories=["operational", "customer"])
ct("scoped view shows only allowed categories", {c["key"] for c in xm["categories"]} == {"operational", "customer"} and not xm["scope"]["full"] and xm["scope"]["note_ar"])
ct("no profit/liquidity drivers, impacts or alerts leak into scoped view", all(d["category"] in ("operational", "customer") for d in xm["drivers"]) and all(a["category"] in ("operational", "customer") for a in xm["alerts"]))
ct("scoped heatmap hides other category cells", all(set(r["cells"]) <= {"operational", "customer"} for r in xm["branches"]["rows"]))
ct("scoped register drops stored items outside scope", all(r["category"] in ("operational", "customer") for r in R.analyze_risk(M, customer_rows=D["sales"], today=T, categories=["operational"], stored=stored)["register"]))
ct("scoped confidence counts only in-scope drivers", xm["confidence"]["total"] == sum(1 for m in R.DRIVERS.values() if m["cat"] in ("operational", "customer")))
print("[GROUP] 3.3.20–3.3.22 confidence, AI, CEO brief, integration")
ct("full fixture → confidence 100% sufficient", x["confidence"]["pct"] == 100.0 and x["confidence"]["sufficiency"] == "good")
ct("AI questions include 'how did we get N?'", any(str(x["index"]["score"]) in q for q in x["ai_questions"]))
ct("CEO brief deterministic with headline + decisions needed", x["brief"]["headline"].startswith("مؤشر المخاطر") and x["brief"]["decisions_needed"] and "بلا ذكاء اصطناعي" in x["brief"]["method_ar"])
s0 = x["signals"][0]
ct("signals in unified schema for executive center", all(k in s0 for k in ("id", "type", "code", "source_module", "name_ar", "severity", "evidence", "suggested_action_ar", "metric_id")) and s0["severity"] in ("high", "medium", "low"))
ct("stressed index > healthy index", x["index"]["score"] > R.analyze_risk(MH, customer_rows=H["sales"], today=T)["index"]["score"])
ct("output JSON-serializable", bool(json.dumps(x, ensure_ascii=False)))
src_code = open(R.__file__, encoding="utf-8").read()
ct("engine never touches AI", "ai_gateway" not in src_code and "Gemini" not in src_code)
ct("no causation claims in text (only 'relationship')", "يسبب" not in src_code and "بسبب" not in src_code)

print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
