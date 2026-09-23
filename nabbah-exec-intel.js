/* NABBAH 2.3 — Executive Intelligence panel (reusable, AR/EN, RTL/LTR).
   Renders into #nbExecIntel. Data: GET /company/executive-intelligence.
   Actual values vs estimates are labelled. No numbers are computed here. */
(function () {
  const root = document.getElementById("nbExecIntel");
  if (!root) return;
  const TOKEN = localStorage.getItem("nabbah_token") || "";
  const LANG = (localStorage.getItem("nabbah_lang") || document.documentElement.lang || "ar").startsWith("en") ? "en" : "ar";
  const T = {
    ar: { title: "الذكاء التنفيذي", loading: "جارٍ التحليل…", error: "تعذّر تحميل التحليل", empty: "لا توجد بيانات للفترة بعد — أدخل بيانات الفروع.",
      period: "الفترة", vsPrev: "مقارنة بـ", sales: "صافي المبيعات", profit: "الربح", margin: "الهامش", growth: "النمو",
      revTarget: "المبيعات مقابل الهدف", profTarget: "الربح مقابل الهدف", notSet: "لم يُحدَّد هدف", gap: "الفرق", attain: "نسبة التحقيق",
      branches: "أداء الفروع", risks: "المخاطر", opps: "الفرص", recs: "التوصيات", dq: "تحذيرات جودة البيانات",
      forecast: "التوقع للشهر القادم", base: "أساسي", best: "متفائل", worst: "متشائم", conf: "الثقة",
      insufficient: "لا يمكن التوقع", estimate: "تقديري", actual: "فعلي", rule: "القاعدة", impact: "الأثر",
      toDecision: "حوّل لقرار", owner: "المسؤول", due: "الموعد", create: "إنشاء القرار", created: "تم إنشاء القرار والمهام ✓",
      onlyOwner: "إنشاء القرارات للمالك فقط", openDec: "قرارات مفتوحة", overdue: "مهام متأخرة", none: "لا شيء",
      notEval: "لم يُقيَّم (بيانات غير متوفرة)", gate: { ALLOW: "البيانات كافية", QUALIFY: "توصيات مشروطة — راجع التحذيرات", BLOCK: "البيانات غير كافية — التوصيات المالية معلّقة" } },
    en: { title: "Executive Intelligence", loading: "Analyzing…", error: "Could not load the analysis", empty: "No data for this period yet — enter branch data.",
      period: "Period", vsPrev: "vs", sales: "Net sales", profit: "Profit", margin: "Margin", growth: "Growth",
      revTarget: "Revenue vs target", profTarget: "Profit vs target", notSet: "No target set", gap: "Gap", attain: "Attainment",
      branches: "Branch performance", risks: "Risks", opps: "Opportunities", recs: "Recommendations", dq: "Data quality warnings",
      forecast: "Next-month forecast", base: "Base", best: "Best", worst: "Worst", conf: "Confidence",
      insufficient: "Cannot forecast", estimate: "Estimate", actual: "Actual", rule: "Rule", impact: "Impact",
      toDecision: "Make a decision", owner: "Owner", due: "Due", create: "Create decision", created: "Decision and tasks created ✓",
      onlyOwner: "Only the owner can create decisions", openDec: "Open decisions", overdue: "Overdue tasks", none: "None",
      notEval: "Not evaluated (no data)", gate: { ALLOW: "Data sufficient", QUALIFY: "Recommendations qualified — see warnings", BLOCK: "Insufficient data — financial recommendations on hold" } },
  }[LANG];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString(LANG === "ar" ? "ar-SA" : "en-US", { maximumFractionDigits: 2 }));
  const money = (m) => (m && m.value != null ? fmt(m.value) : "—");
  const SEVL = LANG === "ar" ? { critical: "حرج", high: "مرتفع", medium: "متوسط", low: "منخفض" } : { critical: "critical", high: "high", medium: "medium", low: "low" };
  const CONFL = LANG === "ar" ? { high: "عالية", medium: "متوسطة", low: "منخفضة" } : { high: "high", medium: "medium", low: "low" };
  const NEL = LANG === "ar" ? { declining_sales: "تراجع المبيعات", high_inventory: "المخزون", low_repeat: "تكرار الشراء" } : {};
  const SEV = { critical: "#dc2626", high: "#ea580c", medium: "#ca8a04", low: "#16a34a" };
  const badge = (txt, color) => `<span style="font-size:11px;padding:2px 8px;border-radius:10px;background:${color}22;color:${color};font-weight:700">${esc(txt)}</span>`;
  const est = () => badge(T.estimate, "#7c3aed");
  const card = (inner) => `<div style="background:var(--panel,#101a15);color:var(--txt,#e8f1ec);border:1px solid var(--line,#1d2c24);border-radius:14px;padding:14px;margin:10px 0">${inner}</div>`;
  const I18 = window.NabbahI18n; const tr = (k, fb) => { const v = I18 && I18.t ? I18.t(k, LANG) : k; return v && v !== k ? v : fb; };
  const loc = (x) => (x && x.texts && (x.texts[LANG] || x.texts.ar)) || null;
  const tech = (x, tx) => `<details style="margin-top:4px;font-size:11px;opacity:.75"><summary style="cursor:pointer">${tr("rule.details", LANG === "ar" ? "التفاصيل الفنية" : "Technical details")}</summary>
      ${tr("rule.technical", "Rule")}: <code>${esc(x.rule_code || x.code || "")}</code> · ${esc(x.technical_expression || x.method || "")}
      ${x.threshold ? ` · ${tr("rule.threshold", "Threshold")}: ${esc(x.threshold)}` : ""}${tx && tx.impact_formula ? ` · ${tr("rule.formula", "Formula")}: ${esc(tx.impact_formula)}` : ""}</details>`;
  const h = (t) => `<div style="font-weight:800;margin-bottom:8px">${esc(t)}</div>`;
  root.setAttribute("dir", LANG === "ar" ? "rtl" : "ltr");
  root.innerHTML = card(`<div>${T.loading}</div>`);

  fetch("/company/executive-intelligence", { headers: { Authorization: "Bearer " + TOKEN } })
    .then((r) => (r.status === 403 || r.status === 402 ? null : r.ok ? r.json() : Promise.reject(r.status)))
    .then((d) => { if (d) render(d); else root.innerHTML = ""; })
    .catch(() => { root.innerHTML = card(`<div style="color:#dc2626">${T.error}</div>`); });

  function render(d) {
    if (!d.has_data) { root.innerHTML = card(h(T.title) + `<div>${T.empty}</div>` + dq(d)); return; }
    const s = d.summary;
    const kpi = (label, val, extra = "") => `<div style="flex:1;min-width:120px"><div style="font-size:12px;opacity:.7">${label}</div><div style="font-size:20px;font-weight:800">${val}</div>${extra}</div>`;
    let html = h(`${T.title} · ${T.period} ${esc(d.period)}${d.comparison_period ? ` (${T.vsPrev} ${esc(d.comparison_period)})` : ""}`);
    html += `<div style="display:flex;gap:10px;flex-wrap:wrap">${kpi(T.sales, money(s.net_sales))}${kpi(T.profit, money(s.profit))}${kpi(T.margin, s.margin_pct == null ? "—" : fmt(s.margin_pct) + "%")}${kpi(T.growth, s.growth_pct == null ? "—" : fmt(s.growth_pct) + "%")}${kpi(T.openDec, s.open_decisions)}${kpi(T.overdue, s.overdue_actions)}</div>`;
    html += gateLine(d);
    const tgt = (label, x) => x.status !== "ok" ? `<div>${label}: <i>${T.notSet}</i></div>` :
      `<div>${label}: ${T.actual} ${money(x.actual)} / ${money(x.target)} — ${x.attainment_pct != null ? T.attain + " " + fmt(x.attainment_pct) + "%" : T.gap + " " + money(x.gap)}</div>`;
    html += card(tgt(T.revTarget, d.revenue_vs_target) + tgt(T.profTarget, d.profit_vs_target));
    html += card(h(T.branches) + `<table style="width:100%;font-size:13px"><tr><th></th><th>${T.sales}</th><th>${T.margin}</th><th>${T.growth}</th><th>${T.attain}</th></tr>` +
      d.branch_performance.map((b) => `<tr><td>${esc(b.name)}</td><td>${money(b.sales)}</td><td>${b.margin == null ? "—" : fmt(b.margin) + "%"}</td><td>${b.growth == null ? "—" : fmt(b.growth) + "%"}</td><td>${b.target_attainment == null ? "—" : fmt(b.target_attainment) + "%"}</td></tr>`).join("") + `</table>`);
    html += card(h(T.risks) + list(d.risks) + (d.not_evaluated.length ? `<div style="font-size:12px;opacity:.7;margin-top:6px">${T.notEval}: ${d.not_evaluated.map((x) => esc(NEL[x.code] || x.code)).join(", ")}</div>` : ""));
    html += card(h(T.opps) + list(d.opportunities));
    html += card(h(T.recs) + (d.recommendations.length ? d.recommendations.map(recHtml).join("") : T.none));
    html += forecastHtml(d.forecast) + dq(d);
    root.innerHTML = card(html);
    root.querySelectorAll("[data-sig]").forEach((btn) => btn.addEventListener("click", () => openForm(btn)));
    root.querySelectorAll("[data-mem]").forEach((btn) => btn.addEventListener("click", () => memory(btn)));
  }
  function gateLine(d) {
    const g = d.data_quality.gate; const c = g === "ALLOW" ? "#16a34a" : g === "QUALIFY" ? "#ca8a04" : "#dc2626";
    return `<div style="margin-top:8px">${badge(T.gate[g], c)}</div>`;
  }
  function list(items) {
    if (!items.length) return T.none;
    return items.map((x) => { const tx = loc(x); return `<div style="border-${LANG === "ar" ? "right" : "left"}:4px solid ${SEV[x.severity]};padding:6px 10px;margin:6px 0">
      <b>${esc(tx ? tx.title : (LANG === "ar" ? x.name_ar : x.name_en))}</b>${x.branch_name ? " — " + esc(x.branch_name) : ""} ${badge(SEVL[x.severity] || x.severity, SEV[x.severity])}
      <div style="font-size:12px">${esc(tx ? tx.description : x.description_ar)}</div>
      <div style="font-size:12px;opacity:.85">${tr("rule.kpi", "KPI")}: ${esc(tx ? tx.kpi : x.metric_id)}${x.current_value != null ? ` · ${tr("rule.current", "Current")}: ${fmt(x.current_value)}` : ""}</div>
      ${tx && tx.action ? `<div style="font-size:12px">${tr("rule.action", "Action")}: ${esc(tx.action)}</div>` : ""}${tech(x, tx)}
      ${x.estimated_impact ? `<div style="font-size:12px">${T.impact}: ${money(x.estimated_impact)} ${esc(x.estimated_impact.currency)} ${est()}</div>` : ""}</div>`; }).join("");
  }
  function recHtml(r) {
    const imp = r.expected_impact ? `${T.impact}: ${money(r.expected_impact)} ${est()}` : "";
    const tx = loc(r);
    return `<div style="border:1px solid var(--line,#1d2c24);border-radius:10px;padding:8px 10px;margin:6px 0">
      ${badge(r.priority, r.priority === "P1" ? "#dc2626" : "#ca8a04")} <b>${esc(tx ? tx.title : (LANG === "ar" ? r.problem_ar : r.problem_en))}</b>
      <div style="font-size:12px">${esc(tx ? tx.description : r.evidence.detail_ar)}</div>
      <div style="font-size:13px;margin-top:4px">→ ${esc(tx && tx.action ? tx.action : (LANG === "ar" ? r.recommendation_ar : r.recommendation_en))}</div>${tech(r, tx)}
      <button data-mem="${esc(r.metric_id || "")}|${esc(r.problem_type || "")}|${r.branch_id || ""}" style="margin-top:6px;padding:3px 10px;border-radius:8px;cursor:pointer;background:transparent;color:var(--txt-soft,#8ba396);border:1px solid var(--line,#1d2c24)">${tr("dm.open", "Past decisions")}</button><div class="nbMem" style="font-size:12px"></div>
      <div style="font-size:12px;opacity:.8">${imp} · ${T.conf}: ${esc(CONFL[r.confidence] || r.confidence)}</div>
      ${r.status === "requires_data_first" ? `<div style="font-size:12px;color:#dc2626">${esc(r.limitations[0])}</div>` :
        `<button data-sig="${esc(r.signal_id)}" style="margin-top:6px;padding:4px 12px;border-radius:8px;cursor:pointer">${T.toDecision}</button><div class="nbForm"></div>`}</div>`;
  }
  function forecastHtml(f) {
    const s = f.sales;
    if (s.status !== "ok") return card(h(T.forecast) + `<div>${T.insufficient}: ${esc((loc(s) || {}).description || s.reason || "")} ${s.missing_months && s.missing_months.length ? "(" + s.missing_months.join(", ") + ")" : ""}</div>`);
    return card(h(`${T.forecast} (${esc(s.forecast_period)}) `) + est() +
      `<div style="display:flex;gap:10px;margin-top:6px"><div>${T.worst}: <b>${money(s.worst)}</b></div><div>${T.base}: <b>${money(s.base)}</b></div><div>${T.best}: <b>${money(s.best)}</b></div></div>
       <div style="font-size:12px;opacity:.8">${T.conf}: ${esc(CONFL[s.confidence] || s.confidence)} · ${((s.assumptions_texts && s.assumptions_texts[LANG]) || s.assumptions).map(esc).join(" · ")}</div>`);
  }
  function dq(d) {
    const w = d.data_quality.warnings; if (!w.length) return "";
    return card(h(T.dq) + w.map((x) => `<div style="font-size:12px;margin:4px 0">${badge(SEVL[x.severity] || x.severity, SEV[x.severity] || "#6b7280")} ${esc((loc(x) || {}).description || (LANG === "ar" ? x.message_ar : x.message_en))}</div>`).join(""));
  }
  function memory(btn) {
    const [metric, problem, branch] = btn.dataset.mem.split("|"); const box = btn.nextElementSibling;
    const q = new URLSearchParams({ metric_id: metric, problem_type: problem }); if (branch) q.set("branch_id", branch);
    box.textContent = "…";
    fetch("/company/decision-memory?" + q, { headers: { Authorization: "Bearer " + TOKEN } }).then((r) => (r.ok ? r.json() : null)).then((m) => {
      if (!m) { box.textContent = T.error; return; }
      box.innerHTML = (m.items.length ? m.items.map((x) => `<div style="margin:4px 0">• ${esc(x.title)} — ${badge(tr("dm.status." + x.outcome_status, x.outcome_status), "#a855f7")} ${x.actual_change != null ? `(${fmt(x.actual_change)})` : ""}<div style="opacity:.7">${x.reasons.map((r) => tr("dm.reason." + r, r)).join(" · ")}</div></div>`).join("")
        : `<div>${tr("dm.none", "—")}</div>`) + `<div style="opacity:.7;margin-top:4px">${esc(m.caveat[LANG] || m.caveat.ar)}</div>`;
    }).catch(() => { box.textContent = T.error; });
  }
  function openForm(btn) {
    const box = btn.nextElementSibling;
    box.innerHTML = `<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap"><input placeholder="${T.owner}" class="o" style="flex:1"><input type="date" class="d"><button class="c">${T.create}</button></div><div class="m" style="font-size:12px"></div>`;
    box.querySelector(".c").addEventListener("click", () => {
      fetch("/company/recommendations/to-decision", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + TOKEN },
        body: JSON.stringify({ signal_id: btn.dataset.sig, owner: box.querySelector(".o").value, due_date: box.querySelector(".d").value }) })
        .then((r) => { box.querySelector(".m").textContent = r.ok ? T.created : r.status === 403 ? T.onlyOwner : T.error; });
    });
  }
})();
