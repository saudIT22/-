"""سلسلة اختبار كاملة لعرض المجلس فوق risk_fixture: مخاطر → مسببات → تنبؤ → أهداف → قرارات."""
import copy
import risk_fixture as FX, risk_engine as R, drivers_engine as DE, prediction_engine as PE, goals_engine as G, decisions_engine as DX, dec_fixture as DF

Y = {"start": "2026-01-01", "end": "2026-12-31"}
GOALS = [{"id": 1, "name": "الإيراد السنوي", "metric": "revenue", "target": 1_900_000, **Y, "owner": "سعود", "pillar": "growth", "priority": "high"},
         {"id": 2, "name": "صافي الربح", "metric": "net_profit", "target": 150000, **Y, "owner": "المالية", "pillar": "profitability", "priority": "high"},
         {"id": 4, "name": "استرداد التسرب", "metric": "leakage_recovery", "target": 500000, **Y, "owner": "المالية", "pillar": "profitability"},
         {"id": 7, "name": "OKR الربحية", "objective": "رفع الربحية", **Y, "pillar": "profitability", "owner": "سعود"}]
KRS = [{"id": 1, "goal_id": 7, "metric": "net_margin", "baseline": 9.7, "target": 15}]
ACTS = [{"id": 1, "goal_id": 4, "action": "استرداد الخصومات", "status": "done", "expected_impact": 100000, "actual_impact": 80000}]


def build(stressed=True, viewer=None, budget=None):
    T = FX.TODAY
    D = FX.build(stressed=stressed); M = FX.run_modules(D)
    risk = R.analyze_risk(M, customer_rows=D["sales"], sector="fnb", today=T)
    drv = DE.analyze_drivers(risk, M, customer_rows=D["sales"], sector="fnb", today=T)
    pred = PE.analyze_prediction(M, sales_rows=D["sales"], risk=risk, drivers=drv, settings={"targets": {"annual_revenue": 2_000_000, "annual_profit": 150000}}, sector="fnb", today=T)
    ctx = G.build_context(M, D["sales"], risk=risk, drivers=drv, pred=pred, today=T)
    gres = G.analyze_goals(copy.deepcopy(GOALS), krs=KRS, actions=ACTS, mods=M, sales_rows=D["sales"], risk=risk, drivers=drv, pred=pred,
                           strategy={"vision": "x", "pillars": ["profitability", "growth", "customer"]}, sector="fnb", today=T, ctx=ctx)
    dres = DX.analyze_decisions(DF.decisions(), sector="fnb", today=T, ctx=ctx)
    return {"T": T, "D": D, "M": M, "risk": risk, "drv": drv, "pred": pred, "ctx": ctx, "gres": gres, "dres": dres,
            "budget": budget if budget is not None else {"revenue_monthly": 160000, "opex_monthly": {"payroll": 30000, "rent": 12000}}}
