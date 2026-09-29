"""Phase 2.5 v1.1 — full sales intelligence spec tests. Run: python3 test_phase25_v11.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sales_engine import analyze_sales
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def R(d, b, ref, g, ch=None, sku=None, cat=None, disc=0, ret=0, promo=None, net=None):
    return {"date": d, "branch_name": b, "reference": ref, "gross_sales": g, "discounts": disc, "returns": ret,
            "channel": ch, "product_sku": sku, "category": cat, "quantity": 1, "net_sales": net, "promotion": promo}

cur = [R("2026-09-01", "جدة", "A1", 60000, "POS", "قهوة", "مشروبات", 3000, 500),
       R("2026-09-02", "الرياض", "A2", 40000, "Delivery", "حلى", "حلويات", 1000, 0),
       R("2026-09-03", "مكة", "A3", 20000, None, "قهوة", "مشروبات", 0, 2000)]
prev = [R("2026-08-01", "جدة", "B1", 50000, "POS", "قهوة", "مشروبات"),
        R("2026-08-02", "الرياض", "B2", 38000, "Delivery", "حلى", "حلويات"),
        R("2026-08-03", "مكة", "B3", 30000, "POS", "قهوة", "مشروبات")]
r = analyze_sales(cur, cur + prev, period="2026-09")

print("[GROUP] 1 — KPIs: current → comparison → change")
k = r["kpis"]
ct("all nine KPIs present", all(c in k for c in ("net_sales", "gross_sales", "transactions", "aov", "quantity",
                                                 "discounts", "discount_rate", "returns", "return_rate")))
ct("net sales change = current − previous", k["net_sales"]["current"] == 113500 and k["net_sales"]["previous"] == 118000
   and k["net_sales"]["change"] == -4500)
ct("net sales change % matches comparison", k["net_sales"]["change_pct"] == r["comparison"]["previous"]["growth_pct"])
ct("rates change in points, not % of %", k["discount_rate"]["change_unit"] == "points" and k["discount_rate"]["change_pct"] is None)
only = analyze_sales(cur, cur, period="2026-09")
ct("no previous period -> unavailable with reason (not zero)",
   only["kpis"]["net_sales"]["comparison_available"] is False and only["kpis"]["net_sales"]["change"] is None
   and only["kpis"]["net_sales"]["reason_ar"])

print("[GROUP] 6 — product intelligence")
pi = r["product_insights"]
ct("top5 ranked by net sales", [p["key"] for p in pi["top5"]] == ["قهوة", "حلى"])
ct("growing = positive growth only", all(p["growth_pct"] > 0 for p in pi["growing"]))
ct("declining = negative growth only", all(p["growth_pct"] < 0 for p in pi["declining"]))
ct("coffee declined (77.5k vs 80k)", [p["key"] for p in pi["declining"]] == ["قهوة"])
ct("dessert grew (39k vs 38k)", [p["key"] for p in pi["growing"]] == ["حلى"])
ct("contribution present", all(p["contribution_pct"] is not None for p in pi["top5"]))
ct("no comparison -> note, empty lists", only["product_insights"]["growing"] == [] and only["product_insights"]["note_ar"])

print("[GROUP] 7 — discounts intelligence")
dc = r["discounts"]
ct("discounts by branch ranked by discount value", [x["key"] for x in dc["by_branch"]] == ["جدة", "الرياض"])
ct("branch discount rate = disc ÷ gross of that branch", dc["by_branch"][0]["discount_rate_pct"] == 5.0)
ct("discounts by product", dc["by_product"][0]["key"] == "قهوة")
ct("discount trend covers both months", [x["period"] for x in dc["trend"]] == ["2026-08", "2026-09"])
ct("zero-discount month rate is 0 (real zero, data exists)", dc["trend"][0]["rate_pct"] == 0.0)

print("[GROUP] 9 — returns intelligence")
rt = r["returns"]
ct("returns by branch ranked by returns value", [x["key"] for x in rt["by_branch"]] == ["مكة", "جدة"])
ct("returns by_branch net_sales is real net (bug fixed)", rt["by_branch"][0]["net_sales"]["value"] == 18000)
ct("branch return rate", rt["by_branch"][0]["return_rate_pct"] == 10.0)
ct("returns trend present", len(rt["trend"]) == 2)

print("[GROUP] 8 — promotions")
ct("no promo column -> unavailable + how to fix", r["promotions"]["available"] is False and "الحملة" in r["promotions"]["reason_ar"])
base = [R(f"2026-09-{d:02d}", "جدة", f"P{d}", 1000) for d in range(1, 11)]
promo = [R(f"2026-09-{d:02d}", "جدة", f"Q{d}", 1500, disc=100, promo="العودة للمدارس") for d in (11, 12, 13)]
after = [R(f"2026-09-{d:02d}", "جدة", f"S{d}", 1100) for d in (14, 15, 16)]
pr = analyze_sales(base + promo + after, base + promo + after, period="2026-09")["promotions"]
it = pr["items"][0]
ct("promotion detected", pr["available"] and it["promotion"] == "العودة للمدارس" and it["days"] == 3)
ct("during = net of promo rows", it["during"]["value"] == 4200)
ct("before = equal window before start", it["before"]["value"] == 3000 and it["before_days_with_data"] == 3)
ct("after = equal window after end", it["after"]["value"] == 3300)
ct("discount cost reported", it["discount_cost"]["value"] == 300)
ct("no causation claim", "لا تثبت" in pr["note_ar"])

print("[GROUP] 10 — forecast (same engine as 2.3)")
ct("2 months -> insufficient, no fabricated number", r["forecast"]["status"] == "insufficient_data" and "base" not in r["forecast"])
long = []
for m in range(1, 8):
    long.append(R(f"2026-{m:02d}-10", "جدة", f"L{m}", 10000 + m * 1000))
f = analyze_sales([x for x in long if x["date"].startswith("2026-07")], long, period="2026-07")["forecast"]
ct("enough months -> forecast for next month", f["status"] == "ok" and f["forecast_period"] == "2026-08")
ct("forecast marked estimate with confidence", f["is_estimate"] is True and f["confidence"] in ("low", "medium", "high"))
ct("actual series attached for the chart", len(f["actual_series"]) == 7)

print("[GROUP] 11/16 — data quality signals")
sig = {s["code"]: s for s in r["signals"]}
ct("missing channel -> data_quality signal", sig["missing_channel_data"]["type"] == "data_quality")
ct("evidence states the share %", "33.33%" in sig["missing_channel_data"]["evidence"][0])
cm = r["completeness"]["fields"]
ct("completeness per field", cm["channel"]["missing"] == 1 and cm["channel"]["missing_pct"] == 33.33 and cm["date"]["missing"] == 0)

print("[GROUP] safety")
ct("no NaN/Infinity", "NaN" not in json.dumps(r, default=str) and "Infinity" not in json.dumps(r, default=str))
ct("version bumped", r["version"] == "sales-v1.1")
empty = analyze_sales([], [])
ct("empty -> no new sections invented", "kpis" not in empty and "forecast" not in empty)

print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
