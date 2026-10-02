"""Sector Intelligence Layer tests. Run: python3 test_phase30.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sector_intelligence import get_profile, compute_kpis, sector_leakage, PROFILES, KPI_DEFS
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
SECTORS = ["fnb", "retail", "ecommerce", "manufacturing", "contracting", "distribution", "services", "clinics", "hospitals", "logistics", "other"]
ct("11 sector profiles (matching the platform's sector list)", sorted(PROFILES) == sorted(SECTORS))
ct("legacy aliases map (restaurant/cafe → fnb)", get_profile("restaurant")["key"] == "fnb" and get_profile("cafe")["key"] == "fnb")
ct("unknown sector → other (never crashes)", get_profile("xyz")["key"] == "other" and get_profile(None)["key"] == "other")
ct("every profile: terms, KPIs defined, leakage types, core KPIs shared", all(get_profile(s)["terms"] and all(k in KPI_DEFS for k in get_profile(s)["kpis"]) and get_profile(s)["leakage"] and get_profile(s)["core_kpis"] for s in SECTORS))
ct("sector-specific vocabulary differs (F&B food cost vs contracting project cost)", get_profile("fnb")["terms"]["cogs"] == "تكلفة الطعام" and get_profile("contracting")["terms"]["cogs"] == "تكلفة المشروع")
mon = {"2026-06": {"revenue": 100000, "cogs": 29000, "payroll": 18000, "delivery_exp": 2400, "delivery_rev": 30000, "shrinkage": 500},
       "2026-07": {"revenue": 100000, "cogs": 29000, "payroll": 18000, "delivery_exp": 2400, "delivery_rev": 30000, "shrinkage": 500},
       "2026-08": {"revenue": 100000, "cogs": 32000, "payroll": 19000, "delivery_exp": 3000, "delivery_rev": 30000, "shrinkage": 900}}
k = {x["code"]: x for x in compute_kpis("fnb", mon, "2026-08", ["2026-06", "2026-07"])}
ct("F&B food cost % 29 → 32 with historical baseline", k["food_cost_pct"]["value"] == 32.0 and k["food_cost_pct"]["baseline"] == 29.0)
ct("KPI baseline from configured target overrides history", {x["code"]: x for x in compute_kpis("fnb", mon, "2026-08", ["2026-06"], {"food_cost_pct": 30})}["food_cost_pct"]["baseline"] == 30)
ct("leakage-item target also drives its KPI (one target, one source)", {x["code"]: x for x in compute_kpis("fnb", mon, "2026-08", ["2026-06"], {"food_cost": 31})}["food_cost_pct"]["baseline"] == 31)
ct("KPI missing input → unavailable, not 0", {x["code"]: x for x in compute_kpis("services", {"2026-08": {"revenue": 1}}, "2026-08", [])}["labor_pct"]["available"] is False)
sl = {i["code"]: i for i in sector_leakage("fnb", mon, "2026-08", ["2026-06", "2026-07"])["items"]}
ct("food cost leakage = (32% − 29%) × 100,000 = 3,000 SAR", sl["food_cost"]["amount"] == 3000.0 and "ريال" in sl["food_cost"]["money_ar"])
ct("delivery commission leakage = (10% − 8%) × 30,000 = 600", sl["delivery_commission"]["amount"] == 600.0 and sl["delivery_commission"]["supersedes"] == ["delivery"])
ct("labor leakage = 1% × 100,000 = 1,000 and supersedes payroll", sl["labor_cost"]["amount"] == 1000.0 and "payroll" in sl["labor_cost"]["supersedes"])
ct("shrinkage leakage from negative adjustments", sl["shrinkage"]["amount"] == 400.0)
ct("no supplier data → supplier item unavailable with reason", sl["supplier_price"]["available"] is False and "غير متاح" in sl["supplier_price"]["reason_ar"])
ct("data-needed items (waste, portion, voids) unavailable — never zero", all(not sl[c]["available"] and sl[c].get("amount") is None for c in ("waste", "portion_variance", "voids")))
ct("obsolete stock = exposure, not in total, recoverable unknown", (lambda o: o["available"] and not o["in_total"] and o["recoverable"] is None)({i["code"]: i for i in sector_leakage("fnb", mon, "2026-08", ["2026-06", "2026-07"], obsolete_value=2500)["items"]}["obsolete_stock"]))
con = {i["code"]: i for i in sector_leakage("contracting", mon, "2026-08", ["2026-06"])["items"]}
ct("contracting: project overrun / change orders need project data", not con["project_overrun"]["available"] and "مشروع" in con["project_overrun"]["reason_ar"] and not con["change_orders"]["available"])
cl = {i["code"]: i for i in sector_leakage("clinics", mon, "2026-08", ["2026-06"])["items"]}
ct("clinics: no-show needs appointment data", not cl["no_show"]["available"] and "المواعيد" in cl["no_show"]["reason_ar"])
ct("no baseline (first month) → no invented leakage", sector_leakage("fnb", {"2026-08": mon["2026-08"]}, "2026-08", [])["items"][0]["available"] is False)
ct("recurring flag when leakage in ≥2 of last 3 months", sector_leakage("fnb", {**mon, "2026-07": {**mon["2026-07"], "cogs": 31000}}, "2026-08", ["2026-06"])["items"][0]["recurring"] is True)
ct("all 11 sectors compute without error on the same canonical data", all(sector_leakage(s, mon, "2026-08", ["2026-06", "2026-07"])["items"] for s in SECTORS))
ct("no NaN", "NaN" not in json.dumps([sector_leakage(s, mon, "2026-08", ["2026-06"]) for s in SECTORS], default=str))
print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
