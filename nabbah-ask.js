/* ═══════════════════════════════════════════════════════════
   نبّاه — اسأل نبّاه (Nabbah Ask — البحث الذكي)
   من رؤية V2 (البند ٢٧): المدير يكتب سؤاله بلغته
   "ليش الربح نزل في أغسطس؟" ونبّاه يجاوب — بدل التنقّل بين الصفحات.
   زر عائم في كل صفحة → صندوق سؤال → إجابة من بيانات الشركة.
   يستخدم endpoint /company/ask الموجود.
   ═══════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const here = (location.pathname.split("/").pop() || "").toLowerCase();
  const SKIP = ["login.html", "index.html", "company-register.html", ""];
  if (SKIP.includes(here)) return;

  const TOKEN = localStorage.getItem("nabbah_token") || "";
  if (!TOKEN) return;

  // أسئلة مقترحة (من رؤية الاستشاري)
  const SUGGESTIONS = [
    "ليش الربح نزل؟",
    "وش أكبر مصروفاتي؟",
    "أي فرع الأفضل؟",
    "وش أكبر المخاطر؟",
    "ليش الإيراد زاد والربح نزل؟",
  ];

  const css = `
    #nbAskBtn{position:fixed;bottom:22px;left:22px;z-index:998;height:52px;padding:0 20px;border-radius:26px;
      background:linear-gradient(135deg,#10b981,#0b8457);color:#fff;border:none;font-family:'Tajawal',sans-serif;
      font-weight:800;font-size:14px;cursor:pointer;box-shadow:0 8px 24px rgba(16,185,129,.35);display:flex;align-items:center;gap:8px;transition:.2s}
    #nbAskBtn:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(16,185,129,.45)}
    #nbAskModal{position:fixed;inset:0;z-index:1002;background:rgba(0,0,0,.6);backdrop-filter:blur(4px);
      display:none;align-items:flex-end;justify-content:center;padding:0}
    #nbAskModal.show{display:flex}
    #nbAskBox{background:#0d1712;border:1px solid #1d2c24;border-radius:20px 20px 0 0;width:100%;max-width:680px;
      max-height:85vh;display:flex;flex-direction:column;font-family:'Tajawal',sans-serif;animation:nbUp .25s ease}
    @keyframes nbUp{from{transform:translateY(40px);opacity:.5}to{transform:translateY(0);opacity:1}}
    @media(min-width:700px){#nbAskModal{align-items:center}#nbAskBox{border-radius:20px}}
    #nbAskHead{padding:16px 20px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #1d2c24;flex-shrink:0}
    #nbAskHead .bell{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#10b981,#0b8457);display:grid;place-items:center;font-size:16px;transform:rotate(-6deg)}
    #nbAskHead .t{font-weight:900;font-size:17px;color:#fff}
    #nbAskHead .t small{display:block;font-size:11px;color:#8ba396;font-weight:500}
    #nbAskHead .x{margin-right:auto;cursor:pointer;font-size:22px;color:#5c6f66}
    #nbAskBody{flex:1;overflow-y:auto;padding:18px 20px;min-height:120px}
    #nbAskSugg{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}
    .nbSugg{background:#101a15;border:1px solid #1d2c24;color:#8ba396;border-radius:20px;padding:7px 14px;
      font-size:12.5px;cursor:pointer;font-family:inherit;transition:.15s}
    .nbSugg:hover{border-color:#10b981;color:#10b981}
    .nbAnswer{background:#101a15;border:1px solid #1d2c24;border-radius:14px;padding:16px;font-size:14px;
      color:#e8f1ec;line-height:1.85;white-space:pre-wrap}
    .nbQ{color:#10b981;font-weight:700;font-size:14px;margin-bottom:12px}
    #nbAskFoot{padding:14px 20px;border-top:1px solid #1d2c24;display:flex;gap:10px;flex-shrink:0}
    #nbAskInput{flex:1;background:#101a15;border:1px solid #1d2c24;color:#e8f1ec;border-radius:12px;
      padding:12px 15px;font-size:14px;font-family:inherit;outline:none}
    #nbAskInput:focus{border-color:#10b981}
    #nbAskSend{background:linear-gradient(135deg,#10b981,#0b8457);color:#fff;border:none;border-radius:12px;
      padding:0 20px;font-weight:800;font-size:14px;cursor:pointer;font-family:inherit}
    #nbAskSend:disabled{opacity:.5;cursor:default}
    .nbSpin{width:26px;height:26px;border:3px solid #1d2c24;border-top-color:#10b981;border-radius:50%;
      animation:nbsp 1s linear infinite;margin:20px auto}@keyframes nbsp{to{transform:rotate(360deg)}}
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // الزر العائم
  const btn = document.createElement("button");
  btn.id = "nbAskBtn";
  btn.innerHTML = "🤖 اسأل نبّاه";
  document.body.appendChild(btn);

  // النافذة
  const modal = document.createElement("div");
  modal.id = "nbAskModal";
  modal.innerHTML = `
    <div id="nbAskBox">
      <div id="nbAskHead">
        <span class="bell">🔔</span>
        <div class="t">اسأل نبّاه<small>اكتب سؤالك بلغتك — نبّاه يجاوب من بيانات شركتك</small></div>
        <span class="x" id="nbAskX">×</span>
      </div>
      <div id="nbAskBody">
        <div id="nbAskSugg">${SUGGESTIONS.map(q => `<button class="nbSugg">${q}</button>`).join("")}</div>
      </div>
      <div id="nbAskFoot">
        <input id="nbAskInput" placeholder="مثال: ليش الربح نزل في آخر فترة؟" />
        <button id="nbAskSend">إرسال</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  const body = document.getElementById("nbAskBody");
  const input = document.getElementById("nbAskInput");
  const sendBtn = document.getElementById("nbAskSend");

  function openModal() { modal.classList.add("show"); setTimeout(() => input.focus(), 100); }
  function closeModal() { modal.classList.remove("show"); }
  btn.addEventListener("click", openModal);
  document.getElementById("nbAskX").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });

  async function ask(q) {
    if (!q.trim()) return;
    body.innerHTML = `<div class="nbQ">🔍 ${q}</div><div class="nbSpin"></div>`;
    sendBtn.disabled = true;
    try {
      const r = await fetch("/company/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + TOKEN },
        body: JSON.stringify({ question: q }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "تعذّر الحصول على إجابة");
      const ans = d.answer || d.response || d.text || "لم أستطع تكوين إجابة.";
      body.innerHTML = `<div class="nbQ">🔍 ${q}</div><div class="nbAnswer">${ans}</div>`;
    } catch (e) {
      body.innerHTML = `<div class="nbQ">🔍 ${q}</div><div class="nbAnswer" style="color:#ef4444">${e.message}</div>`;
    }
    sendBtn.disabled = false;
  }

  sendBtn.addEventListener("click", () => ask(input.value));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(input.value); });
  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("nbSugg")) { input.value = e.target.textContent; ask(e.target.textContent); }
  });
})();
