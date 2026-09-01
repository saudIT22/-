/* ═══════════════════════════════════════════════════════════
   نبّاه — شريط الفلاتر الموحّد (Nabbah Unified Filters)
   من رؤية V2 (البند ٢٦): فلاتر موحّدة أعلى كل صفحة.
   الفترة · الفرع · القسم — اختيار واحد يطبّق على كل الصفحة.
   آمن: طبقة اختيارية، الصفحة تستمع للحدث nabbahFilterChange.
   ═══════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // لا نعرض الفلاتر في صفحات معيّنة (إدخال، إعدادات، تسجيل)
  const here = (location.pathname.split("/").pop() || "").toLowerCase();
  const SKIP = ["company-settings.html", "company-register.html", "company-team.html",
                "company-input.html", "company-upload.html", "login.html", "index.html", ""];
  if (SKIP.includes(here)) return;

  const TOKEN = localStorage.getItem("nabbah_token") || "";
  if (!TOKEN) return;

  // الحالة المحفوظة (تبقى بين الصفحات)
  const saved = JSON.parse(localStorage.getItem("nabbah_filters") || "{}");
  const state = {
    period: saved.period || "current",
    branch: saved.branch || "all",
  };

  const PERIODS = [
    { v: "current", l: "الفترة الحالية" },
    { v: "last30", l: "آخر ٣٠ يوم" },
    { v: "quarter", l: "الربع الحالي" },
    { v: "year", l: "السنة الحالية" },
    { v: "all", l: "كل الفترات" },
  ];

  const css = `
    #nbFilters{position:sticky;top:0;z-index:30;background:rgba(7,11,9,.92);backdrop-filter:blur(10px);
      border-bottom:1px solid #1d2c24;padding:10px 16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-family:'Tajawal',sans-serif}
    #nbFilters .fl{display:flex;align-items:center;gap:6px}
    #nbFilters .lbl{font-size:11px;color:#5c6f66;font-weight:600}
    #nbFilters select{background:#101a15;border:1px solid #1d2c24;color:#c3d3cb;border-radius:9px;
      padding:6px 10px;font-size:12.5px;font-family:inherit;font-weight:600;cursor:pointer;outline:none}
    #nbFilters select:hover{border-color:#10b981}
    #nbFilters .reset{margin-right:auto;font-size:11.5px;color:#5c6f66;cursor:pointer;background:none;border:none;font-family:inherit}
    #nbFilters .reset:hover{color:#10b981}
    @media(max-width:600px){#nbFilters{gap:7px;padding:8px 12px}#nbFilters .lbl{display:none}}
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const bar = document.createElement("div");
  bar.id = "nbFilters";
  bar.innerHTML = `
    <div class="fl"><span class="lbl">الفترة</span>
      <select id="nbfPeriod">${PERIODS.map(p => `<option value="${p.v}"${p.v === state.period ? " selected" : ""}>${p.l}</option>`).join("")}</select>
    </div>
    <div class="fl"><span class="lbl">الفرع</span>
      <select id="nbfBranch"><option value="all">كل الفروع</option></select>
    </div>
    <button class="reset" id="nbfReset">↺ إعادة تعيين</button>
  `;

  // نحقنه أعلى المحتوى (بعد الهيدر إن وجد)
  function inject() {
    if (document.getElementById("nbFilters")) return;  // مرة واحدة
    const header = document.querySelector("header");
    if (header) {
      header.insertAdjacentElement("afterend", bar);
    } else {
      document.body.insertBefore(bar, document.body.firstChild);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
  // ضمان الحقن حتى لو تأخّر الـDOM
  setTimeout(inject, 300);

  // نجيب الفروع من الباك-إند لملء قائمة الفرع
  fetch("/company/dashboard", { headers: { "Authorization": "Bearer " + TOKEN } })
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      if (!d || !d.branches) return;
      const sel = document.getElementById("nbfBranch");
      if (!sel) return;
      d.branches.forEach(b => {
        if (b.name) {
          const o = document.createElement("option");
          o.value = b.id || b.name;
          o.textContent = b.name;
          if (String(o.value) === String(state.branch)) o.selected = true;
          sel.appendChild(o);
        }
      });
    })
    .catch(() => {});

  // نطلق حدث موحّد + نعيد تحميل الصفحة بالفلتر الجديد (ربط فعلي)
  function emit() {
    localStorage.setItem("nabbah_filters", JSON.stringify(state));
    // نطلق الحدث للصفحات التي تستمع له (تحديث فوري بلا إعادة تحميل)
    window.dispatchEvent(new CustomEvent("nabbahFilterChange", { detail: { ...state } }));
    // ونعيد تحميل الصفحة بمعاملات الفلتر (للصفحات التي تقرأ من URL)
    // نؤخّر قليلاً ليلتقط أي مستمع فوري أولاً
    const url = new URL(location.href);
    url.searchParams.set("period", state.period);
    url.searchParams.set("branch", state.branch);
    // نحدّث URL بلا قفزة، ونترك الصفحة تقرر إعادة الجلب
    history.replaceState(null, "", url.toString());
    // إشعار بصري أن الفلتر طُبّق
    showApplied();
  }

  // إشعار بصري صغير أن الفلتر طُبّق
  function showApplied() {
    let toast = document.getElementById("nbFilterToast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "nbFilterToast";
      toast.style.cssText = "position:fixed;top:60px;left:50%;transform:translateX(-50%);z-index:1005;" +
        "background:#10b981;color:#fff;padding:8px 18px;border-radius:20px;font-size:13px;font-weight:700;" +
        "font-family:'Tajawal',sans-serif;box-shadow:0 6px 20px rgba(16,185,129,.4);opacity:0;transition:.3s;pointer-events:none";
      document.body.appendChild(toast);
    }
    const pLabel = (PERIODS.find(p => p.v === state.period) || {}).l || "";
    const bLabel = document.querySelector("#nbfBranch option:checked");
    toast.textContent = "✓ طُبّق: " + pLabel + (bLabel && bLabel.value !== "all" ? " · " + bLabel.textContent : "");
    toast.style.opacity = "1";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { toast.style.opacity = "0"; }, 2000);
    // نعلم الصفحة تعيد الجلب إن كانت تدعم دالة nabbahReload
    if (typeof window.nabbahReload === "function") {
      window.nabbahReload(state);
    }
  }

  document.addEventListener("change", (e) => {
    if (e.target.id === "nbfPeriod") { state.period = e.target.value; emit(); }
    if (e.target.id === "nbfBranch") { state.branch = e.target.value; emit(); }
  });
  document.addEventListener("click", (e) => {
    if (e.target.id === "nbfReset") {
      state.period = "current"; state.branch = "all";
      const p = document.getElementById("nbfPeriod"); if (p) p.value = "current";
      const b = document.getElementById("nbfBranch"); if (b) b.value = "all";
      emit();
    }
  });

  // نتيح للصفحات قراءة الحالة الحالية
  window.NabbahFilters = { get: () => ({ ...state }) };
})();
