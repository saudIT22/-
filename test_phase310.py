"""Phase 3.10 — Board Presentation tests. Run: python3 test_phase310.py"""
import copy, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import prediction_engine as PE
import goals_engine as G
import board_engine as B
import board_fixture as BF

P = F = 0


def ct(n, c):
    global P, F
    if c:
        P += 1; print(f"PASS: {n}")
    else:
        F += 1; print(f"FAIL: {n}")


X = BF.build()
OWN = {"role": "owner", "user_id": 1}
mk = lambda v=OWN, prev=None, budget=None, **kw: B.analyze_board(ctx=X["ctx"], risk=X["risk"], drivers=X["drv"], pred=X["pred"], goals_res=X["gres"], dec_res=X["dres"],
                                                                 budget=X["budget"] if budget is None else budget, previous=prev, sector="fnb", today=X["T"], viewer=v, **kw)
PK = mk()

print("[GROUP] 3.10.1 data contract & evidence")
m = B.metric("revenue", "الإيراد", 1000, "financial", period="2026-09", calc="مجموع", confidence="high")
ct("metric carries source, link, period, calculation, confidence", m["source_ar"] and m["link"] == B.LINKS["financial"] and m["period"] and m["calculation_ar"] and m["confidence"])
ct("missing metric → unavailable with reason (never 0)", B.metric("x", "x", None, "financial")["available"] is False and B.metric("x", "x", None, "financial")["value"] is None)
ct("pack has every section in contract", all(k in PK for k, _ in B.SECTIONS))
ct("pack pages = cover + summary … appendix + data quality", [p["key"] for p in PK["pack_pages"]][:3] == ["cover", "summary", "lenses"] and PK["pack_pages"][-2]["key"] == "appendix")
ct("board metrics rows collected with source module", PK["metrics"] and all(set(x) >= {"metric", "value", "source_module"} for x in PK["metrics"]))

print("[GROUP] 3.10.2/3.10.3 five lenses & overview")
cats = {c["key"]: c for c in X["risk"]["categories"]}
L = {l["key"]: l for l in PK["lenses"]}
ct("profitability = 100 − profit risk score (sourced, not invented)", L["profitability"]["score"] == round(100 - cats["profit"]["score"], 1) and L["profitability"]["link"] == B.LINKS["financial"])
ct("liquidity / customer / risk lenses from 3.3", L["liquidity"]["score"] == round(100 - cats["liquidity"]["score"], 1) and L["customer"]["score"] == round(100 - cats["customer"]["score"], 1)
   and L["risk"]["score"] == round(100 - X["risk"]["index"]["score"], 1))
g, _ = B.growth_recent(X["ctx"]["sm"])
ct("growth lens = 50 + 2.5 × recent growth (clamped)", L["growth"]["score"] == round(max(0, min(100, 50 + 2.5 * g)), 1))
ct("lens thresholds 🟢 ≥70 · 🟠 60–69 · 🔴 <60", B.lens_status(81)[0] == "green" and B.lens_status(68)[0] == "orange" and B.lens_status(58)[0] == "red" and B.lens_status(None)[0] == "na")
ct("company status red when ≥2 lenses red", PK["status"]["key"] == "red")
H = BF.build(stressed=False)
PH = B.analyze_board(ctx=H["ctx"], risk=H["risk"], drivers=H["drv"], pred=H["pred"], goals_res=H["gres"], dec_res=H["dres"], budget=H["budget"], sector="fnb", today=H["T"], viewer=OWN)
ct("healthy company: lenses differ (some green)", any(l["status"] == "green" for l in PH["lenses"]) and PH["lenses"] != PK["lenses"])
prev = copy.deepcopy(PK["snapshot"]); prev["lenses"] = [{"key": l["key"], "score": (l["score"] or 0) + 10} for l in PK["lenses"]]
ct("trend arrow vs previous board snapshot", all(l["arrow"] == "↓" for l in mk(prev=prev)["lenses"] if l["score"] is not None))
ct("no previous → no fake trend", all(l["trend"] is None for l in PK["lenses"]))

print("[GROUP] executive summary & 3 things")
sm = PK["summary"]
ct("paragraph built from engine facts (revenue change, cause, gap, branches)", "الإيراد" in sm["paragraph_ar"] and "فجوة" in sm["paragraph_ar"] and "فرع" in sm["paragraph_ar"])
ct("three things: biggest risk / opportunity / decision with links", all(sm["three"][k] and sm["three"][k]["link"] for k in ("risk", "opportunity", "decision")))
ct("biggest risk = highest-scoring category", sm["three"]["risk"]["ar"].startswith(max(X["risk"]["categories"], key=lambda c: c["score"] or 0)["ar"]))

print("[GROUP] 3.10.4 best/worst branch")
br = PK["branches"]
fb = {b["key"]: b for b in X["M"]["finance"]["branches"]}
ct("best and worst differ, ranking rule declared", br["status"] == "ok" and br["best"]["branch"] != br["worst"]["branch"] and "متوسط الترتيب" in br["rule_ar"])
ct("worst branch has lower margin than best (evidence)", fb[br["worst"]["branch"]]["gross_margin"] <= fb[br["best"]["branch"]]["gross_margin"])
ct("why = evidence lines from engines (not AI)", br["worst"]["why"] and all(("مقابل متوسط" in w) or ("مسبب" in w) for w in br["worst"]["why"]))
ct("branch metrics carry sources", all(m_["source_ar"] for m_ in br["best"]["metrics"]))
ct("single branch → unavailable", B.branches({"branches": [{"key": "أ"}]}, {}, {}, {})["status"] == "unavailable")

print("[GROUP] 3.10.5/3.10.6 exceptions & risk report")
ex = PK["exceptions"]
ct("exceptions only (≤12), sorted red → orange → yellow", len(ex) <= 12 and [x["severity"] for x in ex] == sorted([x["severity"] for x in ex], key=lambda s: {"red": 0, "orange": 1, "yellow": 2}[s]))
ct("profit/revenue forecast below target flagged red", any(x["title_ar"].startswith("توقع الربح") and x["severity"] == "red" for x in ex) and any("الإيراد دون الهدف" in x["title_ar"] for x in ex))
ct("goal at risk and late high-impact decision appear", any(x["source"] == "goals" for x in ex) and any(x["source"] == "decisions" for x in ex))
rr = PK["risk"]
t = rr["top"][0]
ct("risk report sorted by score with driver, root cause, mitigation, status", rr["top"] == sorted(rr["top"], key=lambda r: -r["score"]) and t["driver"]["name_ar"] and t["root_cause"] and t["mitigation"]["status_ar"])
lq = next(c for c in X["risk"]["categories"] if c["key"] == t["key"])
imp = {i["type"]: i["amount"] for i in lq["impacts"] if i.get("amount")}
ct("impact range = actual+potential → +exposure (typed, not mixed)", t["impact_range"] == [round((imp.get("actual") or 0) + (imp.get("potential") or 0), 2), round(sum(imp.values()), 2)])
sup = next((r for r in rr["top"] if r["key"] == "operational"), None)
ct("mitigation linked to an existing 3.9 decision when one exists", any(r["mitigation"].get("decision_id") for r in rr["top"]))

print("[GROUP] 3.10.7 financial summary (budget vs actual, money went where)")
fr = {r["key"]: r for r in PK["financial"]["rows"]}
tr = {t_["period"]: t_ for t_ in X["M"]["finance"]["trends"]}
ytd = [p for p in sorted(tr) if p >= "2026-01"]
ct("YTD actual from financial unit trends", abs(fr["revenue"]["actual"] - sum(tr[p]["revenue"] for p in ytd)) < 0.5 and PK["financial"]["fiscal_months"] == len(ytd))
ct("budget = monthly budget × months; variance & %", fr["revenue"]["budget"] == 160000 * len(ytd) and fr["revenue"]["variance"] == round(fr["revenue"]["actual"] - fr["revenue"]["budget"], 2))
ct("expenses over budget flagged bad (inverse)", fr["opex"]["budget"] == 42000 * len(ytd) and fr["opex"]["bad"] == (fr["opex"]["variance"] > 0))
ct("no budget for gross profit → reason, not 0", fr["gross_profit"]["budget"] is None and fr["gross_profit"]["budget_reason_ar"])
nb = mk(budget={})["financial"]
ct("no budget at all → unavailable budget column", not nb["budget_available"] and all(r["budget"] is None for r in nb["rows"]))
mo = PK["financial"]["money"]
ct("money went where: COGS + expense lines, shares sum ≈100%", abs(sum(l["share_pct"] for l in mo["lines"]) - 100) < 0.5 and mo["lines"][0]["key"] == "cogs")
ct("largest unexpected increase identified with link", mo["largest_increase"] and mo["largest_increase"]["ar"] == "الإيجار" and mo["largest_increase"]["change_pct"] == 60.0)
ct("variance why from financial bridge", PK["financial"]["variance_why"] and PK["financial"]["variance_main_ar"])

print("[GROUP] 3.10.8–3.10.12 forecast, goals, initiatives, decisions")
fc = PK["forecast"]
ct("forecast rows with confidence from 3.7", fc["status"] == "ok" and fc["rows"][0]["value"] == X["pred"]["outlook"]["forecast_revenue"] and fc["rows"][0]["confidence"] == X["pred"]["outlook"]["confidence"]["score"])
ct("forecast target gap from 3.7", fc["target_gap"]["gap"] == X["pred"]["gap"]["gap"])
ct("insufficient history → forecast unavailable (not invented)", B.forecast({"status": "insufficient", "message_ar": "غير كافية"})["status"] == "unavailable")
gs = PK["goals"]
ct("company goals with target/progress/forecast/status from 3.8", gs["company_goals"] and all(set(x) >= {"target", "progress_pct", "forecast", "status_ar"} for x in gs["company_goals"]))
ct("OKR progress listed with KRs", gs["okrs"] and gs["okrs"][0]["krs"])
ini = {i["key"]: i for i in PK["initiatives"]}
ct("initiatives = pillars with real goals (progress = avg goal progress)", ini["growth"]["goals"] == 1 and ini["growth"]["progress_pct"] == round(min(100, next(x for x in X["gres"]["goals"] if x["id"] == 1)["progress_pct"]), 1))
ct("pillar without goals flagged", ini["customer"]["warning_ar"])
rq = PK["decisions_required"]
ct("decisions required: pending approvals first, then proposed from goals", rq[0]["kind"] == "pending" and any(r["kind"] == "proposed" for r in rq) and len(rq) <= 5)
ct("each item: impact, risk if delayed, owner, deadline, recommendation", all(set(r) >= {"expected_impact", "risk_if_delayed_ar", "owner", "deadline_days", "recommendation_ar"} for r in rq))
ds = PK["decision_status"]
ct("decision status from 3.9 + overdue high-impact sentence", ds["late"] == X["dres"]["overview"]["overdue"] and ds["late_high_impact"] == 1 and "متأخرة" in ds["sentence_ar"])
rc = PK["recommendations"]
ct("recommendations ranked high/medium with evidence/impact/status", rc["high"] and all(set(x) >= {"evidence", "impact", "decision_status_ar"} for x in rc["high"] + rc["medium"]))

print("[GROUP] 3.10.13 appendix")
ap = PK["appendix"]
plk = {r["key"]: r for r in ap["pl"]}
ct("P&L month + YTD from financial unit", plk["revenue"]["month"] == X["M"]["finance"]["summary"]["revenue"]["net_sales"] and plk["net_profit"]["ytd"] == round(sum(tr[p]["net_profit"] for p in ytd), 2))
ct("balance sheet missing items unavailable, totals not faked", ap["balance_sheet"]["total_assets"] is None and any(i["value"] is None and i["reason_ar"] for i in ap["balance_sheet"]["assets"]))
cfr = {r["key"]: r for r in ap["cash_flow"]["rows"]}
ct("cash flow: operating = collections − outflows; investing unavailable (no fake 0)", cfr["operating"]["value"] == -70000.0 and cfr["investing"]["value"] is None and cfr["investing"]["reason_ar"])

print("[GROUP] 3.10.14/3.10.15 pre-read & previous meeting")
ct("no previous meeting → honest message", not PK["comparison"]["available"] and PK["preread"]["what_changed"][0])
pv = copy.deepcopy(PK["snapshot"])
pv["metrics"]["revenue_month"] = PK["snapshot"]["metrics"]["revenue_month"] / 1.062
pv["metrics"]["risk_index"] -= 8
pv["decisions_done"] = [d for d in pv["decisions_done"] if d != 1]
pv["decisions_measured"] = [d for d in pv["decisions_measured"] if d != 1]
pv["risk_categories"] = {k: v - 5 for k, v in pv["risk_categories"].items()}
gid = next(iter(pv["goals_status"]))
pv["goals_status"][gid] = "on_track"
pv["as_of"] = "2026-07-01"
C = mk(prev=pv)
cm = {r["key"]: r for r in C["comparison"]["rows"]}
ct("revenue +6.2% since last meeting", cm["revenue"]["change_pct"] == 6.2)
ct("risk +8 points", cm["risk"]["change_points"] == 8.0)
ct("decisions completed since + results achieved", C["comparison"]["decisions_completed"] == 1 and C["comparison"]["results_achieved"])
ct("risks increased & goals changed detected", C["comparison"]["risks_increased"] and C["comparison"]["goals_changed"][0]["before"] == "on_track")
ct("pre-read answers all 7 questions", set(C["preread"]) >= {"what_changed", "attention", "decisions_required", "risks_increased", "goals_changed", "decisions_completed", "results_achieved"}
   and C["preread"]["what_changed"] and C["preread"]["goals_changed"])
ct("profit change % suppressed when previous profit ≤ 0 (no misleading %)", cm["profit"]["change_pct"] is None)

print("[GROUP] 3.10.16/3.10.17 presentation mode & pack")
S = PK["slides"]
ct("slides: one message + ≤3 numbers + evidence + decision each", S and all(s["message"] and len(s["numbers"]) <= 3 and "evidence" in s and "decision" in s for s in S))
ct("slide order follows board journey", [s["key"] for s in S][:3] == ["summary", "lenses", "financial"] and S[-1]["key"] == "decisions_required")
sn = PK["snapshot"]
ct("snapshot for versioning/comparison holds metrics, lenses, decisions", set(sn) >= {"metrics", "lenses", "risk_categories", "goals_status", "decisions_done"})
ct("deterministic pack", json.dumps(mk(), sort_keys=True, ensure_ascii=False, default=str) == json.dumps(mk(), sort_keys=True, ensure_ascii=False, default=str))

print("[GROUP] 3.10.22–3.10.25 sectors, RBAC, data quality, AI")
ct("11 sectors configured (one engine)", set(B.SECTOR_BOARD) == set(PE.SECTOR_PRED))
sk = {r["key"]: r for r in PK["sector"]["rows"]}
ct("restaurant board KPIs: food cost / labor / delivery / branches", list(sk) == ["gross_margin", "payroll_pct", "on_time", "branches"] and sk["branches"]["value"] == 3)
ct("sector KPI missing → unavailable with reason", any(not r["available"] and r["reason_ar"] for r in B.sector_kpis("retail", {"cm": {}, "branches": []}, {}, {})["rows"]))
BRD = {"role": "staff", "title": "board", "user_id": 9}
pb = mk(v=BRD)
ct("board member: reads all sections, cannot generate/resolve", not pb["scope"]["can_generate"] and not pb["scope"]["can_resolve"] and not any(isinstance(pb.get(k), dict) and pb[k].get("restricted") for k, _ in B.SECTIONS))
pc = mk(v={"role": "accountant", "user_id": 3})
ct("CFO/accountant: financial + strategic, can generate, cannot resolve", pc["scope"]["can_generate"] and not pc["scope"]["can_resolve"] and pc["appendix"].get("pl"))
pm = mk(v={"role": "manager", "user_id": 4})
ct("manager: no financial appendix / financial summary", pm["appendix"].get("restricted") and pm["financial"].get("restricted") and not pm["scope"]["can_generate"])
ct("manager: profit/liquidity risks and profit forecast hidden", all(r["key"] not in ("profit", "liquidity") for r in pm["risk"]["top"]) if not pm["risk"].get("restricted") else True)
ct("manager forecast has no profit/cash rows", all(r["key"] not in ("profit", "cash") for r in pm["forecast"]["rows"]))
full = mk()
stored_for_mgr = B.restrict(full, {"role": "manager", "user_id": 4})
ct("stored full pack restricted for manager = live manager view", json.dumps(stored_for_mgr, sort_keys=True, ensure_ascii=False, default=str) == json.dumps(pm, sort_keys=True, ensure_ascii=False, default=str))
MGRV = {"role": "manager", "user_id": 4}
gm = G.analyze_goals(copy.deepcopy(BF.GOALS), krs=BF.KRS, actions=BF.ACTS, ctx=X["ctx"], today=X["T"], viewer=MGRV, strategy={"pillars": ["profitability", "growth"]})
pm2 = B.analyze_board(ctx=X["ctx"], risk=X["risk"], drivers=X["drv"], pred=X["pred"], goals_res=gm, dec_res=X["dres"], budget=X["budget"], sector="fnb", today=X["T"], viewer=MGRV)
ct("manager (with own goals scope): no profit/liquidity in summary, exceptions, slides; snapshot hidden",
   "ربح" not in pm2["summary"]["paragraph_ar"] and not any("الربح" in x["title_ar"] or "السيولة" in x["title_ar"] for x in pm2["exceptions"])
   and all("ربح" not in n["label"] and "السيولة" not in n["label"] for sl in pm2["slides"] if sl["key"] != "lenses" for n in sl["numbers"])
   and not any(sl["key"] == "financial" for sl in pm2["slides"]) and pm2["snapshot"].get("restricted"))
ct("manager still sees the five lenses (indicator scores, no money)", len(pm2["lenses"]) == 5 and not pm2["overview"].get("restricted"))
ct("owner pack unchanged by restrict", json.dumps(B.restrict(full, OWN), sort_keys=True, ensure_ascii=False, default=str) == json.dumps(full, sort_keys=True, ensure_ascii=False, default=str))
ct("employee: no access", B.board_scope({"role": "staff", "title": "employee"})["kind"] == "none")
dq = {r["key"]: r for r in PK["data_quality"]["rows"]}
ct("data quality disclosure: revenue high, profit medium (missing lines), forecast %, BS low", dq["revenue"]["level"] == "high" and dq["profit"]["level"] == "medium" and dq["forecast"]["level_ar"].endswith("%") and dq["balance_sheet"]["level"] == "low")
ax = B.ai_context(PK)
ct("AI context from pack only + explain-only rule", ax["lenses"] and ax["financial"] and "لا تخترع" in ax["rule"])
ct("AI context respects RBAC (manager: no financial)", not B.ai_context(pm)["financial"])
ct("board questions open evidence chains across modules", B.BOARD_QUESTIONS[0]["chain"][0][0] == "financial" and len(B.BOARD_QUESTIONS[1]["chain"]) >= 4)
e = B.analyze_board(ctx=G.build_context({}, [], today=X["T"]), today=X["T"], viewer=OWN)
ct("empty company: pack still renders with unavailable sections", e["branches"]["status"] == "unavailable" and e["forecast"]["status"] == "unavailable" and e["lenses"][0]["score"] is None)
print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
