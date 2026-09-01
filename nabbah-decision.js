/* ═══════════════════════════════════════════════════════════
   نبّاه — رحلة القرار (Nabbah Decision Journey)
   من رؤية V2 (البند ٣١): كل تحليل قابل للتحويل إلى قرار متتبَّع.
   شريط يظهر أسفل الصفحات التحليلية:
     "شفت المشكلة؟ حوّلها لقرار" → المسؤول + الموعد + KPI
   يربط بـ endpoint /company/decisions/save الموجود.
   آمن: طبقة اختيارية، لا تلمس محتوى الصفحة.
   ═══════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const here = (location.pathname.split("/").pop() || "").toLowerCase();
  // يظهر فقط في الصفحات التحليلية (حيث القرار منطقي)
  const SHOW = ["company-command-center.html", "company-health.html", "company-finance.html",
                "company-leakage.html", "company-risks.html", "company-branches.html",
                "company-cashflow.html", "company-sales.html", "company-root-cause.html",
                "company-predictions.html", "company-hr-analytics.html"];
  if (!SHOW.includes(here)) return;

  const TOKEN = localStorage.getItem("nabbah_token") || "";
  if (!TOKEN) return;

  const css = `
    #nbDecBar{max-width:1080px;margin:20px auto 40px;padding:0 18px}
    #nbDecCard{background:linear-gradient(135deg,rgba(16,185,129,.08),transparent);border:1px solid rgba(16,185,129,.25);
      border-radius:16px;padding:18px 20px;font-family:'Tajawal',sans-serif}
    #nbDecCard .h{display:flex;align-items:center;gap:10px;margin-bottom:4px}
    #nbDecCard .h .ic{font-size:20px}
    #nbDecCard .h .t{font-weight:800;font-size:15px;color:#10b981}
    #nbDecCard .sub{font-size:12.5px;color:#8ba396;margin-bottom:14px}
    #nbDecForm{display:none;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
    #nbDecForm.show{display:grid}
    #nbDecForm .full{grid-column:1/-1}
    #nbDecForm label{display:block;font-size:11.5px;color:#8ba396;font-weight:600;margin-bottom:4px}
    #nbDecForm input{width:100%;background:#101a15;border:1px solid #1d2c24;color:#e8f1ec;border-radius:10px;
      padding:10px 12px;font-size:13px;font-family:inherit;outline:none}
    #nbDecForm input:focus{border-color:#10b981}
    .nbDecBtn{background:linear-gradient(135deg,#10b981,#0b8457);color:#fff;border:none;border-radius:11px;
      padding:11px 20px;font-weight:800;font-size:13.5px;cursor:pointer;font-family:inherit}
    .nbDecBtn.ghost{background:#101a15;border:1px solid #1d2c24;color:#8ba396}
    #nbDecMsg{font-size:12.5px;margin-top:10px;font-weight:600}
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const bar = document.createElement("div");
  bar.id = "nbDecBar";
  bar.innerHTML = `
    <div id="nbDecCard">
      <div class="h"><span class="ic">⚡</span><span class="t">شفت ما يحتاج قرار؟ حوّله لإجراء متتبَّع</span></div>
      <div class="sub">نبّاه يسجّل خط الأساس تلقائياً ويقيس أثر القرار لاحقاً (المبيعات قبل/بعد).</div>
      <button class="nbDecBtn" id="nbDecOpen">⚡ اعتمد قراراً جديداً</button>
      <div id="nbDecForm">
        <div class="full">
          <label>القرار</label>
          <input id="nbDecTitle" placeholder="مثال: خفض تكلفة المشتريات 12%" />
        </div>
        <div>
          <label>المسؤول</label>
          <input id="nbDecOwner" placeholder="مثال: مدير العمليات" />
        </div>
        <div>
          <label>موعد الإنجاز</label>
          <input id="nbDecDue" type="date" />
        </div>
        <div class="full">
          <label>مؤشر النجاح (KPI)</label>
          <input id="nbDecKpi" placeholder="مثال: خفض التكلفة 45,000 ريال شهرياً" />
        </div>
        <div class="full" style="display:flex;gap:10px;margin-top:4px">
          <button class="nbDecBtn" id="nbDecSave">حفظ القرار للمتابعة</button>
          <button class="nbDecBtn ghost" id="nbDecCancel">إلغاء</button>
        </div>
        <div id="nbDecMsg" class="full"></div>
      </div>
    </div>`;

  // نحقنه في نهاية المحتوى
  function inject() {
    if (document.getElementById("nbDecBar")) return;
    const wrap = document.querySelector(".wrap") || document.body;
    wrap.appendChild(bar);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else { inject(); }
  setTimeout(inject, 400);

  document.addEventListener("click", async (e) => {
    if (e.target.id === "nbDecOpen") {
      document.getElementById("nbDecForm").classList.add("show");
      e.target.style.display = "none";
    }
    if (e.target.id === "nbDecCancel") {
      document.getElementById("nbDecForm").classList.remove("show");
      document.getElementById("nbDecOpen").style.display = "inline-block";
    }
    if (e.target.id === "nbDecSave") {
      const title = document.getElementById("nbDecTitle").value.trim();
      const owner = document.getElementById("nbDecOwner").value.trim();
      const due = document.getElementById("nbDecDue").value;
      const kpi = document.getElementById("nbDecKpi").value.trim();
      const msg = document.getElementById("nbDecMsg");
      if (!title) { msg.style.color = "#ef4444"; msg.textContent = "اكتب القرار أولاً"; return; }
      e.target.disabled = true;
      msg.style.color = "#8ba396"; msg.textContent = "جارٍ الحفظ…";
      try {
        const r = await fetch("/company/decisions/save", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Authorization": "Bearer " + TOKEN },
          body: JSON.stringify({ title, owner, due_date: due, kpi, source: here }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || "تعذّر الحفظ");
        msg.style.color = "#10b981";
        msg.innerHTML = "✓ اعتُمد القرار! <a href='company-decisions.html' style='color:#10b981;text-decoration:underline'>تابعه في مركز القرارات ←</a>";
        document.getElementById("nbDecTitle").value = "";
        document.getElementById("nbDecOwner").value = "";
        document.getElementById("nbDecKpi").value = "";
      } catch (err) {
        msg.style.color = "#ef4444"; msg.textContent = err.message;
      }
      e.target.disabled = false;
    }
  });
})();
