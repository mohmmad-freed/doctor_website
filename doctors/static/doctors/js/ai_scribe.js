/*
 * ai_scribe.js — CSP-safe voice capture for the AI-scribe "Draft with AI" panel.
 *
 * The recorder UI lives in doctors/partials/ws_notes.html, which is HTMX-swapped
 * into #tab-content, so an inline <script> there loses its nonce on re-inject and
 * is blocked. This file is loaded once on the host page (patient_workspace.html)
 * and (re-)wires the record button on load and after each HTMX swap. The
 * `btn._wired` guard keeps it idempotent.
 *
 * Audio is uploaded via fetch() to a same-origin Django endpoint (connect-src
 * 'self'); getUserMedia is gated by Permissions-Policy, not CSP. No audio is
 * stored client-side (no blob: URL / playback), so no media-src is needed.
 */
(function () {
  "use strict";

  function wire() {
    var wrap = document.getElementById("ai-rec-wrap");
    var btn = document.getElementById("ai-record-btn");
    var ta = document.getElementById("ai-transcript");
    if (!btn || !ta || btn._wired) { return; }
    // No mic / MediaRecorder support → keep the paste-only experience.
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder)) { return; }
    btn._wired = true;
    if (wrap) { wrap.style.display = ""; }

    var statusEl = document.getElementById("ai-rec-status");
    var iconEl = document.getElementById("ai-rec-icon");
    var labelEl = document.getElementById("ai-rec-label");
    var form = btn.closest("form");
    var url = btn.dataset.url;
    var RTL = document.documentElement.dir === "rtl";
    function t(ar, en) { return RTL ? ar : en; }
    function fmt(s) { var m = Math.floor(s / 60), r = s % 60; return m + ":" + (r < 10 ? "0" : "") + r; }
    function setStatus(m) { if (statusEl) { statusEl.textContent = m || ""; } }

    var rec = null, chunks = [], stream = null, recording = false, timer = null, secs = 0;

    function stopStream() { if (stream) { stream.getTracks().forEach(function (tk) { tk.stop(); }); stream = null; } }

    async function start() {
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (e) { setStatus(t("تعذّر الوصول للميكروفون", "Microphone blocked")); return; }
      chunks = [];
      try { rec = new MediaRecorder(stream); }
      catch (e) { setStatus(t("التسجيل غير مدعوم", "Recording unsupported")); stopStream(); return; }
      rec.ondataavailable = function (e) { if (e.data && e.data.size) { chunks.push(e.data); } };
      rec.onstop = upload;
      rec.start();
      recording = true; secs = 0;
      iconEl.className = "fa-solid fa-stop text-red-500";
      labelEl.textContent = t("إيقاف", "Stop");
      btn.classList.add("ring-2", "ring-red-400");
      setStatus("● 0:00");
      timer = setInterval(function () { secs++; setStatus("● " + fmt(secs)); }, 1000);
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      recording = false;
      iconEl.className = "fa-solid fa-microphone text-indigo-500";
      labelEl.textContent = t("تسجيل", "Record");
      btn.classList.remove("ring-2", "ring-red-400");
      try { if (rec && rec.state !== "inactive") { rec.stop(); } } catch (e) {}
    }

    function upload() {
      stopStream();
      var blob = new Blob(chunks, { type: (chunks[0] && chunks[0].type) || "audio/webm" });
      if (!blob.size) { setStatus(t("لا يوجد صوت", "No audio captured")); return; }
      setStatus(t("جارٍ التفريغ…", "Transcribing…"));
      btn.disabled = true;
      var fd = new FormData();
      fd.append("audio", blob, "recording.webm");
      var clinicInput = form.querySelector("[name=clinic_id]");
      if (clinicInput) { fd.append("clinic_id", clinicInput.value); }
      var csrf = form.querySelector("[name=csrfmiddlewaretoken]");
      fetch(url, { method: "POST", body: fd, headers: csrf ? { "X-CSRFToken": csrf.value } : {} })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok || res.j.error) { setStatus(res.j.error || t("فشل التفريغ", "Transcription failed")); return; }
          var txt = (res.j.text || "").trim();
          if (txt) { ta.value += (ta.value ? "\n" : "") + txt; }
          setStatus(txt ? t("تم — راجِع النص ثم اضغط إنشاء", "Done — review, then Generate") : t("لم يُلتقط كلام", "No speech detected"));
        })
        .catch(function () { setStatus(t("خطأ في الشبكة", "Network error")); })
        .finally(function () { btn.disabled = false; });
    }

    btn.addEventListener("click", function () { recording ? stop() : start(); });
  }

  if (document.readyState !== "loading") { wire(); }
  else { document.addEventListener("DOMContentLoaded", wire); }
  document.addEventListener("htmx:afterSwap", wire);
  document.addEventListener("htmx:load", wire);
})();
