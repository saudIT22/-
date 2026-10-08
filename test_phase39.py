"""Phase 3.9 — Follow-up on Decisions tests. Run: python3 test_phase39.py"""
import copy, json, os, sys
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import drivers_engine as DE
import prediction_engine as PE
import goals_engine as G
import decisions_engine as DX
import risk_fixture as FX
import dec_fixture as DF

P = F = 0


def ct(n, c):
    global P, F
    if c:
        P += 1; print(f"PASS: {n}")
    else:
        F += 1; print(f"FAIL: {n}")


T = FX.TODAY
D = FX.build(stressed=True); M = FX.run_modules(D)
risk = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T)
drv = DE.analyze_drivers(risk, M, customer_rows=D["sales"], sector="fnb", today=T)
CTX = G.build_context(M, D["sales"], risk=risk, drivers=drv, today=T)
GE = {2: {"name": "صافي الربح", "status": "behind", "status_ar": "متأخر", "progress_pct": 19.5, "expected_gap": 185000, "unit": "SAR"}}
DEC = DF.decisions()
RES = DX.analyze_decisions(copy.deepcopy(DEC), mods=M, sales_rows=D["sales"], risk=risk, drivers=drv, goal_evals=GE, sector="fnb", today=T, ctx=CTX)
E = {e["id"]: e for e in RES["decisions"]}

print("[GROUP] 3.9.1/3.9.3 data contract & creation")
ct("valid decision passes", DX.validate_decision({"title": "x", "priority": "high", "due": "2026-10-01", "kpis": [{"metric": "revenue", "target": 1}]}) == [])
ct("missing title rejected", DX.validate_decision({"title": " "}))
ct("unknown KPI rejected", any("الكتالوج" in e for e in DX.validate_decision({"title": "x", "kpis": [{"metric": "magic"}]})))
ct("negative cost rejected", any("سالبة" in e for e in DX.validate_decision({"title": "x", "cost": -5})))
ct("bad delay reason rejected", DX.validate_decision({"title": "x", "delay_reason": "lazy"}))
ct("readiness lists what blocks submission", DX.readiness({"title": "x"}) == ["السبب", "مؤشر KPI", "المسؤول", "الموعد", "الأثر المتوقع"])
ct("side-effect KPI alone is not a primary KPI", "مؤشر KPI" in DX.readiness({"title": "x", "kpis": [{"metric": "on_time", "side_effect": True}]}))

print("[GROUP] 3.9.4/3.9.5 approval workflow & RBAC")
OWN = {"role": "owner", "user_id": 1, "name": "سعود"}
CEO = {"role": "manager", "title": "ceo", "user_id": 2, "name": "الرئيس"}
MGR = {"role": "manager", "user_id": 3, "name": "مدير", "title": None}
BRM = {"role": "manager", "title": "branch_manager", "branch": "جدة", "user_id": 4, "name": "مدير جدة"}
EMP = {"role": "staff", "title": "employee", "user_id": 5, "name": "مدير المشتريات"}
ACC = {"role": "accountant", "user_id": 6, "name": "محاسب"}
ready = {"id": 99, "title": "قرار", "rationale": "سبب", "kpis": [{"metric": "revenue"}], "owner": "x", "due": "2026-12-01", "expected_impact": 1000, "workflow": "draft", "category": "marketing", "created_by_id": 3}
ct("draft → pending_approval allowed when ready (manager)", DX.transition_check(ready, "pending_approval", MGR) == [])
ct("incomplete draft cannot be submitted", any("يحتاج" in e for e in DX.transition_check({**ready, "owner": None}, "pending_approval", MGR)))
ct("manager cannot approve", DX.transition_check({**ready, "workflow": "pending_approval"}, "approved", MGR))
ct("CEO can approve", DX.transition_check({**ready, "workflow": "pending_approval"}, "approved", CEO) == [])
ct("creator (non-owner) cannot approve own decision", any("المنشئ" in e for e in DX.transition_check({**ready, "workflow": "pending_approval", "created_by_id": 2}, "approved", CEO)))
ct("illegal jump draft → completed blocked", any("لا يمكن الانتقال" in e for e in DX.transition_check(ready, "completed", OWN)))
ct("completed ≠ terminal: completed → measured only by exec", DX.transition_check({**ready, "workflow": "completed"}, "measured", MGR) and not DX.transition_check({**ready, "workflow": "completed"}, "measured", OWN))
ct("rejected decision can return to draft for rework", "draft" in DX.TRANSITIONS["rejected"])
d3 = DEC[2]
ct("employee sees decision assigned to them", DX.visible(d3, EMP) and DX.can("progress", d3, EMP) and DX.can("evidence", d3, EMP))
ct("employee cannot approve/edit/escalate", not DX.can("approve", d3, EMP) and not DX.can("edit", d3, EMP) and not DX.can("escalate", d3, EMP))
ct("employee cannot see unrelated decisions", not DX.visible(DEC[1], EMP))
ct("branch manager sees only branch decisions", DX.visible(DEC[3], BRM) and not DX.visible(DEC[0], BRM))
ct("sensitive financial decision hidden from manager, visible to accountant", not DX.visible(DEC[5], MGR) and DX.visible(DEC[5], ACC))
ct("manager can create/assign/recommend; owner can approve/reject/escalate/close", DX.can("create", {}, MGR) and DX.can("recommend", DEC[0], MGR)
   and all(DX.can(a, DEC[0], OWN) for a in ("approve", "reject", "escalate", "cancel", "measure")))
ct("employee cannot create", not DX.can("create", {}, EMP))
re_ = DX.analyze_decisions(copy.deepcopy(DEC), mods=M, sales_rows=D["sales"], today=T, viewer=EMP, ctx=CTX)
ct("employee view = assigned only", sorted(e["id"] for e in re_["decisions"]) == [1, 3] and re_["scope"]["kind"] == "own")

print("[GROUP] 3.9.6 status engine")
ct("due passed + in progress → Late (derived)", E[3]["status"] == "late" and E[3]["workflow"] == "in_progress")
ct("completed → Evaluating (completed ≠ successful)", E[2]["status"] == "evaluating" and E[2]["status_ar"] == "بانتظار القياس")
ct("measured / pending / draft / cancelled kept", (E[1]["status"], E[4]["status"], E[5]["status"], E[11]["status"]) == ("measured", "pending_approval", "draft", "cancelled"))
ct("pending approval past due also Late", DX.display_status({"workflow": "pending_approval", "due": "2026-09-01"}, T) == "late")

print("[GROUP] 3.9.9/3.9.16 KPI linking & before/after (canonical source)")
k2 = E[2]["primary"]
sm = PE.sales_monthly(D["sales"])
man_before = sum(sm["company"][m]["revenue"] for m in ("2025-11", "2025-12", "2026-01")) / 3
man_after = sum(sm["company"][m]["revenue"] for m in ("2026-04", "2026-05", "2026-06")) / 3
ct(f"before = avg of 3 complete months before decision month ({k2['before']:,.0f})", k2["before_months"] == ["2025-11", "2025-12", "2026-01"] and abs(k2["before"] - man_before) < 0.5)
ct("after = months after completion month (completion month excluded)", k2["after_months"] == ["2026-04", "2026-05", "2026-06"] and abs(k2["after"] - man_after) < 0.5)
ct("money impact = (after − before) × months", abs(E[2]["outcome"]["actual"] - round((man_after - man_before) * 3, 2)) < 1)
k1 = E[1]["primary"]
tr = {t["period"]: t for t in M["finance"]["trends"]}
gm = lambda ms: sum(tr[m]["gross_profit"] for m in ms) / sum(tr[m]["revenue"] for m in ms) * 100
ct("ratio KPI before/after = ratio of sums", abs(k1["before"] - gm(["2025-12", "2026-01", "2026-02"])) < 0.01 and abs(k1["after"] - gm(["2026-05", "2026-06", "2026-07"])) < 0.01)
ct("current-only KPI uses stored baseline as before", E[3]["primary"]["before"] == 20 and E[3]["primary"]["before_basis_ar"].startswith("خط أساس محفوظ"))
ct("KPI progress toward target", DX.kpi_eval({"metric": "nps", "baseline": 64, "target": 75, "actual_manual": 68}, {"created_at": "2026-01-01"}, CTX)["progress_pct"] == round(4 / 11 * 100, 1))
kw = DX.kpi_eval({"metric": "stockout_rate", "baseline": 420, "target": 250, "actual_manual": 310}, {"created_at": "2026-01-01"}, G.build_context({}, [], today=T))
ct("spec KPI example (waste 420 → target 250, now 310): progress 64.7% (lower is better)", kw["progress_pct"] == 64.7)
ct("decision too recent for a complete after-month → still Evaluating, not judged", DX.outcome({"workflow": "completed", "completed_at": "2026-09-10"}, [DX.kpi_eval({"metric": "revenue"}, {"created_at": "2026-08-01", "completed_at": "2026-09-10"}, CTX)], CTX, None)["state"] == "evaluating")

print("[GROUP] 3.9.14/3.9.15 expected vs actual · result labels")
ct("Supplier Change 200K → 180K = Strong (−20K)", DX.result_of(200000, 180000) == "strong")
ct("Discount Change 150K → 90K = Partial (−60K)", DX.result_of(150000, 90000) == "partial")
ct("Branch Recovery 300K → 330K = Excellent (+30K)", DX.result_of(300000, 330000) == "excellent")
ct("weak / negative thresholds", DX.result_of(100, 30) == "weak" and DX.result_of(100, -5) == "negative")
ct("good result + bad side effect → Mixed", DX.result_of(200, 190, side_bad=True) == "mixed")
ct("no expected: direction decides", DX.result_of(None, None, primary_imp=-2) == "negative" and DX.result_of(None, None, primary_imp=0) == "no_change")
ct("variance = actual − expected", E[2]["outcome"]["variance"] == round(E[2]["outcome"]["actual"] - 150000, 2))
ct("side effect (on-time ↓) reported on supplier decision", E[1]["outcome"]["side_effects"] and E[1]["outcome"]["side_effects"][0]["worse"] and "التسليم" in E[1]["outcome"]["mixed_note_ar"])
ct("manual outcome flagged, limited confidence", E[8]["outcome"]["manual"] and E[8]["outcome"]["confidence"] == "limited")
nokpi = DX.outcome({"workflow": "completed", "completed_at": "2026-05-01"}, [], CTX, None)
ct("no KPI data → cannot measure (never success/fail)", nokpi["state"] == "cannot_measure" and nokpi["result"] == "not_measurable" and "insufficient KPI data" in nokpi["state_ar"])
ct("not completed → not measured yet", E[3]["outcome"]["state"] == "not_ready" and E[3]["outcome"]["actual"] is None)

print("[GROUP] 3.9.10–3.9.13 goal / risk / root cause / leakage links")
rl = E[1]["links"]["risk"]
ct("risk link shows baseline → current with verdict", rl["baseline"] == 74 and rl["current"] == risk_drv if (risk_drv := next(d["score"] for d in risk["drivers"] if d["key"] == "supplier_concentration_pct")) else False)
ct("risk verdict phrasing", rl["verdict_ar"] in ("انخفض الخطر", "ارتفع الخطر", "الخطر بلا تغيّر يُذكر") and "→" in rl["sentence_ar"])
ct("root cause still high → Persistent", E[1]["links"]["root_cause"]["status_key"] == "persistent")
ct("root cause improved to low → Resolved", DX.root_cause_link({"root_cause": "x_discount_leakage", "root_cause_baseline": 70}, CTX)["status_key"] == "resolved")
lk = E[6]["links"]["leakage"]
ct("leakage: actual recovery = monthly reduction × months since completion", lk["reduction"] == 15000 - 7200 and lk["actual_recovery"] == lk["reduction"] * lk["months"])
ct("leakage recovery rate", lk["recovery_rate_pct"] == round(lk["actual_recovery"] / 60000 * 100, 1))
ct("leakage decision measured by recovery (3.1), not the ratio estimate", E[6]["outcome"]["actual"] == lk["actual_recovery"])
ct("spec recovery example 145K / 200K = 72.5%", round(145000 / 200000 * 100, 1) == 72.5)
gl = E[10]["links"]["goal"]
ct("goal link: progress now vs at decision (auto, not added)", gl["progress_pct"] == 19.5 and gl["before_progress_pct"] == 20.0 and "تلقائياً" in gl["note_ar"])

print("[GROUP] 3.9.17/3.9.18 effectiveness & ROI")
ef = E[1]["effectiveness"]
ct("effectiveness shows all 7 components with weights", [c["key"] for c in ef["components"]] == ["kpi", "financial", "completion", "timeliness", "target", "side_effects", "confidence"]
   and sum(c["weight"] for c in ef["components"]) == 100)
ct("effectiveness = weighted mean of available components", abs(ef["score"] - round(sum(c["weight"] * c["value_pct"] for c in ef["components"] if c["value_pct"] is not None)
                                                                       / sum(c["weight"] for c in ef["components"] if c["value_pct"] is not None), 1)) < 0.11)
ct("final only when measured", ef["final"] and not E[2]["effectiveness"]["final"])
ct("spec ROI example: cost 50K, return 180K → net 130K, ROI 260%", DX.roi({"cost": 50000}, {"actual": 180000}) == {"available": True, "cost": 50000.0, "return": 180000, "net": 130000, "roi_pct": 260.0,
                                                                                                                  "rule_ar": DX.roi({"cost": 1}, {"actual": 1})["rule_ar"]})
ct("ROI unavailable without cost / before measurement", not DX.roi({}, {"actual": 5})["available"] and not DX.roi({"cost": 5}, {"actual": None})["available"])

print("[GROUP] 3.9.19/3.9.20 delay, escalation, dependencies, recurring")
dl = E[3]["delay"]
ct("late days + reason + impact at risk", dl["days"] == (T - date(2026, 9, 1)).days and dl["reason_ar"] == "بانتظار المورد" and dl["impact_at_risk"] == 280000)
ct("high impact + overdue → escalate", dl["escalate"] and "تصعيد" in dl["escalate_ar"])
ct("escalation alert raised", any(a["kind"] == "late_escalate" and a["decision_id"] == 3 for a in RES["alerts"]) and RES["escalations"])
ct("delay reasons aggregated", RES["delays"]["reasons"][0]["reason"] == "supplier")
dp = E[7]["dependencies"]
ct("downstream decision blocked by late upstream", dp["blocked"] and dp["upstream"][0]["id"] == 3)
ct("upstream shows its effect on dependants", E[3]["dependencies"]["affects_ar"])
cyc = {1: {"id": 1, "title": "أ", "depends_on": [2]}, 2: {"id": 2, "title": "ب", "depends_on": [1]}}
ct("dependency cycle detected", DX.dependency_issues(cyc))
ct("recurring decision detected (3 similar in 8 months)", RES["recurring"] and RES["recurring"][0]["count"] == 3 and "هيكلية" in RES["recurring"][0]["insight_ar"])
ct("cancelled decisions not counted as recurring", 11 not in RES["recurring"][0]["ids"])

print("[GROUP] overview, views, financial, heatmap, trail")
o = RES["overview"]
ct("overview counts", (o["open"], o["pending_approval"], o["overdue"], o["drafts"], o["total"]) == (3, 1, 1, 1, 11))
ct("expected total excludes drafts/cancelled", o["expected_total"] == sum(d.get("expected_impact") or 0 for d in DEC if d["workflow"] not in ("draft", "cancelled", "rejected")))
ct("actual total = sum of measured actuals", abs(o["actual_total"] - sum(e["outcome"]["actual"] for e in RES["decisions"] if e["outcome"].get("actual") is not None)) < 0.01)
ct("financial table rows have expected/actual/variance/result", all(set(r) >= {"expected", "actual", "variance", "result_ar"} for r in RES["financial"]["rows"]))
ct("by owner/branch/department groups", any(g["key"] == "مدير المشتريات" and g["late"] == 1 for g in RES["by_owner"]) and any(g["key"] == "جدة" for g in RES["by_branch"])
   and any(g["key"] == "purchasing" for g in RES["by_department"]))
ct("effectiveness history by category (which decisions work)", RES["effectiveness"]["by_category"] and RES["effectiveness"]["by_category"][0]["measured"] >= 1)
ct("heatmap: late high-impact = red", next(h for h in RES["heatmap"] if h["id"] == 3)["quadrant"] == "red")
ct("audit trail sorted newest first with actor", RES["trail"][0]["at"] >= RES["trail"][-1]["at"] and RES["trail"][0]["actor"])
ct("approval waiting > 7 days alert", any(a["kind"] == "approval_waiting" and a["decision_id"] == 4 for a in RES["alerts"]))
ct("ready-to-measure alert for completed decisions", any(a["kind"] == "ready_to_measure" for a in RES["alerts"]))
ct("draft without KPI flagged", any(a["kind"] == "no_kpi" and a["decision_id"] == 5 for a in RES["alerts"]))

print("[GROUP] 3.9.21–3.9.25 history, evidence, sectors, legacy, data quality, AI")
ct("history kept per decision (chronological)", [h["event"] for h in E[1]["history"]] == ["created", "approved", "completed", "measured"])
ct("11 sectors configured (one engine)", set(DX.SECTOR_DECISIONS) == set(PE.SECTOR_PRED) and all(c in DX.CATEGORIES for v in DX.SECTOR_DECISIONS.values() for c in v))
ct("sector categories carry KPI (restaurant: food cost / supplier / delivery / labor / discount)", [c["key"] for c in RES["sector"]["categories"]] == ["supplier", "cost", "operations", "staffing", "discount"]
   and RES["sector"]["categories"][1]["ar"] == "تكلفة الطعام والتشغيل")
lg = DX.from_legacy({"status": "open", "linked_to": "risk:dso_days|2026-09-30", "decision_type": "risk", "kpi": "net_sales", "expected_impact_value": 5000}, [{"status": "in_progress", "progress": 30}])
ct("legacy decision read without modification (open+action → in progress, risk key, KPI alias)", lg["workflow"] == "in_progress" and lg["risk_key"] == "dso_days" and lg["kpis"][0]["metric"] == "revenue" and lg["legacy"])
ct("legacy done → completed; goal link parsed", DX.from_legacy({"status": "done", "linked_to": "goal:7"})["workflow"] == "completed" and DX.from_legacy({"status": "done", "linked_to": "goal:7"})["goal_id"] == 7)
ct("legacy numeric links → dependencies", DX.from_legacy({"status": "open", "linked_to": "3,4"})["depends_on"] == [3, 4])
dq = RES["data_quality"]
ct("data quality lists decisions without KPI / cannot measure / manual", any(x["id"] == 5 for x in dq["no_kpi"]) and any(x["id"] == 8 for x in dq["manual"]))
ax = DX.ai_context(RES)
ct("AI context: engine numbers + rule (explain only)", ax["decisions"] and "لا تُصدر قراراً" in ax["rule"] and "review" in ax["decisions"][0])
ct("deterministic review draft with expected/actual/variance", any("الفعلي" in l for l in E[1]["review"]) and any("النتيجة" in l for l in E[1]["review"]))
ct("signals for executive integration", RES["signals"] and RES["signals"][0]["source_module"] == "decisions")
ct("deterministic", json.dumps(DX.analyze_decisions(copy.deepcopy(DEC), mods=M, sales_rows=D["sales"], today=T, ctx=CTX), sort_keys=True, ensure_ascii=False, default=str)
   == json.dumps(DX.analyze_decisions(copy.deepcopy(DEC), mods=M, sales_rows=D["sales"], today=T, ctx=CTX), sort_keys=True, ensure_ascii=False, default=str))
ce = DX.analyze_decisions([], mods={}, sales_rows=[], today=T)
ct("empty company: honest empty state", ce["overview"]["total"] == 0 and not ce["has_data"])
print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
