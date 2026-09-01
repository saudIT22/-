/* ═══════════════════════════════════════════════════════════
   نبّاه — الشريط الجانبي الموحّد (Nabbah Unified Sidebar)
   من رؤية V2: تنقّل منظّم بـ١٢ مجموعة حول رحلة القرار التنفيذي.
   يُحقن في أي صفحة عبر <script src="nabbah-sidebar.js"></script>
   آمن: لا يكسر القوائم الحالية — طبقة تنقّل إضافية.
   ═══════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // بنية التنقّل — ١٢ مجموعة (كل عنصر: label, href, وأيقونة)
  // الصفحات غير المبنية بعد تُعلّم "قريباً" (soon:true) — شفافية، لا روابط ميتة.
  const NAV = [
    { icon: "⌂", label: "الرئيسية", items: [
      { label: "اللوحة", href: "company-dashboard.html" },
    ]},
    { icon: "🎯", label: "مركز القيادة", items: [
      { label: "مركز القيادة التنفيذي", href: "company-command-center.html" },
      { label: "صحة الشركة", href: "company-health.html" },
      { label: "الأهداف والنتائج", href: "company-goals.html" },
      { label: "عرض مجلس الإدارة", href: "company-board.html" },
    ]},
    { icon: "💰", label: "المالية", items: [
      { label: "الوحدة المالية", href: "company-finance.html" },
      { label: "التدفق النقدي", href: "company-cashflow.html" },
      { label: "هدر الإيرادات", href: "company-leakage.html" },
      { label: "الضريبة والزكاة", href: "company-tax.html" },
    ]},
    { icon: "📈", label: "المبيعات والعملاء", items: [
      { label: "المبيعات التفصيلية", href: "company-sales.html" },
      { label: "وحدة العملاء", href: "company-customers.html" },
      { label: "المنافسون", href: "company-competitors.html" },
    ]},
    { icon: "👥", label: "الأشخاص", items: [
      { label: "الموارد البشرية", href: "company-hr-analytics.html" },
      { label: "إدخال بيانات HR", href: "company-hr.html" },
    ]},
    { icon: "⚙️", label: "العمليات", items: [
      { label: "التشغيل", href: "company-ops.html" },
      { label: "مقارنة الفروع", href: "company-branches.html" },
    ]},
    { icon: "📦", label: "سلسلة الإمداد", items: [
      { label: "المخزون", href: "company-inventory.html" },
      { label: "المشتريات", href: "company-procurement.html" },
    ]},
    { icon: "🎯", label: "الاستراتيجية", items: [
      { label: "الأهداف والنتائج", href: "company-goals.html" },
    ]},
    { icon: "⚠️", label: "المخاطر والامتثال", items: [
      { label: "محرّك المخاطر", href: "company-risks.html" },
      { label: "الضريبة والزكاة", href: "company-tax.html" },
      { label: "سجل التدقيق", href: "company-audit.html" },
      { label: "جودة البيانات", href: "company-data-quality.html" },
    ]},
    { icon: "🌍", label: "السوق", items: [
      { label: "المنافسون", href: "company-competitors.html" },
      { label: "الأحداث المؤثرة", href: "company-events.html" },
      { label: "مقارنة بالقطاع", href: "company-benchmarks.html" },
    ]},
    { icon: "🤖", label: "ذكاء نبّاه", items: [
      { label: "التنبؤ بالأداء", href: "company-predictions.html" },
      { label: "الأسباب الجذرية", href: "company-root-cause.html" },
      { label: "ذاكرة الشركة", href: "company-memory.html" },
    ]},
    { icon: "📊", label: "التقارير", items: [
      { label: "التقرير التنفيذي الذكي", href: "company-executive-report.html" },
      { label: "التقرير التنفيذي", href: "company-report.html" },
      { label: "التقرير الشهري", href: "company-monthly-report.html" },
      { label: "متابعة القرارات", href: "company-decisions.html" },
    ]},
  ];

  const BOTTOM = [
    { icon: "🗂️", label: "رفع البيانات", href: "company-upload.html" },
    { icon: "👥", label: "صلاحيات الفريق", href: "company-team.html" },
    { icon: "⚙️", label: "الإعدادات", href: "company-settings.html" },
  ];

  const here = (location.pathname.split("/").pop() || "").toLowerCase();

  // نبني الـHTML
  const css = `
    #nbSidebar{position:fixed;top:0;right:0;height:100vh;width:250px;background:#0a120e;border-left:1px solid #1d2c24;
      z-index:1000;display:flex;flex-direction:column;transform:translateX(100%);transition:transform .25s;font-family:'Tajawal',sans-serif}
    #nbSidebar.open{transform:translateX(0)}
    #nbSbHead{padding:16px 18px;display:flex;align-items:center;gap:10px;border-bottom:1px solid #1d2c24;flex-shrink:0}
    #nbSbHead .bell{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#10b981,#0b8457);display:grid;place-items:center;font-size:16px;transform:rotate(-6deg)}
    #nbSbHead .nm{font-weight:900;font-size:18px;color:#fff}
    #nbSbBody{flex:1;overflow-y:auto;padding:8px 0}
    #nbSbBody::-webkit-scrollbar{width:5px}#nbSbBody::-webkit-scrollbar-thumb{background:#1d2c24;border-radius:3px}
    .nbGrp{border-bottom:1px solid rgba(29,44,36,.4)}
    .nbGrpH{display:flex;align-items:center;gap:10px;padding:11px 18px;cursor:pointer;font-size:13.5px;font-weight:700;color:#c3d3cb;transition:.15s}
    .nbGrpH:hover{background:rgba(255,255,255,.03)}
    .nbGrpH .ic{font-size:15px;width:20px;text-align:center}
    .nbGrpH .ar{margin-right:auto;font-size:10px;color:#5c6f66;transition:transform .2s}
    .nbGrp.open .nbGrpH .ar{transform:rotate(90deg)}
    .nbGrpItems{max-height:0;overflow:hidden;transition:max-height .25s}
    .nbGrp.open .nbGrpItems{max-height:400px}
    .nbItem{display:block;padding:9px 18px 9px 42px;font-size:12.5px;color:#8ba396;text-decoration:none;transition:.12s;position:relative}
    .nbItem:hover{color:#10b981;background:rgba(16,185,129,.05)}
    .nbItem.active{color:#10b981;font-weight:700;background:rgba(16,185,129,.09)}
    .nbItem.active::before{content:"";position:absolute;right:0;top:0;bottom:0;width:3px;background:#10b981}
    .nbItem.soon{opacity:.4;pointer-events:none}
    .nbItem.soon::after{content:" قريباً";font-size:9px;color:#5c6f66}
    #nbSbFoot{border-top:1px solid #1d2c24;padding:8px 0;flex-shrink:0}
    #nbToggle{position:fixed;top:14px;right:14px;z-index:1001;width:42px;height:42px;border-radius:12px;
      background:#101a15;border:1px solid #1d2c24;color:#10b981;font-size:19px;cursor:pointer;display:grid;place-items:center}
    #nbOverlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:999;opacity:0;pointer-events:none;transition:.25s}
    #nbOverlay.show{opacity:1;pointer-events:auto}
    @media(min-width:1100px){
      /* الشريط مخفي دائماً — يظهر بزر ☰ فقط (المحتوى يأخذ كل الشاشة) */
      #nbToggle{display:grid}
    }
  `;

  function buildGroup(g) {
    const items = g.items.map(it => {
      const active = it.href.toLowerCase() === here ? " active" : "";
      return `<a class="nbItem${active}" href="${it.href}">${it.label}</a>`;
    }).join("");
    // نفتح المجموعة التي تحتوي الصفحة الحالية
    const hasActive = g.items.some(it => it.href.toLowerCase() === here);
    return `<div class="nbGrp${hasActive ? " open" : ""}">
      <div class="nbGrpH"><span class="ic">${g.icon}</span><span>${g.label}</span><span class="ar">▶</span></div>
      <div class="nbGrpItems">${items}</div>
    </div>`;
  }

  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const toggle = document.createElement("button");
  toggle.id = "nbToggle";
  toggle.innerHTML = "☰";
  document.body.appendChild(toggle);

  const overlay = document.createElement("div");
  overlay.id = "nbOverlay";
  document.body.appendChild(overlay);

  const sb = document.createElement("nav");
  sb.id = "nbSidebar";
  sb.innerHTML = `
    <div id="nbSbHead"><span class="bell">🔔</span><span class="nm">نبّاه</span><span id="nbClose" style="margin-right:auto;cursor:pointer;font-size:20px;color:#5c6f66">×</span></div>
    <div id="nbSbBody">${NAV.map(buildGroup).join("")}</div>
    <div id="nbSbFoot">${BOTTOM.map(b => {
      const active = b.href.toLowerCase() === here ? " active" : "";
      return `<a class="nbItem${active}" style="padding-right:18px" href="${b.href}"><span style="margin-left:8px">${b.icon}</span>${b.label}</a>`;
    }).join("")}</div>`;
  document.body.appendChild(sb);

  // فتح/طي المجموعات
  sb.querySelectorAll(".nbGrpH").forEach(h => {
    h.addEventListener("click", () => h.parentElement.classList.toggle("open"));
  });

  // فتح/إغلاق الشريط (موبايل)
  function openSb() { sb.classList.add("open"); overlay.classList.add("show"); }
  function closeSb() { sb.classList.remove("open"); overlay.classList.remove("show"); }
  toggle.addEventListener("click", openSb);
  overlay.addEventListener("click", closeSb);
  const closeBtn = document.getElementById("nbClose");
  if (closeBtn) closeBtn.addEventListener("click", closeSb);
  // إغلاق تلقائي عند اختيار خدمة (تجربة أنظف)
  sb.querySelectorAll(".nbItem").forEach(a => {
    a.addEventListener("click", () => setTimeout(closeSb, 100));
  });
})();
