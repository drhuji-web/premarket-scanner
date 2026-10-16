# -*- coding: utf-8 -*-
"""
PreMarket Scanner — واجهة ويب Flask
تُشغَّل محمياً أو على Render/Railway
"""

import os
import json
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, render_template_string, request

# استيراد منطق السكانر
from scanner import (
    build_candidates,
    analyze_with_claude,
    generate_html_report,
    fetch_btc_price,
    ANTHROPIC_API_KEY,
    ALPHA_VANTAGE_KEY,
    TWELVE_DATA_KEY,
    ET,
)

log = logging.getLogger(__name__)
app = Flask(__name__)

# ─── حالة السكانر (مشتركة بين الخيوط) ─────────────────
_state = {
    "status":     "idle",      # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "report_html": None,
    "error":      None,
    "stocks_count": 0,
}
_lock = threading.Lock()


def _run_scanner():
    """يشتغل في خيط منفصل حتى لا يجمّد الواجهة."""
    with _lock:
        _state["status"]     = "running"
        _state["started_at"] = datetime.now(ET).strftime("%H:%M:%S ET")
        _state["report_html"] = None
        _state["error"]      = None

    try:
        candidates = build_candidates()
        if not candidates:
            raise RuntimeError("لا يوجد مرشحون بعد الفلترة")

        analyzed = analyze_with_claude(candidates)
        btc      = fetch_btc_price()
        html     = generate_html_report(analyzed, btc)

        with _lock:
            _state["status"]      = "done"
            _state["report_html"] = html
            _state["stocks_count"] = len(analyzed)
            _state["finished_at"] = datetime.now(ET).strftime("%H:%M:%S ET")

    except Exception as e:
        log.exception("خطأ في السكانر")
        with _lock:
            _state["status"] = "error"
            _state["error"]  = str(e)


# ══════════════════════════════════════════════════════════
#  الصفحة الرئيسية
# ══════════════════════════════════════════════════════════
INDEX_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>PreMarket Scanner</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',Tahoma,Arial,sans-serif;background:#0f172a;color:#e2e8f0;
         min-height:100vh;display:flex;flex-direction:column;align-items:center;
         padding:24px 16px;direction:rtl}
    h1{font-size:1.6rem;color:#38bdf8;margin-bottom:6px}
    .sub{color:#64748b;font-size:0.9rem;margin-bottom:32px}
    .card{background:#1e293b;border:1px solid #334155;border-radius:14px;
          padding:28px;width:100%;max-width:480px;text-align:center}
    .btn{display:inline-block;background:#0ea5e9;color:#fff;font-size:1rem;
         font-weight:bold;padding:14px 36px;border-radius:10px;border:none;
         cursor:pointer;width:100%;margin-top:8px;transition:background .2s}
    .btn:hover{background:#0284c7}
    .btn:disabled{background:#334155;color:#64748b;cursor:not-allowed}
    .status-box{margin-top:20px;padding:14px;border-radius:8px;font-size:0.92rem;
                line-height:1.6;min-height:60px;display:none}
    .status-running{background:#172554;border:1px solid #1d4ed8;color:#93c5fd;display:block}
    .status-done   {background:#14532d;border:1px solid #16a34a;color:#86efac;display:block}
    .status-error  {background:#7f1d1d;border:1px solid #dc2626;color:#fca5a5;display:block}
    .spinner{display:inline-block;width:18px;height:18px;border:3px solid #1d4ed8;
             border-top-color:#93c5fd;border-radius:50%;animation:spin 0.8s linear infinite;
             vertical-align:middle;margin-left:8px}
    @keyframes spin{to{transform:rotate(360deg)}}
    .view-btn{display:none;background:#16a34a;color:#fff;font-size:1rem;font-weight:bold;
              padding:14px 36px;border-radius:10px;border:none;cursor:pointer;
              width:100%;margin-top:12px;text-decoration:none;text-align:center}
    .info{margin-top:24px;color:#475569;font-size:0.8rem;line-height:1.8}
    .keys{margin-top:10px;display:flex;flex-direction:column;gap:6px}
    .key-row{display:flex;justify-content:space-between;font-size:0.82rem}
    .key-ok{color:#4ade80} .key-no{color:#f87171}
  </style>
</head>
<body>
  <h1>📈 PreMarket Scanner</h1>
  <p class="sub">أفضل الأسهم عالية البيتا قبل افتتاح السوق</p>

  <div class="card">
    <p style="color:#94a3b8;margin-bottom:16px;font-size:0.9rem">
      وقت الخادم: <strong id="srv-time">—</strong>
    </p>

    <button class="btn" id="run-btn" onclick="startScan()">
      🚀 شغّل السكانر الآن
    </button>

    <div class="status-box" id="status-box">
      <span id="status-text"></span>
      <span class="spinner" id="spinner" style="display:none"></span>
    </div>

    <a class="view-btn" id="view-btn" href="/report" target="_blank">
      📊 عرض التقرير
    </a>

    <div class="keys">
      {{ key_rows | safe }}
    </div>
  </div>

  <div class="info">
    ⚠️ للأغراض التعليمية فقط — ليس توصية استثمارية<br>
    PreMarket AI Scanner v4
  </div>

<script>
  // تحديث وقت الخادم
  async function updateTime(){
    const r = await fetch('/api/time');
    const d = await r.json();
    document.getElementById('srv-time').textContent = d.time;
  }
  updateTime(); setInterval(updateTime, 30000);

  async function startScan(){
    const btn = document.getElementById('run-btn');
    btn.disabled = true;
    setStatus('running','⏳ جارٍ تشغيل السكانر... قد يستغرق 10-20 دقيقة',true);
    document.getElementById('view-btn').style.display='none';

    await fetch('/api/run', {method:'POST'});
    pollStatus();
  }

  function pollStatus(){
    const t = setInterval(async()=>{
      const r = await fetch('/api/status');
      const d = await r.json();

      if(d.status === 'running'){
        const elapsed = d.elapsed || '';
        setStatus('running','⏳ جارٍ التحليل... ' + elapsed, true);
      } else if(d.status === 'done'){
        clearInterval(t);
        setStatus('done','✅ اكتمل امتقرير! ' + d.stocks_count + ' أسهم — ' + d.finished_at, false);
        document.getElementById('view-btn').style.display='block';
        document.getElementById('run-btn').disabled = false;
        document.getElementById('run-btn').textContent = '🔄 إعادة التشغيل';
      } else if(d.status === 'error'){
        clearInterval(t);
        setStatus('error','❌ خطأ: ' + d.error, false);
        document.getElementById('run-btn').disabled = false;
      }
    }, 5000);
  }

  function setStatus(type, msg, spin){
    const box = document.getElementById('status-box');
    box.className = 'status-box status-' + type;
    document.getElementById('status-text').textContent = msg;
    document.getElementById('spinner').style.display = spin ? 'inline-block' : 'none';
  }

  // تحقق من حالة السكانر عند فتح الصفحة
  (async()=>{
    const r = await fetch('/api/status');
    const d = await r.json();
    if(d.status === 'running'){
      document.getElementById('run-btn').disabled = true;
      setStatus('running','⏳ السكانر يعمل... بدأ ' + d.started_at, true);
      pollStatus();
    } else if(d.status === 'done'){
      setStatus('done','✅ آخر تقرير: ' + d.stocks_count + ' أسهم — ' + d.finished_at, false);
      document.getElementById('view-btn').style.display='block';
    }
  })();
</script>
</body>
</html>"""


@app.route("/")
def index():
    def key_badge(label, val):
        ok = bool(val)
        cls = "key-ok" if ok else "key-no"
        icon = "✅" if ok else "❌"
        return f'<div class="key-row"><span>{label}</span><span class="{cls}">{icon}</span></div>'

    rows = (
        key_badge("Anthropic API", ANTHROPIC_API_KEY)
        + key_badge("Alpha Vantage", ALPHA_VANTAGE_KEY)
        + key_badge("Twelve Data", TWELVE_DATA_KEY)
    )
    return render_template_string(INDEX_HTML, key_rows=rows)


@app.route("/api/time")
def api_time():
    now = datetime.now(ET)
    return jsonify({"time": now.strftime("%I:%M %p ET — %A %b %d")})


@app.route("/api/run", methods=["POST"])
def api_run():
    with _lock:
        if _state["status"] == "running":
            return jsonify({"ok": False, "msg": "السكانر يعمل بالفعل"})

    t = threading.Thread(target=_run_scanner, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    with _lock:
        s = dict(_state)
        s.pop("report_html", None)   # لا نرسل HTML في الـ status
        # احسب الوقت المملوط إن كان يعمل
        if s["status"] == "running" and s["started_at"]:
            s["elapsed"] = ""  # يمكن إضافة عداد لاحقاً
    return jsonify(s)


@app.route("/report")
def report():
    with _lock:
        html = _state.get("report_html")
    if not html:
        return "<p style='color:white;font-family:sans-serif;padding:40px'>لا يوجد تقرير بعد — شغّل املاى  أولاً</p>", 404
    return html


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
