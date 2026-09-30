"""Phase 2.7 — Purchases Intelligence tests. Run: python3 test_phase27.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from purchases_engine import analyze_purchases
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def L(d, sup, sku, qty, uc, po, br="جدة", cat="بن", exp=None, rec=None, rq=None, xq=None, st=None, total=None):
    return {"date": d, "supplier_name": sup, "product_sku": sku, "quantity": qty, "unit_cost": uc, "reference": po,
            "branch_name": br, "category": cat, "expected_date": exp, "received_date": rec, "received_qty": rq,
            "rejected_qty": xq, "status": st, "total_cost": total}

print("[GROUP] honesty")
e = analyze_purchases([])
ct("no data → has_data False + what is needed", e["has_data"] is False and e["required"]["required"])
rows = [L("2026-07-05", "أ", "بن", 100, 10, "PO1"), L("2026-07-10", "ب", "بن", 50, 12, "PO2"),
        L("2026-08-03", "أ", "بن", 100, 11, "PO3", exp="2026-08-06", rec="2026-08-06", rq=100, xq=2, st="مستلم"),
        L("2026-08-04", "أ", "حليب", 40, 5, "PO3", cat="ألبان", exp="2026-08-06", rec="2026-08-06", rq=40, xq=0, st="مستلم"),
        L("2026-08-10", "ب", "بن", 20, 13, "PO4", br="الرياض", exp="2026-08-12", rec="2026-08-17", rq=20, xq=4, st="مستلم"),
        L("2026-08-20", "ج", "كوب", 500, 0.5, "PO5", cat="تغليف", st="مفتوح"),
        L("2026-08-22", None, "كوب", 100, None, None, cat="تغليف")]
r = analyze_purchases(rows)
k = r["kpis"]
ct("spend = Σ qty×cost (1100+200+260+250), missing cost excluded", k["spend"]["current"] == 1810.0 and k["spend"]["missing_cost_lines"] == 1)
ct("growth vs previous month (1600)", k["spend"]["previous"] == 1600.0 and k["growth"]["current"] == round((1810 - 1600) / 1600 * 100, 2))
ct("PO count = distinct PO numbers (PO3,PO4,PO5)", k["po_count"]["current"] == 3)
ct("avg PO value = spend ÷ POs", k["avg_po"]["current"] == round(1810 / 3, 2))
ct("active suppliers (أ,ب,ج)", k["suppliers"]["current"] == 3)
ct("open POs from status (PO5 = 250)", k["open_pos"]["current"] == 1 and k["open_pos"]["open_value"]["value"] == 250.0)
nost = analyze_purchases([L("2026-08-01", "أ", "بن", 1, 1, "X")])
ct("no status column → open POs unavailable (not 0)", nost["kpis"]["open_pos"]["current"] is None and nost["kpis"]["open_pos"]["reason_ar"])
nopo = analyze_purchases([L("2026-08-01", "أ", "بن", 1, 1, None)])
ct("no PO numbers → PO KPIs unavailable with reason", nopo["kpis"]["po_count"]["current"] is None and "أمر شراء" in nopo["kpis"]["po_count"]["reason_ar"])

print("[GROUP] spend analysis & concentration")
ct("spend by category with share", r["by_category"][0]["key"] == "بن" and r["by_category"][0]["share_pct"] == round(1360 / 1810 * 100, 2))
ct("spend by branch", {b["key"] for b in r["by_branch"]} == {"جدة", "الرياض"})
ct("top supplier أ (1300 = 71.82%)", r["top5"][0]["supplier"] == "أ" and r["concentration"]["top_share_pct"] == 71.82)
ct("concentration rule published + risk", r["concentration"]["at_risk"] and "40%" in r["concentration"]["rule_ar"])
ct("concentration signal", any(s["code"] == "supplier_concentration" for s in r["signals"]))
rr = analyze_purchases(rows, rules={"concentration_pct": 80})
ct("threshold is configurable", rr["concentration"]["at_risk"] is False)

print("[GROUP] scorecard: price / quality / delivery")
sa = {s["supplier"]: s for s in r["suppliers"]}
ct("price score only where ≥2 suppliers sell same product", sa["أ"]["price"] and sa["ج"]["price"] is None)
ct("أ cheaper than ب for coffee → higher price score", sa["أ"]["price"]["score"] > sa["ب"]["price"]["score"])
ct("quality = 1 − rejected ÷ received (أ: 2 of 140)", sa["أ"]["quality"]["acceptance_pct"] == round(138 / 140 * 100, 2))
ct("delivery: ب late 5 days", sa["ب"]["delivery"]["late_pct"] == 100.0 and sa["ب"]["delivery"]["avg_delay_days"] == 5.0)
ct("no delivery dates → delivery None (not 100%)", sa["ج"]["delivery"] is None and sa["ج"]["quality"] is None)
ct("PO cycle = received − order date", sa["ب"]["cycle"]["avg_days"] == 7.0)

print("[GROUP] price variance & savings")
pr = {p["product_sku"]: p for p in r["products"]}
# coffee Aug avg = (100*11+20*13)/120 = 11.333 ; baseline July avg of lines = (10+12)/2 = 11
ct("variance vs historical baseline", pr["بن"]["baseline_unit_cost"] == 11.0 and pr["بن"]["variance_pct"] == round((1360 / 120 - 11) / 11 * 100, 2))
ct("no baseline → saving not invented", pr["كوب"]["realized_saving"] is None)
ct("potential saving vs cheapest comparable supplier (ب pays 2 more × 20)", pr["بن"]["potential_saving"]["value"] == 40.0 and pr["بن"]["cheapest_supplier"] == "أ")
down = analyze_purchases([L("2026-07-01", "أ", "س", 10, 12, "A"), L("2026-08-01", "أ", "س", 100, 10.5, "B")])
ct("realized saving = (baseline − current) × qty = 150", down["savings"]["realized"]["value"] == 150.0)
ct("savings unavailable without history", analyze_purchases([L("2026-08-01", "أ", "س", 1, 1, "A")])["savings"]["available"] is False)
up = analyze_purchases([L("2026-07-01", "أ", "س", 10, 10, "A"), L("2026-08-01", "أ", "س", 10, 12, "B")])
ct("cost increase ≥10% → signal", any(s["code"] == "cost_increase" and s["dimension"] == "س" for s in up["signals"]))

print("[GROUP] cross-module (2.5 ↔ 2.6 ↔ 2.7)")
sales = [{"date": "2026-07-15", "category": "بن", "net_sales": 1000, "branch_name": "جدة"},
         {"date": "2026-08-15", "category": "بن", "net_sales": 1020, "branch_name": "جدة"}]
inv = [{"period": "2026-07", "product_sku": "بن", "branch_name": "جدة", "closing_value": 500},
       {"period": "2026-08", "product_sku": "بن", "branch_name": "جدة", "closing_value": 700}]
x = analyze_purchases([L("2026-07-01", "أ", "بن", 10, 10, "A"), L("2026-08-01", "أ", "بن", 15, 10, "B")], sales_rows=sales, inventory_snaps=inv)
c = x["cross"]["by_category"][0]
ct("purchases +50% vs sales +2% vs inventory +40%", c["purchases_change_pct"] == 50.0 and c["sales_change_pct"] == 2.0 and c["inventory_change_pct"] == 40.0)
ct("gap signal, relationship wording only", any(s["code"] == "purchase_sales_gap" for s in x["signals"]) and "لا تثبت" in x["cross"]["note_ar"])
ro = analyze_purchases(rows, reorder_needs=[{"product_sku": "بن", "branch": "جدة", "status": "low"}])
ct("inventory reorder need → supplier options with price/delivery/quality", {o["supplier"] for o in ro["reorder_options"][0]["options"]} == {"أ", "ب"})
ct("reorder options signal", any(s["code"] == "reorder_supplier_options" for s in ro["signals"]))

print("[GROUP] PO deep dive, data quality, safety")
po3 = next(p for p in r["po"]["list"] if p["po"] == "PO3")
ct("PO groups its lines", len(po3["lines"]) == 2 and po3["total"]["value"] == 1300.0 and po3["delay_days"] == 0)
ct("statuses only as they exist in data", set(r["po"]["statuses"]) == {"مستلم", "مفتوح"})
ct("missing supplier % reported + signal", r["data_quality"]["missing_supplier_pct"] == 20.0 and any(s["code"] == "supplier_missing" for s in r["signals"]))
ct("weekly grain works", analyze_purchases(rows, grain="week")["period"].startswith("2026-W"))
ct("deterministic ids", [s["id"] for s in analyze_purchases(rows)["signals"]] == [s["id"] for s in r["signals"]])
ct("no NaN/Infinity", "NaN" not in json.dumps(r, default=str) and "Infinity" not in json.dumps(r, default=str))
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "purchases_engine.py"), encoding="utf-8").read().lower())
print("[GROUP] workspace data (overview, 360s, drivers)")
ct("health rule-based: high concentration → critical", r["health"]["status"] == "critical" and r["health"]["risks"] >= 1 and "حرجة" in r["health"]["rule_ar"])
calm = analyze_purchases([L("2026-08-01", "أ", "س", 1, 10, "A"), L("2026-08-02", "ب", "ص", 1, 10, "B"), L("2026-08-03", "ج", "ع", 1, 10, "C")], rules={"concentration_pct": 50})
ct("no risk signals → stable", calm["health"]["status"] == "stable")
ct("signals prioritised: risks first, high before medium", [x["type"] for x in r["signals"]][0] == "risk" and r["signals"][0]["severity"] == "high")
ct("trend point carries growth + top supplier (for chart click)", r["trend"][-1]["top_supplier"] == "أ" and r["trend"][-1]["growth_pct"] is not None)
ct("top-2 concentration", r["concentration"]["top2"] == ["أ", "ب"])
up2 = analyze_purchases([L("2026-07-01", "أ", "س", 10, 10, "A"), L("2026-08-01", "أ", "س", 10, 12, "B")])
ci = next(x for x in up2["signals"] if x["code"] == "cost_increase")
ct("cost increase impact = (12−10) × 10 = 20", ci["estimated_impact"]["value"] == 20.0)
ct("price index on comparable products: +20%", up2["price_index"]["change_pct"] == 20.0 and up2["price_index"]["price_effect"]["value"] == 20.0)
ct("drivers: total delta + price effect", up2["drivers"]["total_delta"]["value"] == 20.0 and up2["drivers"]["price_effect"]["value"] == 20.0)
nc = analyze_purchases([L("2026-07-01", "أ", "س", 10, 10, "A"), L("2026-08-01", "أ", "ص", 10, 12, "B")])
ct("no comparable products → price effect not invented + note", nc["price_index"] is None and nc["drivers"]["price_note_ar"])
ct("drivers by category/branch/supplier", r["drivers"]["by_category"] and r["drivers"]["by_supplier"])
s360 = next(x for x in r["suppliers"] if x["supplier"] == "ب")
ct("supplier 360: late POs, top products, branches", s360["late_pos"] == 1 and s360["top_products"][0]["product_sku"] == "بن" and s360["branch_names"] == ["الرياض"])
ct("supplier 360: own price change vs its history (13 vs 12)", s360["price_changes"][0]["change_pct"] == round(1 / 12 * 100, 2))
ct("PO lifecycle only recorded steps + honest note", [x["step"] for x in po3["lifecycle"]] == ["created", "expected", "received"] and "غير مسجّلة" in po3["unrecorded_ar"])
fx = analyze_purchases([L("2026-07-01", "أ", "بن", 10, 10, "A"), L("2026-08-01", "أ", "بن", 15, 10, "B")], sales_rows=sales, inventory_snaps=inv)
ct("company-level flow sales/inventory/purchases", fx["flow"]["sales_change_pct"] == 2.0 and fx["flow"]["inventory_change_pct"] == 40.0 and fx["flow"]["purchases_change_pct"] == 50.0)

print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
