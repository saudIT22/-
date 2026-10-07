"""Phase 3.5 — Sector Benchmark Intelligence tests. Run: python3 test_phase35.py"""
import json, os, sys, copy
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import risk_engine as R
import drivers_engine as DE
import benchmark_engine as BE
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


def B(m, v, **k):
    base = {"id": f"t-{m}-{k.get('region', '')}{k.get('city', '')}{k.get('period', '2025')}{k.get('size_segment', '')}", "metric": m, "sector": "fnb",
            "period": "2025", "value": v, "source": "TEST source", "methodology": "test", "confidence": "medium", "sample_size": 40, "period_end": "2025-12-31"}
    base.update(k)
    return base


DS = [B("gross_margin", 40, p10=20, p25=30, p50=40, p75=48, p90=55), B("gross_margin", 38, period="2024", period_end="2024-12-31"),
      B("gross_margin", 41, region="makkah"), B("net_margin", 8), B("aov", 300), B("discount_rate", 3), B("dso", 30), B("on_time", 90),
      B("customer_retention", 70), B("employee_turnover", 25), B("dio", 20), B("inventory_turnover", 12)]
PROF = {"sector": "fnb", "city": "جدة"}
z = BE.analyze_benchmark(M, sales_rows=D["sales"], risk=risk, drivers=drv, datasets=DS, profile=PROF, today=T)
C = z["comparisons"]

print("[GROUP] 3.5.1/3.5.4 data contract & benchmark validation")
ct("benchmark without source/methodology rejected", any("source" in e for e in BE.validate_benchmark({"metric": "x", "sector": "fnb", "period": "2025", "value": 1, "confidence": "high"})))
ct("non-ascending percentiles rejected", BE.validate_benchmark(B("aov", 1, p25=5, p50=3)) != [])
ct("valid benchmark passes", BE.validate_benchmark(B("aov", 300, p25=250, p50=300, p75=350)) == [])
ct("every compared metric carries source/period/sector/geo/size/method/sample/confidence/freshness",
   all({"source", "period", "sector", "geo_level", "size_segment", "methodology", "sample_size", "freshness_ar"} <= set(x["source"]) and x.get("confidence")
       for x in C.values() if x["status"] == "compared"))

print("[GROUP] missing ≠ zero")
ct("no benchmark → 'benchmark unavailable' (not 0)", C["ebitda_margin"]["status"] == "benchmark_unavailable" and C["ebitda_margin"].get("benchmark") is None)
ct("no company data → metric not compared", C["revenue_growth"]["status"] == "company_unavailable" and "24" in C["revenue_growth"]["company_reason_ar"])
e = BE.analyze_benchmark({}, today=T, profile=PROF)
ct("empty company → nothing compared, has_data False", e["has_data"] is False and e["summary"]["compared"] == 0)
z0 = BE.analyze_benchmark(M, sales_rows=D["sales"], risk=risk, drivers=drv, datasets=[], profile=PROF, today=T)
ct("no datasets at all → zero comparisons, all 'unavailable', no invented averages", z0["summary"]["compared"] == 0 and z0["summary"]["benchmark_unavailable"] > 0)
ct("data-needed metrics (NPS/CSAT/CAC/CLV/conversion) listed honestly", {x["key"] for x in z["data_needed"]} == {"conversion", "nps", "csat", "cac", "clv"})

print("[GROUP] 3.5.2/3.5.10/3.5.11 profile, geography, size")
ct("city → region (جدة → مكة المكرمة)", z["profile"]["region"] == "makkah" and z["profile"]["region_ar"] == "مكة المكرمة")
ct("size from revenue bands", BE.size_segment(2e6) == "micro" and BE.size_segment(10e6) == "small" and BE.size_segment(150e6) == "medium" and BE.size_segment(5e8) == "large" and BE.size_segment(None) is None)
ct("most specific geography chosen (regional over national)", C["gross_margin"]["source"]["geo_level"] == "regional" and C["gross_margin"]["benchmark"] == 41)
ct("all geo levels shown (national + regional)", set(C["gross_margin"]["geo_levels"]) == {"national", "regional"})
ct("other region's benchmark never used", not BE.candidates("gross_margin", [B("gross_margin", 50, region="riyadh")], {"sector": "fnb", "region": "makkah"}))
ct("size segment respected (no 500M vs 2M)", not BE.candidates("aov", [B("aov", 100, size_segment="large")], {"sector": "fnb", "size_segment": "micro"})
   and BE.candidates("aov", [B("aov", 100, size_segment="micro")], {"sector": "fnb", "size_segment": "micro"}))
ct("other sector's benchmark never used", not BE.candidates("aov", [dict(B("aov", 1), sector="retail")], {"sector": "fnb"}))
ct("annual growth not compared to a monthly benchmark basis", not BE.candidates("revenue_growth", [B("revenue_growth", 10, basis="month")], {"sector": "fnb"}, "yoy"))

print("[GROUP] 3.5.5–3.5.9 comparison, gap, percentile, impact")
g = C["net_margin"]
ct("gap = company − benchmark, position by tolerance", g["gap"] == round(g["company"] - 8, 2) and g["position"] in ("below", "critical"))
ct("lower-is-better handled (DSO 93 vs 30 → critical)", C["dso"]["position"] == "critical")
ct("higher-is-better above (AOV above sector)", C["aov"]["position"] == "above")
ct("near band", BE.position_of(40.5, 40, BE.METRICS["gross_margin"]) == "near")
pb = B("gross_margin", 40, p10=20, p25=30, p50=40, p75=48, p90=55)
ct("percentile by interpolation on published distribution", BE.percentile_of(40, pb, "higher") == 50 and BE.percentile_of(48, pb, "higher") == 75)
ct("lower-is-better percentile inverted", BE.percentile_of(30, pb, "lower") == 75)
ct("no distribution → no percentile/rank (reason given)", C["aov"]["percentile"] is None and C["aov"]["rank_reason_ar"])
ct("national level with distribution still yields percentile", C["gross_margin"]["geo_levels"]["national"]["percentile"] is not None)
ct("rank bands", BE.rank_of(80) == "top" and BE.rank_of(50) == "average" and BE.rank_of(10) == "critical" and BE.rank_of(None) is None)
im = C["net_margin"]["impact"]
ct("margin gap impact = gap pp × TTM revenue, labelled illustrative", im and im["amount"] > 0 and "ليست استرداداً مضموناً" in im["note_ar"])
ct("DSO/DIO impact is cash tied up, not profit", C["dso"]["impact"]["type"] == "cash" and C["dio"]["impact"]["type"] == "cash")
ct("no impact when company is better", C["aov"]["impact"] is None)
ct("opportunities not summed (overlap note)", "لا نجمع" in z["summary"]["illustrative_total_note_ar"])

print("[GROUP] 3.5.3 confidence & freshness")
old = B("aov", 300, period="2019", period_end="2019-12-31", confidence="high")
ct("stale (> 24 months) benchmark downgraded", BE.effective_confidence(old, T)[0] == "medium")
ct("small sample downgraded", BE.effective_confidence(B("aov", 1, sample_size=4, confidence="high"), T)[0] == "medium")
ct("cross-sector benchmark downgraded", BE.effective_confidence(dict(B("aov", 1, confidence="high"), sector="all"), T)[0] == "medium")
ct("comparison confidence = min(benchmark, company)", C["on_time"]["confidence"] == "low")

print("[GROUP] peers (anonymized, consented, k ≥ 5)")
pr = BE.peer_benchmarks({"gross_margin": [30, 32, 35, 38, 40, 41], "aov": [100, 200, 300]}, "fnb")
ct("peer benchmark only with ≥ 5 companies", [p["metric"] for p in pr] == ["gross_margin"])
ct("peer output aggregated only (median + percentiles + n), no individual values", set(pr[0]) >= {"p25", "p50", "p75", "sample_size"} and "values" not in pr[0] and pr[0]["sample_size"] == 6)
ct("peer confidence by sample size", pr[0]["confidence"] == "low")
zp = BE.analyze_benchmark(M, sales_rows=D["sales"], risk=risk, drivers=drv, datasets=[], peers=pr, profile=PROF, today=T)
ct("peer benchmark used + labelled origin", zp["comparisons"]["gross_margin"]["source"]["origin"] == "peer" and zp["comparisons"]["gross_margin"]["percentile"] is not None)

print("[GROUP] 3.5.12 trends & history")
ct("sector trend from two published periods", C["gross_margin"]["sector_trend"] is not None)
gm_nat = BE.sector_trend("gross_margin", DS, {"sector": "fnb"})
ct("national sector trend 2024 → 2025 change = +2", gm_nat["change"] == 2.0)
hist = [{"metric": "gross_margin", "period": p, "percentile": v, "gap_signed": None} for p, v in (("2026-Q1", 62), ("2026-Q2", 58), ("2026-Q3", 51))]
ph = BE.position_history("gross_margin", hist)
ct("losing sector position detected (62 → 58 → 51)", ph["signal"] == "losing" and "تفقد" in ph["signal_ar"])
zh = BE.analyze_benchmark(M, sales_rows=D["sales"], risk=risk, drivers=drv, datasets=DS, profile=PROF, today=T, history=hist)
ct("losing position surfaces on the metric's alert", any(a["metric"] == "gross_margin" and "تفقد" in (a.get("position_trend_ar") or a["kind_ar"]) for a in zh["alerts"]))
ct("company trend computed from monthly series", C["net_margin"]["company_trend"] in ("يتحسن", "يتراجع", "مستقر", "بيانات غير كافية"))

print("[GROUP] 3.5.13–3.5.16 positioning, strengths/weaknesses, opportunities, alerts")
pos = z["positioning"]
ct("sector positioning (price × performance), no competitor positions invented", pos["price_index"] is not None and pos["performance_index"] is not None and "لا نعرض مواقع منافسين" in pos["competitors_note_ar"])
ct("strengths and weaknesses detected", z["strengths"] and z["weaknesses"])
ct("weaknesses ordered critical first", [w["position"] for w in z["weaknesses"]][0] == "critical")
ct("opportunities link to areas + drivers + actions", z["opportunities"] and all(o["areas_ar"] for o in z["opportunities"]) and any(o["drivers"] for o in z["opportunities"]))
ct("alerts: critical/below/advantage", {a["kind"] for a in z["alerts"]} >= {"critical", "advantage"})
zi = BE.strategic_insights({"net_margin": {"position": "critical"}, "gross_margin": {"position": "near"}, "revenue_growth": {"position": "below"}, "customer_retention": {"position": "above"}})
ct("strategic insights: opex gap + acquisition (not retention)", any("المصروفات" in i["ar"] for i in zi) and any("الاستقطاب" in i["ar"] or "استقطاب" in i["ar"] for i in zi))
ct("branch benchmark vs same company benchmark", z["branches"]["rows"] and all("position" in i for r in z["branches"]["rows"] for i in r["items"]))

print("[GROUP] 3.5.17–3.5.19 risk / drivers / root cause integration")
ct("weak metric explained by 3.4 drivers (why)", C["dso"]["why"] and C["dso"]["why"][0]["key"] in ("dso_days", "overdue_ar_pct"))
ct("why carries root-cause candidates from 3.4", any(w.get("candidates") is not None for w in C["net_margin"]["why"]))
ct("risk link to 3.3 category level", C["dso"]["risk_link"]["category"] == "liquidity" and C["dso"]["risk_link"]["level"] is not None)
ct("signals in unified schema for executive center", z["signals"] and all(k in z["signals"][0] for k in ("id", "type", "code", "source_module", "severity", "evidence")))

print("[GROUP] 3.5.20 sectors, 3.5.21 RBAC, 3.5.22 quality")
ct("11 sectors configured on one engine", all(k in BE.SECTOR_BENCH for k in ("fnb", "retail", "ecommerce", "manufacturing", "contracting", "distribution", "services", "clinics", "hospitals", "logistics", "other")))
ct("fnb focus includes sector KPIs + needs (table turnover, waste)", any(f["metric"].startswith("sector:") for f in z["sector_config"]["focus"]) and any("الهدر" in n["ar"] for n in z["sector_config"]["needs"]))
zm = BE.analyze_benchmark(M, sales_rows=D["sales"], risk=risk, drivers=drv, datasets=DS, profile=PROF, today=T, categories=["operational", "customer"])
ct("scoped role: no financial or cash benchmarks", all(x["group"] not in ("financial", "cash", "sector") for x in zm["comparisons"].values()))
ct("quality: coverage + confidence counts", z["quality"]["coverage_pct"] is not None and sum(z["quality"]["by_confidence"].values()) == z["summary"]["compared"])
ct("snapshot rows for history/peers (company values only)", z["snapshot"] and all("company_value" in r for r in z["snapshot"]))
ct("deterministic", json.dumps(BE.analyze_benchmark(M, sales_rows=D["sales"], risk=risk, drivers=drv, datasets=DS, profile=PROF, today=T), sort_keys=True, ensure_ascii=False) == json.dumps(z, sort_keys=True, ensure_ascii=False))
ct("JSON-serializable", bool(json.dumps(z, ensure_ascii=False)))
src = open(BE.__file__, encoding="utf-8").read() if os.path.exists(BE.__file__) else ""
ct("engine never touches AI", "ai_gateway" not in src and "Gemini" not in src)
ct("no guaranteed-profit wording", "ستربح" not in src and "مضمون" not in src.replace("ليست استرداداً مضموناً", "").replace("ليس استرداداً مضموناً", ""))
print(f"\nTOTAL: {P + F} | PASSED: {P} | FAILED: {F}")
sys.exit(1 if F else 0)
