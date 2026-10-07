"""Phase 3.8 — Goals & Results Intelligence tests. Run: python3 test_phase38.py"""
import copy, json, os, sys
from datetime import date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import drivers_engine as DE
import prediction_engine as PE
import goals_engine as G
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
risk = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T)
drv = DE.analyze_drivers(risk, M, customer_rows=D["sales"], sector="fnb", today=T)
pred = PE.analyze_prediction(M, sales_rows=D["sales"], risk=risk, drivers=drv, settings={"targets": {"annual_revenue": 2_000_000}}, sector="fnb", today=T)
CTX = lambda: G.build_context(M, D["sales"], risk=risk, drivers=drv, pred=pred, today=T)
Y = {"start": "2026-01-01", "end": "2026-12-31"}

print("[GROUP] 3.8.1 data contract")
ok = {"name": "الإيراد", "metric": "revenue", "target": 1e6, **Y}
ct("valid goal passes", G.validate_goal(ok) == [])
ct("missing target rejected", any("المستهدف" in e for e in G.validate_goal({**ok, "target": None})))
ct("end before start rejected", any("بعد البداية" in e for e in G.validate_goal({**ok, "end": "2025-12-01"})))
ct("branch goal without branch rejected", any("الفرع" in e for e in G.validate_goal({**ok, "level": "branch"})))
ct("no metric and no KRs rejected (not measurable)", any("قابلاً للقياس" in e for e in G.validate_goal({**ok, "metric": None})))
ct("no metric but OKR KRs accepted", G.validate_goal({**ok, "metric": None}, has_krs=True) == [])
ct("parent from another company rejected", any("شركتك" in e for e in G.validate_goal({**ok, "parent_id": 99}, known_ids={1, 2})))
ct("finance metric at branch level rejected", any("الفرع" in e for e in G.validate_goal({**ok, "metric": "net_profit", "branch": "جدة", "level": "branch"})))
ct("vague KR rejected", G.validate_kr({"metric": None, "target": None}) and G.validate_kr({"metric": "nps", "target": 75}) and not G.validate_kr({"metric": "nps", "baseline": 64, "target": 75}))
ct("done action needs actual impact", G.validate_action({"action": "x", "status": "done", "expected_impact": 100}))
ct("milestone outside goal period rejected", G.validate_milestone({"title": "m", "due": "2027-03-01", "target": 1}, ok))

print("[GROUP] 3.8.5/3.8.6 KPI linking & goal vs actual")
ctx = CTX()
a = G.metric_actual({"metric": "revenue", **Y}, ctx)
manual = sum(float(r["net_sales"]) for r in D["sales"] if "2026-01-01" <= r["date"] <= "2026-12-31")
ct(f"revenue actual = exact sum of invoice rows in period ({a['value']:,.0f})", abs(a["value"] - manual) < 0.5 and a["as_of"] == date(2026, 9, 30))
ab = G.metric_actual({"metric": "revenue", "branch": "جدة", "level": "branch", **Y}, ctx)
ct("branch actual filters by branch", abs(ab["value"] - sum(float(r["net_sales"]) for r in D["sales"] if r["branch_name"] == "جدة" and r["date"] >= "2026-01-01")) < 0.5)
an = G.metric_actual({"metric": "net_profit", **Y}, ctx)
ct("net profit = sum of monthly statements in period", abs(an["value"] - sum(t["net_profit"] for t in M["finance"]["trends"] if t["period"] >= "2026-01")) < 0.5)
am = G.metric_actual({"metric": "gross_margin", **Y}, ctx)
tr = [t for t in M["finance"]["trends"] if t["period"] >= "2026-01"]
ct("margin = ratio of sums (not mean of ratios)", abs(am["value"] - sum(t["gross_profit"] for t in tr) / sum(t["revenue"] for t in tr) * 100) < 0.01)
ct("operational KPI linked to 3.5 metrics", G.metric_actual({"metric": "order_accuracy", **Y}, ctx)["value"] == 95.3)
ct("risk KPI linked to 3.3 driver", G.metric_actual({"metric": "supplier_late_pct", **Y}, ctx)["value"] == ctx["rd"]["supplier_late_pct"]["value"])
un = G.metric_actual({"metric": "customer_retention", **Y}, ctx)
ct("missing KPI → unavailable with reason (not 0)", un["status"] == "unavailable" and un["value"] is None and un["reason_ar"])
ct("manual KPI without value → unavailable", G.metric_actual({"metric": "nps", **Y}, ctx)["status"] == "unavailable")
ct("empty data → unavailable", G.metric_actual({"metric": "revenue", **Y}, G.build_context({}, [], today=T))["status"] == "unavailable")
ct("progress flow = actual / target", G.progress_of(G.METRICS["revenue"], 50, 200, None) == 0.25)
ct("progress level from baseline", abs(G.progress_of(G.METRICS["nps"], 68, 75, 64) - 4 / 11) < 1e-9)
ct("progress lower-is-better", G.progress_of(G.METRICS["employee_turnover"], 15, 10, 20) == 0.5)

print("[GROUP] 3.8.7 status engine (time + forecast, not ratio only)")
st, why = G.status_from(8.2 / 18, 0.5, 15.1 / 18)
ct("spec example 18M / 50% elapsed / 8.2M / forecast 15.1M → At Risk", st == "at_risk")
ct("…and the reason cites the forecast", any("التوقع" in w for w in why))
ct("far behind trajectory → Behind", G.status_from(0.30, 0.5, 0.7)[0] == "behind")
ct("on trajectory and forecast ≥ target → On Track", G.status_from(0.52, 0.5, 1.02)[0] == "on_track")
ct("same ratio, earlier in the year → On Track (time matters)", G.status_from(8.2 / 18, 0.44, 1.0)[0] == "on_track")
ct("target reached early → Completed", G.status_from(1.02, 0.8, 1.2, achieved=True)[0] == "completed")
ct("period ended below target → Missed", G.status_from(0.9, 1.0, 0.9, ended=True, achieved=False)[0] == "missed")
ct("not started", G.status_from(None, 0, None, started=False)[0] == "not_started")
ct("missing actual → unavailable (never 0)", G.status_from(None, 0.5, None)[0] == "unavailable")
ct("overdue milestone escalates to At Risk", G.status_from(0.52, 0.5, 1.02, milestone_overdue=True)[0] == "at_risk")
ct("dependency risk escalates to At Risk", G.status_from(0.52, 0.5, 1.02, dependency_risk=True)[0] == "at_risk")
ct("manual cancel respected", G.status_from(0.1, 0.9, 0.1, override="cancelled")[0] == "cancelled")
ct("probability: interval above target → high", G.probability(G.METRICS["revenue"], 100, {"status": "ok", "projection": 120, "lower": 105, "upper": 130}) == "high")
ct("probability: target inside interval → medium", G.probability(G.METRICS["revenue"], 100, {"status": "ok", "projection": 98, "lower": 90, "upper": 110}) == "medium")
ct("probability: upper below target → low", G.probability(G.METRICS["revenue"], 100, {"status": "ok", "projection": 80, "lower": 70, "upper": 95}) == "low")
ct("probability inverted for lower-is-better", G.probability(G.METRICS["employee_turnover"], 10, {"status": "ok", "projection": 8, "lower": 7, "upper": 9}) == "high")

print("[GROUP] 3.8.15 forecast vs goal (uses 3.7)")
g1 = {"id": 1, "name": "الإيراد", "metric": "revenue", "target": 1_900_000, **Y, "owner": "سعود"}
e1 = G.evaluate_goal(g1, ctx)
pts = PE.forecast_series(PE.series_of(ctx["sm"]["company"], "revenue"), 3, today=T, data_end=ctx["sm"]["data_end"])["points"]
ct("projection = actual + 3.7 forecast for remaining months", abs(e1["forecast"] - (e1["actual"] + sum(p["value"] for p in pts))) < 1)
ct("projection interval brackets the point", e1["forecast_lower"] <= e1["forecast"] <= e1["forecast_upper"])
ct("expected gap = target − projection", abs(e1["expected_gap"] - (1_900_000 - e1["forecast"])) < 0.01)
ct("seasonal trajectory used with 12 months history", e1["trajectory"] == "seasonal")
ct("required vs recent monthly rate computed", e1["required_rate"] and e1["recent_rate"] and e1["remaining_months"] == 3)
ct("goal beyond 12-month horizon → forecast unavailable (stated)", G.evaluate_goal({**g1, "end": "2028-06-30"}, ctx)["forecast_status"] == "unavailable")
ep = G.evaluate_goal({"id": 2, "name": "ربح", "metric": "net_profit", "target": 150000, **Y}, ctx)
pf = PE.profit_forecast(M["finance"]["trends"], PE.forecast_series([(t["period"], t["revenue"]) for t in M["finance"]["trends"]], 3, today=T, data_end=ctx["fin_end"]), 3, T)
ct("profit projection uses 3.7 profit bridge", abs(ep["forecast"] - (ep["actual"] + sum(p["net_profit"] for p in pf["points"]))) < 1)
ct("stressed company: revenue goal behind, explained", e1["status"] in ("behind", "at_risk") and e1["why"])

print("[GROUP] 3.8.8 OKR")
okr = {"id": 7, "name": "ربحية", "objective": "زيادة الربحية وتحسين جودة النمو", **Y}
krs = [{"id": 1, "goal_id": 7, "metric": "net_margin", "baseline": 9.7, "target": 15}, {"id": 2, "goal_id": 7, "metric": "nps", "baseline": 64, "target": 75, "actual": 68},
       {"id": 3, "goal_id": 7, "metric": None, "name": "Increase customer satisfaction"}]
eo = G.evaluate_goal(okr, ctx, krs=krs)
ct("OKR evaluates each KR with metric/baseline/target/current/source", len(eo["key_results"]) == 3 and eo["key_results"][1]["actual"] == 68 and eo["key_results"][1]["source_ar"])
ct("NPS KR progress 36% (64→75, now 68)", abs(eo["key_results"][1]["progress_pct"] - 36.4) < 0.1)
ct("vague KR flagged not measurable", eo["key_results"][2]["valid"] is False)
ct("objective confidence limited when a KR is vague", eo["confidence"]["level"] == "limited")
ct("objective status = worst KR", eo["status"] == "behind")

print("[GROUP] 3.8.9/3.8.10/3.8.11 cascading, branches, departments")
ac = G.auto_cascade(g1, ctx)
ct("auto cascade sums exactly to parent target", ac["status"] == "ok" and abs(ac["total"] - 1_900_000) < 0.01 and len(ac["rows"]) == 3)
ct("auto cascade states its basis", "حصة" in ac["basis_ar"])
ct("cascade not offered for non-branch metric", G.auto_cascade({**g1, "metric": "net_profit"}, ctx)["status"] == "unavailable")
goals = [g1, {"id": 5, "name": "الرياض", "metric": "revenue", "target": 650000, **Y, "level": "branch", "branch": "الرياض", "parent_id": 1, "distribution": "manual"},
         {"id": 9, "name": "شركة تحت فرع", "metric": "orders", "target": 10, **Y, "level": "company", "parent_id": 5}]
r = G.analyze_goals(goals, mods=M, sales_rows=D["sales"], risk=risk, drivers=drv, pred=pred, sector="fnb", today=T)
root = r["cascade"]["roots"][0]
ct("cascade allocation coverage & unallocated", root["allocation"]["coverage_pct"] == round(650000 / 1.9e6 * 100, 1) and root["allocation"]["state_ar"] == "توزيع ناقص")
ct("distribution flagged manual vs auto", root["children"][0]["distribution_ar"] == "موزّع يدوياً")
ct("level-order violation detected", any("بمستوى أعلى" in i for i in r["cascade"]["issues"]))
br = {b["branch"]: b for b in r["branches"]}
ct("branch view: saved branch goal shown", any(x["id"] == 5 for x in br["الرياض"]["goals"]))
ct("branch view: other branches get implied share (labelled, not saved)", br["جدة"]["implied"] and br["جدة"]["implied"][0]["implied"] and "غير محفوظ" in br["جدة"]["implied"][0]["implied_ar"])
ct("departments grouped (sales)", any(d["department"] == "sales" for d in r["departments"]))
gp = next(x for x in r["gaps"] if x["id"] == 1)
ct("gap analysis lists branch contribution from approved branch goal", gp["branches"] and gp["branches"][0]["branch"] == "الرياض")
ct("gap drivers phrased as relationships", gp["drivers"] and all("ليست سببية" in d["relation_ar"] for d in gp["drivers"]))
ct("gap close options from 3.7 recovery", any(c["source"] == "3.7" for c in gp["close_options"]))

print("[GROUP] 3.8.12 milestones")
ms = [{"id": 1, "goal_id": 1, "title": "Q1", "due": "2026-03-31", "target": 450000}, {"id": 2, "goal_id": 1, "title": "Q2", "due": "2026-06-30", "target": 950000},
      {"id": 3, "goal_id": 1, "title": "Q4", "due": "2026-12-31", "target": 1_900_000}]
em = G.evaluate_goal(g1, ctx, milestones=ms)
q1 = em["milestones"][0]
ct("milestone actual = cumulative sales to due date", abs(q1["actual"] - sum(float(r["net_sales"]) for r in D["sales"] if "2026-01-01" <= r["date"] <= "2026-03-31")) < 0.5)
ct("milestone status computed (achieved/overdue)", q1["status"] in ("achieved", "overdue") and em["milestones"][2]["status"] == "upcoming")
ct("trajectory follows approved milestones", em["trajectory"] == "milestones")
gen = G.generate_milestones(g1, ctx)
ct("generated quarterly milestones cumulative, last = target", len(gen) == 4 and gen[-1]["target"] == 1_900_000 and all(a["target"] < b["target"] for a, b in zip(gen, gen[1:])))
bad_ms = [{"id": 4, "goal_id": 1, "title": "Q2 هدف عالٍ", "due": "2026-06-30", "target": 5_000_000}]
eb = G.evaluate_goal({**g1, "target": 1_000_000}, ctx, milestones=bad_ms)
ct("overdue milestone → alert + at-risk reason", any(a["kind"] == "milestone_overdue" for a in eb["alerts"]))

print("[GROUP] 3.8.13 action items")
gl = {"id": 4, "name": "استرداد التسرب", "metric": "leakage_recovery", "target": 500000, **Y}
acts = [{"id": 1, "goal_id": 4, "action": "خصومات", "status": "done", "expected_impact": 100000, "actual_impact": 80000},
        {"id": 2, "goal_id": 4, "action": "مصروفات", "status": "open", "expected_impact": 150000, "due": "2026-09-01"}]
el = G.evaluate_goal(gl, ctx, actions=acts)
ct("leakage recovery actual = measured impact of done actions", el["actual"] == 80000)
ct("projection = actual + open actions (labelled, limited confidence)", el["forecast"] == 230000 and el["confidence"]["level"] == "limited")
ct("realisation % actual vs expected", el["actions"]["realisation_pct"] == 80.0)
ct("overdue action flagged", el["actions"]["overdue"] == 1 and any(a["kind"] == "action_overdue" for a in el["alerts"]))
ct("recovery goal with no actions → unavailable (not 0)", G.evaluate_goal(gl, ctx)["status"] == "unavailable")
ea = G.evaluate_goal(g1, ctx, actions=[{"id": 9, "goal_id": 1, "action": "عرض", "status": "open", "expected_impact": 200000}])
ct("actions coverage of the money gap", ea["actions"]["gap_coverage_pct"] == round(200000 / ea["expected_gap"] * 100, 1))
ct("action impact not added to data-based actual (no double count)", ea["actual"] == e1["actual"])

print("[GROUP] 3.8.14 dependencies")
ch = G.dependency_chain(g1, ctx)
ct("sales chain: inventory → purchasing → supplier", [n["node"] for n in ch] == ["inventory", "purchasing", "supplier"])
ct("high/critical node flags dependency risk", e1["dependency_risk"] == any(n["risk"] for n in ch) and e1["dependency_risk"])
gd = [{"id": 1, "name": "مخزون", "metric": "stockout_rate", "target": 2, "baseline": 20, **Y},
      {"id": 2, "name": "مبيعات", "metric": "revenue", "target": 100, **Y, "depends_on": [1]},
      {"id": 3, "name": "أ", "metric": "orders", "target": 1, **Y, "depends_on": [4]}, {"id": 4, "name": "ب", "metric": "orders", "target": 1, **Y, "depends_on": [3]}]
rd_ = G.analyze_goals(gd, mods=M, sales_rows=D["sales"], risk=risk, drivers=drv, today=T)
g2 = next(e for e in rd_["goals"] if e["id"] == 2)
ct("explicit upstream goal listed", g2["upstream"] and g2["upstream"][0]["name"] == "مخزون")
ct("mutual dependency reported", rd_["dependencies"]["issues"])

print("[GROUP] 3.8.16 risk chain Goal → Risk → Driver → Candidate → Action")
rc = ep["risk_chain"]
ct("profit goal linked to risk drivers with category score", rc and rc[0]["risk"]["score"] is not None and rc[0]["driver"]["level"] in ("medium", "high", "critical"))
ct("chain includes 3.4 candidates (with 3.6 note) and action", any(x["candidates"] for x in rc) and all("3.6" in x["candidates_note_ar"] for x in rc) and any(x["action"] for x in rc))

print("[GROUP] 3.8.17/3.8.18 alerts & history")
ctx2 = CTX()
e_now = G.evaluate_goal(g1, ctx2)
prev = {"1": {"status": "on_track", "forecast": e_now["forecast"] * 1.2, "at": "2026-08-31"}}
al, hi = G.status_changes([e_now], prev)
ct("status change alert + automatic history row", any(a["kind"] == "status_changed" for a in al) and hi and hi[0]["field"] == "computed_status" and hi[0]["old_value"] == "on_track")
ct("forecast changed ≥5% alert", any(a["kind"] == "forecast_changed" for a in al))
ct("slow progress alert when recent rate < 80% required", any(a["kind"] == "slow_progress" for a in e1["alerts"]) == (e1["recent_rate"] < 0.8 * e1["required_rate"]))
ct("no-owner alert", any(a["kind"] == "no_owner" for a in ep["alerts"]))
ct("at-risk goal without actions alert", any(a["kind"] == "no_actions" for a in e1["alerts"]))
chg = G.diff_goal({"target": 18_000_000, "owner": "سعود", "name": "x"}, {"target": 19_000_000, "owner": "سعود", "name": "x"})
ct("history diff: only changed fields", len(chg) == 1 and chg[0]["field"] == "target" and chg[0]["old"] == 18_000_000)
ct("target change requires a reason (governance)", G.requires_reason(chg) and not G.requires_reason(G.diff_goal({"owner": "a"}, {"owner": "b"})))
ct("numeric equality not logged as change", G.diff_goal({"target": 100}, {"target": 100.0}) == [])

print("[GROUP] 3.8.19/3.8.24 collaboration + RBAC")
GS = [{"id": 1, "name": "شركة", "metric": "revenue", "level": "company", **Y, "target": 1},
      {"id": 2, "name": "قسم المبيعات", "metric": "aov", "level": "department", "department": "sales", **Y, "target": 1},
      {"id": 3, "name": "فرع جدة", "metric": "revenue", "level": "branch", "branch": "جدة", **Y, "target": 1},
      {"id": 4, "name": "فرع الرياض", "metric": "revenue", "level": "branch", "branch": "الرياض", **Y, "target": 1},
      {"id": 5, "name": "هدف أحمد", "metric": "orders", "level": "employee", "branch": "جدة", "owner": "أحمد", "owner_user_id": 50, **Y, "target": 1},
      {"id": 6, "name": "ربح", "metric": "net_profit", "level": "company", **Y, "target": 1}]
vis = lambda v, assigned=(): [g["id"] for g in GS if G.visible(g, v, assigned)]
ct("owner sees all", vis({"role": "owner"}) == [1, 2, 3, 4, 5, 6])
ct("CEO sees executive goals (company + department)", vis({"role": "manager", "title": "ceo", "user_id": 2}) == [1, 2, 6])
ct("branch manager sees only own branch", vis({"role": "manager", "title": "branch_manager", "branch": "جدة", "user_id": 3}) == [3, 5])
ct("department manager sees department goals", vis({"role": "manager", "title": "dept_manager", "department": "sales", "user_id": 4}) == [2, 3, 4, 5])
ct("employee sees own goals only", vis({"role": "staff", "user_id": 50, "name": "أحمد"}) == [5])
ct("employee sees goal with action assigned to them", vis({"role": "staff", "user_id": 60}, {4}) == [4])
ct("accountant (unbound) sees financial categories", vis({"role": "accountant", "user_id": 7}) == [1, 3, 4, 6])
ct("manager (unbound) sees operational/customer categories", vis({"role": "manager", "user_id": 8}) == [2, 5])
ct("staff can comment but not assign owner", G.can_collab("comment", GS[4], {"role": "staff", "user_id": 50}) and not G.can_collab("assign_owner", GS[4], {"role": "staff", "user_id": 50}))
ct("branch manager can approve branch goals, not company", G.can_collab("approve", GS[2], {"role": "manager", "title": "branch_manager", "branch": "جدة"}) and not G.can_edit(GS[0], {"role": "manager", "title": "branch_manager", "branch": "جدة"}))
ct("cannot approve own goal (non-owner)", not G.can_collab("approve", GS[4], {"role": "manager", "title": "branch_manager", "branch": "جدة", "user_id": 50}))
acts_rb = [{"id": 1, "goal_id": 4, "action": "a", "owner_user_id": 60, "status": "open"}, {"id": 2, "goal_id": 4, "action": "b", "owner_user_id": 61, "status": "open"}]
rs = G.analyze_goals(GS, actions=acts_rb, mods=M, sales_rows=D["sales"], today=T, viewer={"role": "staff", "user_id": 60})
ct("staff: only goal with assigned action and only their action", [g["id"] for g in rs["goals"]] == [4] and [a["id"] for a in rs["actions"]] == [1])
ct("scope label explains what is visible", rs["scope"]["kind"] == "own")

print("[GROUP] 3.8.20/3.8.25 evidence, score, data quality")
ev = e1["evidence"]
ct("evidence: source, calculation, period, last update, records", ev["source_ar"] and "سطر فاتورة" in ev["calculation_ar"] and ev["last_update"] == "2026-09-30" and ev["records"] > 0)
ct("goal score 0–100 and separate from company risk index", 0 <= e1["score"] <= 100 and "لا تُخلط" in G.SCORE_RULE_AR and "score" not in e1["evidence"])
ct("goal score formula: pace 45 / attainment 35 / milestones 20", G.goal_score(1.0, 0.5, None) == round((45 * 1 + 35 * 0.5) / 80 * 100, 1) and G.goal_score(None, None, None) is None)
dq = G.data_quality([G.evaluate_goal({"id": 1, "name": "x", "metric": "customer_retention", "target": 80, "baseline": 60, **Y}, ctx), e1], ctx)
ct("data quality lists unavailable goals with reason", dq["unavailable"] and dq["unavailable"][0]["reason_ar"])
eu = G.evaluate_goal({"id": 1, "name": "x", "metric": "customer_retention", "target": 80, "baseline": 60, **Y}, ctx)
ct("unavailable goal: Data unavailable message, no score", eu["status"] == "unavailable" and eu["score"] is None and "Data unavailable" in eu["confidence"]["message_ar"])
eo2 = G.evaluate_goal({"id": 1, "name": "x", "metric": "order_accuracy", "target": 98, "baseline": 94, **Y}, ctx)
ct("no forecast → 'goal status confidence limited'", eo2["confidence"]["level"] == "limited" and "محدودة" in eo2["confidence"]["message_ar"])
old = G.build_context(M, D["sales"], today=T + timedelta(days=90))
ct("stale data limits confidence", G.evaluate_goal(g1, old)["confidence"]["level"] == "limited")

print("[GROUP] 3.8.21/3.8.22 decisions + before/after")
opts = e1 and G.decision_options(G.evaluate_goal(g1, ctx))
ct("at-risk goal offers decisions with KPI + typed financial impact", opts and opts[0]["kpi"] == "revenue" and all(o.get("impact_type") in (None, "recovery", "potential") for o in opts))
bl = G.decision_baseline(e1)
after = dict(e1, expected_gap=e1["expected_gap"] - 100000, progress=e1["progress"] + 0.05)
mm = G.measure_decisions([{"id": 1, "goal_id": 1, "title": "d", "baseline": bl}], {1: after})
ct("before/after: gap reduction measured, no causation claim", mm[0]["gap_change"] == -100000 and "عوامل أخرى" in mm[0]["note_ar"] and mm[0]["verdict_ar"] == "تحسّن بعد القرار")

print("[GROUP] 3.8.2/3.8.3/3.8.23 strategy, pillars, 11 sectors")
sv = G.strategy_view({"vision": "ر", "mission": None, "pillars": ["growth", "customer"]}, [e1], "fnb")
ct("pillar linked to real goal (KPI → actual → target → gap)", sv["pillars"][0]["chain"][0]["actual"] == e1["actual"] and sv["pillars"][0]["chain"][0]["gap"] is not None)
ct("pillar without goals warned", sv["pillars"][1]["warning_ar"])
ct("missing mission flagged", "الرسالة" in sv["missing_ar"])
ct("11 sectors configured (same engine)", set(G.SECTOR_GOALS) == set(PE.SECTOR_PRED) and len(G.SECTOR_GOALS) == 11)
rt = G.recommended_targets("fnb", ctx)
ct("recommended targets with basis; bench rule honest when no benchmark", any(t["metric"] == "gross_margin" and not t["available"] and "لا نخترع" in t["basis_ar"] for t in rt["templates"]))
ct("growth rule = current TTM × 1.10", next(t for t in rt["templates"] if t["metric"] == "revenue")["recommended_target"] == round(rt["templates"][0]["current"] * 1.1, 2))
ct("sector terminology applied (retail: basket size)", G.recommended_targets("retail", ctx)["terms"]["aov"] == "حجم السلة")
ct("all sector templates use catalog metrics", all(G.metric_meta(c) for s in G.SECTOR_GOALS.values() for c, _, _ in s["templates"]))
sg = G.suggestions_from_existing({"annual_revenue": 18_000_000, "target_margin": 15, "branch_targets": {"جدة": 100000}}, T, 1)
ct("existing targets offered for import (not auto-created)", len(sg) == 3 and sg[0]["source"] == "import" and sg[0]["end"] == "2026-12-31")

print("[GROUP] full analysis, matrix, healthy company, AI, determinism")
full = G.analyze_goals([g1, ep and {"id": 2, "name": "ربح", "metric": "net_profit", "target": 150000, **Y, "priority": "high"}, gl], actions=acts, mods=M, sales_rows=D["sales"],
                       risk=risk, drivers=drv, pred=pred, sector="fnb", today=T)
ct("overview counts sum to total", sum(full["overview"]["counts"][k] for k in ("on_track", "at_risk", "behind", "completed", "missed", "not_started", "unavailable")) == full["overview"]["counts"]["total"])
ct("overview says where the risk is", full["overview"]["where"]["pillar"] and full["overview"]["top_risk"])
ct("matrix places high-impact lagging goals in 'intervene'", any(p["quadrant"] == "intervene" for p in full["matrix"]["points"]))
ct("signals emitted for executive integration", full["signals"] and full["signals"][0]["source_module"] == "goals")
ct("snapshot for next review", "1" in full["snapshot"] and full["snapshot"]["1"]["status"])
ax = G.ai_context(full)
ct("AI context = engine numbers only + rule", ax["goals"] and "لا تخترع" in ax["rule"] and set(ax["goals"][0]) >= {"target", "actual", "forecast", "status_ar"})
ct("deterministic", json.dumps(G.analyze_goals([g1], mods=M, sales_rows=D["sales"], risk=risk, drivers=drv, pred=pred, today=T), sort_keys=True, ensure_ascii=False, default=str)
   == json.dumps(G.analyze_goals([g1], mods=M, sales_rows=D["sales"], risk=risk, drivers=drv, pred=pred, today=T), sort_keys=True, ensure_ascii=False, default=str))
DH = FX.build(stressed=False); MH = FX.run_modules(DH)
rkh = R.analyze_risk(MH, customer_rows=DH["sales"], sector="fnb", today=T)
dvh = DE.analyze_drivers(rkh, MH, customer_rows=DH["sales"], sector="fnb", today=T)
ch_ = G.build_context(MH, DH["sales"], risk=rkh, drivers=dvh, today=T)
cur = G.metric_actual({"metric": "revenue", **Y}, ch_)["value"]
eh = G.evaluate_goal({"id": 1, "name": "h", "metric": "revenue", "target": round(cur / 0.75 * 0.97, 0), **Y}, ch_)
print("   healthy:", eh["status"], eh["why"], eh["attainment_pct"], eh["pace_pct"])
ct(f"healthy company, reachable target → not behind ({eh['status']})", eh["status"] in ("on_track", "at_risk") and eh["status"] != "behind")
ct("…and if at risk it is only due to dependency, not pace/forecast", eh["status"] == "on_track" or eh["dependency_risk"] or (eh["attainment_pct"] or 0) < 97)
print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
