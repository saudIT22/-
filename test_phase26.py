"""Phase 2.6 — Inventory Intelligence tests. Run: python3 test_phase26.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inventory_engine import analyze_inventory, RULES
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")

def S(p, br, sku, close, val=None, sold=None, opening=None, purch=None, adj=None):
    return {"period": p, "branch_name": br, "product_sku": sku, "closing_qty": close, "closing_value": val,
            "sold_qty": sold, "opening_qty": opening, "purchases_qty": purch, "adjustments_qty": adj}

print("[GROUP] empty / honesty")
e = analyze_inventory([])
ct("no data → has_data False + what is required", e["has_data"] is False and "الكمية آخر المدة" in e["required"]["required"])
ct("empty message offers manual entry", "يدوياً" in e["message_ar"])

print("[GROUP] valuation, turnover, DIO (registry definitions)")
snaps = [S("2026-06", "جدة", "A", 100, 1000, sold=30), S("2026-07", "جدة", "A", 80, 800, sold=40),
         S("2026-08", "جدة", "A", 60, 600, sold=50),
         S("2026-06", "جدة", "B", 10, None, sold=0), S("2026-07", "جدة", "B", 10, None, sold=0),
         S("2026-08", "جدة", "B", 10, None, sold=0)]
prod = {"A": {"name": "قهوة", "category": "مشروبات", "cost": 10}, "B": {"name": "كوب", "category": "مستلزمات", "cost": 5}}
r = analyze_inventory(snaps, [], prod)
k = r["kpis"]
ct("inventory value = file value + qty×product cost (600 + 10×5)", k["inventory_value"]["current"] == 650.0)
# cogs window Jun–Aug: A (30+40+50)*10 = 1200 ; B 0 → 1200 ; avg value = ((1000+50)+(800+50)+(600+50))/3 = 850
ct("turnover = cogs ÷ average inventory value (1200 ÷ 850)", k["inventory_turnover"]["current"] == round(1200 / 850, 2))
days = 30 + 31 + 31
ct("DIO = avg value ÷ (cogs ÷ days)", k["dio"]["current"] == round(850 / (1200 / days)))
ct("turnover definition from metric registry", k["inventory_turnover"]["formula"] == "cogs ÷ average_inventory_value")
ct("window shown", r["turnover_detail"]["window"] == "2026-06 → 2026-08")
ct("accuracy: not available, not 0%", k["inventory_accuracy"]["current"] is None and "جرد" in k["inventory_accuracy"]["reason_ar"])

print("[GROUP] missing cost is not zero")
r2 = analyze_inventory([S("2026-08", "جدة", "X", 5)], [], {})
ct("unknown cost → value excludes it + counted as incomplete", r2["kpis"]["inventory_value"]["incomplete_items"] == 1)
ct("turnover unavailable with a reason", r2["kpis"]["inventory_turnover"]["current"] is None and r2["kpis"]["inventory_turnover"]["reason_ar"])
ct("valuation_incomplete data-quality signal", any(s["code"] == "valuation_incomplete" for s in r2["signals"]))
ct("availability tells how to fix", r2["availability"]["valuation"]["available"] is False and "يدوياً" in r2["availability"]["valuation"]["how_ar"])

print("[GROUP] stockout, slow, obsolete, low (explicit rules)")
sales = []
for d in range(1, 32):                                        # A sells 2/day in August in Jeddah
    sales.append({"date": f"2026-08-{d:02d}", "branch_name": "جدة", "product_sku": "A", "quantity": 2})
sales.append({"date": "2026-06-05", "branch_name": "جدة", "product_sku": "C", "quantity": 1})   # C last sold 5 Jun
sales.append({"date": "2026-01-10", "branch_name": "جدة", "product_sku": "D", "quantity": 1})   # D last sold Jan
sales += [{"date": f"2026-07-{d:02d}", "branch_name": "جدة", "product_sku": "E", "quantity": 3} for d in range(1, 32)]
snaps3 = [S("2026-07", "جدة", "E", 20, 200), S("2026-08", "جدة", "E", 0, 0),
          S("2026-08", "جدة", "A", 4, 40), S("2026-08", "جدة", "C", 40, 400), S("2026-08", "جدة", "D", 30, 300),
          S("2026-08", "جدة", "F", 500, 500)]
sales += [{"date": "2026-08-20", "branch_name": "جدة", "product_sku": "F", "quantity": 1}]
r3 = analyze_inventory(snaps3, sales, {})
pos = {p["product_sku"]: p for p in r3["positions"]}
ct("qty 0 → stockout", pos["E"]["status"] == "stockout")
ct("stockout rate = 1 of 5 items = 20%", r3["kpis"]["stockout_rate"]["current"] == 20.0)
ct("C: 87 days since last sale → slow", pos["C"]["status"] == "slow" and pos["C"]["days_since_movement"] == 87)
ct("D: ≥180 days → obsolete", pos["D"]["status"] == "obsolete")
ct("slow value 400, obsolete value 300", r3["kpis"]["slow_moving_value"]["current"] == 400.0 and r3["kpis"]["obsolete_value"]["current"] == 300.0)
ct("A: demand ≈ 62/90 per day, coverage ≤ 7 → low (no ROP)", pos["A"]["status"] == "low" and pos["A"]["reorder_point"] is None)
ct("F: plenty of cover → healthy", pos["F"]["status"] == "healthy")
ct("aging buckets by last movement", next(a for a in r3["aging"] if a["bucket"] == "180+")["value"]["value"] == 300.0)
ct("no ROP → reason names lead time", "مدة توريد" in pos["A"]["reorder_point_reason"])
ct("no params → suggested qty not invented + reason", pos["A"]["suggested_qty"] is None and "مدة التوريد" in pos["A"]["suggested_reason"])
so = next(s for s in r3["stockouts"] if s["product_sku"] == "E")
ct("stockout linked to sales (2.5 → 2.6)", so["out_now"] and so["sales_impact"] is None)   # E had no August sales → cur None
ct("excess inventory signal with impact value", any(s["code"] == "excess_inventory" and s["estimated_impact"]["value"] == 700.0 for s in r3["signals"]))
ct("stockout-risk signal for A", any(s["code"] == "stockout_risk" for s in r3["signals"]))

print("[GROUP] reorder point & suggested quantity")
params = {("A", "جدة"): {"lead_time_days": 10, "safety_stock": 5, "min_order_qty": 12}}
r4 = analyze_inventory(snaps3, sales, {}, params)
a = next(p for p in r4["positions"] if p["product_sku"] == "A")
avg = 62 / 90
ct("ROP = avg daily × lead time + safety stock", abs(a["reorder_point"] - round(avg * 10 + 5, 2)) < 0.01 and a["reorder_point_source"] == "calculated")
ct("qty 4 ≤ ROP → low", a["status"] == "low")
raw = avg * 10 + 5 + avg * 10 - 4
import math
ct("suggested qty rounded UP to MOQ multiple", a["suggested_qty"] == math.ceil(raw / 12) * 12)
r5 = analyze_inventory(snaps3, sales, {}, {("A", None): {"reorder_point": 50}})
ct("manual ROP respected (company-wide param)", next(p for p in r5["positions"] if p["product_sku"] == "A")["reorder_point_source"] == "manual")

print("[GROUP] movement, consumption from sales, filters")
m = analyze_inventory([S("2026-08", "جدة", "A", 70, 700, sold=40, opening=100, purch=20)], [], {})
ct("reconciliation gap detected (100+20−40 ≠ 70)", m["movement"]["reconciliation_gap"] == 10.0 and any(s["code"] == "movement_gap" for s in m["signals"]))
ok = analyze_inventory([S("2026-08", "جدة", "A", 80, 800, sold=40, opening=100, purch=20)], [], {})
ct("balanced movement → gap 0, no signal", ok["movement"]["reconciliation_gap"] == 0.0 and not any(s["code"] == "movement_gap" for s in ok["signals"]))
cs = analyze_inventory([S("2026-07", "جدة", "A", 100, 1000), S("2026-08", "جدة", "A", 70, 700)],
                       [{"date": "2026-08-03", "branch_name": "جدة", "product_sku": "A", "quantity": 30}], {})
ct("sold qty missing in file → consumption taken from sales", cs["movement"]["sold_qty"] == 30.0 and cs["kpis"]["inventory_turnover"]["current"] is not None)
fl = analyze_inventory(snaps3, sales, {}, status_filter="slow")
ct("status filter", [p["product_sku"] for p in fl["positions"]] == ["C"])
ct("by branch/category present", r3["by_branch"][0]["key"] == "جدة" and r3["by_category"][0]["key"] == "غير مصنّف")
ct("deterministic signal ids", [s["id"] for s in analyze_inventory(snaps3, sales, {})["signals"]] == [s["id"] for s in r3["signals"]])
ct("no NaN / Infinity", "NaN" not in json.dumps(r3, default=str) and "Infinity" not in json.dumps(r3, default=str))
ct("rules are published", "180" in r3["rules"]["obsolete"])
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory_engine.py"), encoding="utf-8").read().lower())
print("[GROUP] a stocked-out item must not break turnover (regression)")
so_snaps = [S("2026-07", "جدة", "A", 50, 500, sold=20), S("2026-08", "جدة", "A", 0, None, sold=50),
            S("2026-07", "جدة", "B", 10, 50, sold=5), S("2026-08", "جدة", "B", 8, 40, sold=2)]
rs = analyze_inventory(so_snaps, [], {})
ct("turnover computed even when one item is out of stock", rs["kpis"]["inventory_turnover"]["current"] is not None)
ct("cost of the stocked-out item derived from its other balances", not any(s["code"] == "valuation_incomplete" for s in rs["signals"]))

print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
