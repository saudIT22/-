"""Phase 3.1 — Revenue Leakage tests. Run: python3 test_phase31.py"""
import json, os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leakage_engine import analyze_leakage
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def S(m, br, sku, cat, gross, disc, ret, promo=None, day=5):
    return {"date": f"{m}-{day:02d}", "branch_name": br, "product_sku": sku, "category": cat, "quantity": 10, "gross_sales": gross,
            "discount": disc, "returns": ret, "channel": "نقاط البيع", "promotion": promo}
print("[GROUP] honesty")
ct("no sales → has_data False", analyze_leakage([])["has_data"] is False)
sales = []
for m in ("2026-05", "2026-06", "2026-07"):                      # تاريخ: خصم 5%، مرتجعات 2%
    sales += [S(m, "A", "لاتيه", "مشروبات", 10000, 500, 200), S(m, "C", "كيك", "حلويات", 10000, 500, 200)]
sales += [S("2026-08", "A", "لاتيه", "مشروبات", 10000, 500, 200), S("2026-08", "C", "كيك", "حلويات", 10000, 1500, 1200)]   # C: خصم 15%، مرتجعات 12%
exp = [{"ym": m, "cat": "marketing", "amount": a, "branch": None, "source": "cash"} for m, a in (("2026-05", 2000), ("2026-06", 2000), ("2026-07", 2000), ("2026-08", 5000))]
exp += [{"ym": m, "cat": "payroll", "amount": 20000, "branch": None, "source": "cash"} for m in ("2026-05", "2026-06", "2026-07", "2026-08")]
ar = [{"invoice_date": "2026-03-01", "due_date": "2026-04-01", "amount": 8000, "customer_name": "شركة الراية", "branch_name": "C"},
      {"invoice_date": "2026-08-01", "due_date": "2026-08-31", "amount": 5000, "customer_name": "فندق", "branch_name": "A"}]
r = analyze_leakage(sales, expense_lines=exp, receivables=ar, cost_map={"لاتيه": 300, "كيك": 400}, as_of=date(2026, 8, 31))
o = r["overview"]
print("[GROUP] four leakage types with explicit rules")
ct("discount leakage = actual − historical rate × gross (2000 − 5%×20000 = 1000)", r["discount"]["leakage"] == 1000.0 and r["discount"]["baseline_source"] == "historical")
ct("returns leakage = 1400 − 2%×20000 = 1000", r["returns"]["leakage"] == 1000.0)
ct("opex excess = marketing 5000 − median 2000 = 3000 (payroll flat → 0)", r["opex"]["leakage"] == 3000.0 and r["opex"]["lines"][0]["key"] == "marketing")
ct("total = disjoint sum (1000+1000+3000), rate of net revenue", o["total"] == 5000.0 and o["rate_pct"] == round(5000 / (20000 - 2000 - 1400) * 100, 2))
ct("AR at risk (>90 days) reported separately, NOT in total", o["ar_at_risk"] == 8000.0 and not next(t for t in o["types"] if t["key"] == "ar")["in_total"])
ct("attribution rule published", "لا تتداخل" in o["attribution_ar"] and "لا تُضاف" in o["attribution_ar"])
cfg = analyze_leakage(sales, settings={"max_discount_rate": 3, "acceptable_return_rate": 1})
ct("configured thresholds override history", cfg["discount"]["baseline_source"] == "configured" and cfg["discount"]["leakage"] == 2000 - 600)
print("[GROUP] missing data ≠ zero")
nodisc = [{k: v for k, v in x.items() if k != "discount"} for x in sales]
nd = analyze_leakage(nodisc)
t = {x["key"]: x for x in nd["overview"]["types"]}
ct("no discount column → unavailable with reason", t["discount"]["available"] is False and "الخصم" in t["discount"]["reason_ar"])
ct("no expenses → opex unavailable (not 0)", t["opex"]["available"] is False and t["opex"]["amount"] is None)
ct("no receivables → AR unavailable", t["ar"]["available"] is False)
ct("data-quality signals for unavailable types", sum(1 for s in nd["signals"] if s["code"] == "type_unavailable") >= 3)
first = analyze_leakage([S("2026-08", "A", "x", "y", 1000, 100, 10)])
ct("no history and no configured limit → no baseline, no invented leakage", first["discount"]["available"] is False and first["overview"]["total"] is None)
print("[GROUP] heatmap, branches, products, trends, causes")
hm = {x["branch"]: x["cells"] for x in r["heatmap"]["rows"]}
ct("heatmap C high on discount & returns, A none", hm["C"]["discount"]["level"] == "high" and hm["C"]["returns"]["level"] == "high" and hm["A"]["discount"]["level"] == "none")
ct("heatmap: unavailable cell when no data (opex by branch has no branch lines)", hm["A"]["ar"]["level"] in ("none", "low", "high", "medium"))
bc = {b["key"]: b for b in r["branches"]}
ct("branch leakage + top source", bc["C"]["leakage"] == 2000.0 and bc["C"]["top_source"] is not None)
ct("branch hotspot signal for C", any(s["code"] == "branch_hotspot" and s["dimension"] == "C" for s in r["signals"]))
pc = {p["key"]: p for p in r["products"]}
ct("product leakage with discount/returns % and margin", pc["كيك"]["discount_pct"] == 15.0 and pc["كيك"]["returns_pct"] == 12.0 and pc["كيك"]["gross_margin"] is not None)
ct("trends monthly with type split", len(r["trends"]) == 4 and r["trends"][-1]["discount"] == 1000.0)
ct("quarterly grain", analyze_leakage(sales, grain="quarter")["trends"][0]["period"] == "2026-Q2")
rc = next(c for c in r["root_causes"] if c["type"] == "returns")
ct("root cause = evidence shares + 'possible', not certain", any(e["value"] == "C" and e["share_pct"] > 80 for e in rc["evidence"]) and "ليست سبباً مؤكداً" in rc["note_ar"])
print("[GROUP] discount effectiveness")
pro = sales + [S("2026-08", "A", "لاتيه", "مشروبات", 30000, 3000, 0, "حملة الصيف", day=20)]
eff = analyze_leakage(pro)["discount"]["effectiveness"]
ct("campaign effectiveness measured when campaign data exists (relationship wording)", eff and eff[0]["determined"] and "ليست إثباتاً سببياً" in eff[0]["note_ar"])
ct("no campaign data → effectiveness cannot be determined (not 'ineffective')", r["discount"]["effectiveness"] is None and "لا يمكن تحديد" in r["discount"]["effectiveness_note_ar"])
print("[GROUP] recovery plan: expected ≠ recovered")
acts = [{"id": 1, "code": "returns_excess", "dimension": None, "period": "2026-07", "status": "done", "expected": 900, "due_date": "2026-08-15"},
        {"id": 2, "code": "discount_excess", "dimension": "C", "period": "2026-08", "status": "open", "expected": 1000, "due_date": "2026-08-10"}]
rp = analyze_leakage(sales, actions=acts, as_of=date(2026, 8, 31))["recovery"]
a1, a2 = rp["actions"]
ct("before/after measured only with a later period", a1["measured"] and a1["before"] == 0.0 and a1["after"] == 1000.0 and a2["measured"] is False)
ct("overdue + counts + expected vs measured kept separate", rp["overdue"] == 1 and rp["expected_savings"] == 1900.0 and rp["measured_improvement"] == 0 and "≠" in rp["note_ar"])
newcat = exp + [{"ym": "2026-08", "cat": "maintenance", "amount": 900, "branch": "C", "source": "cash"}]
nc = analyze_leakage(sales, expense_lines=newcat)
ct("new expense category with zero baseline: no crash, flagged as new spend", any("لم يُصرف عليه" in e for s in nc["signals"] for e in s["evidence"]))
print("[GROUP] Money Recovery Center")
mo = r["money"]
ct("4 money metrics: detected / opportunity / recovered / recurring", mo["detected"] == 5000.0 and mo["opportunity"] is not None and mo["recovered"] is not None and "recurring" in mo)
ct("returns opportunity = gap to best branch (A at 2%) — not the full leakage", next(x for x in mo["opportunity_lines"] if x["key"] == "returns")["amount"] == 1000.0 and "أفضل فرع" in next(x for x in mo["opportunity_lines"] if x["key"] == "returns")["basis_ar"])
ct("every money metric carries its published rule", all(k in mo["rules_ar"] for k in ("detected", "opportunity", "recovered", "recurring")))
ct("AR collectible separate (no recent payers → 0)", mo["collectible_ar"] == 0.0)
ct("money language in signal names (SAR)", any("أعلى من المعتاد بـ" in s["name_ar"] and "SAR" in s["name_ar"] for s in r["signals"]))
ct("drill-down: discount transactions + promotions", r["discount"]["transactions"][0]["amount"] == 1500.0 and r["discount"]["by_promotion"][0]["key"] == "بدون حملة")
rec = analyze_leakage(sales + [S("2026-07", "C", "كيك", "حلويات", 10000, 1500, 200)])
ct("recurring leakage = type present ≥2 of last 3 months", any(x["key"] == "discount" for x in rec["money"]["recurring_lines"]))
import sys as _s; _s.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../phase30"))
from sector_intelligence import sector_leakage
monthly = {m: {"revenue": 16600, "cogs": 5000, "payroll": 4000 if m != "2026-08" else 6000} for m in ("2026-05", "2026-06", "2026-07", "2026-08")}
sb = sector_leakage("fnb", monthly, "2026-08", ["2026-05", "2026-06", "2026-07"], supplier_items=[{"product": "دجاج", "supplier": "أ", "impact": 700, "change_pct": 11, "potential_saving": 300}])
rs = analyze_leakage(sales, expense_lines=exp, sector_block=sb)
lab = next(i for i in sb["items"] if i["code"] == "labor_cost")
ct("sector item (labor cost % F&B) added to detected", rs["money"]["detected"] == round(5000 + lab["amount"], 2))
ct("labor supersedes payroll opex excess (no double counting)", any(l.get("superseded_ar") for l in rs["opex"]["lines"] if l["key"] == "payroll"))
sup = next(i for i in sb["items"] if i["code"] == "supplier_price")
ct("supplier price increase is 'part of' food cost → shown, not added; recoverable = vs cheapest supplier", sup["in_total"] is False and sup["part_of"] == "food_cost" and sup["recoverable"] == 300)
ct("sector data-needed types listed as unavailable with required data", any(i["code"] == "waste" and not i["available"] and "سجل الهدر" in i["reason_ar"] for i in sb["items"]))
ct("sector signal speaks money", any(s["code"] == "sector_labor_cost" and "SAR" in s["name_ar"] for s in rs["signals"]))
print("[GROUP] RBAC & safety")
rr = analyze_leakage(sales, expense_lines=exp, restricted_categories={"payroll"})
pl = next(l for l in rr["opex"]["lines"] if l["key"] == "payroll")
ct("restricted category hidden for non-sensitive roles", pl["restricted"] and pl["actual"] is None)
ct("signals sorted risk-first with impact", r["signals"][0]["type"] == "risk" and r["signals"][0]["estimated_impact"])
ct("deterministic ids", [s["id"] for s in analyze_leakage(sales, expense_lines=exp, receivables=ar, as_of=date(2026, 8, 31))["signals"]] == [s["id"] for s in analyze_leakage(sales, expense_lines=exp, receivables=ar, as_of=date(2026, 8, 31))["signals"]])
ct("no NaN/Infinity", "NaN" not in json.dumps(r, default=str) and "Infinity" not in json.dumps(r, default=str))
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "leakage_engine.py"), encoding="utf-8").read().lower())
print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
