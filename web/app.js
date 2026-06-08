"use strict";

const $ = (id) => document.getElementById(id);
let tests = [];
let activeId = null;

async function loadTests() {
  const res = await fetch("/api/tests");
  tests = await res.json();
  renderList();
}

function renderList() {
  const ul = $("testList");
  ul.innerHTML = "";
  if (!tests.length) {
    ul.innerHTML = '<li class="li-meta" style="cursor:default">还没有测试 No tests yet.</li>';
    return;
  }
  for (const t of tests) {
    const li = document.createElement("li");
    if (t.id === activeId) li.className = "active";
    li.innerHTML =
      `<div class="li-label">${escapeHtml(t.label)}</div>` +
      `<div class="li-meta">${t.created.replace("T", " ")} · ${t.seconds}s · ${t.atten}dB · ` +
      `<span style="color:var(--proc)">${t.reduction_db > 0 ? "+" : ""}${t.reduction_db} dB</span></div>`;
    li.onclick = () => selectTest(t.id);
    ul.appendChild(li);
  }
}

function selectTest(id) {
  activeId = id;
  renderList();
  const t = tests.find((x) => x.id === id);
  if (!t) return;
  const base = `/recordings/${t.id}`;
  $("detail").innerHTML = `
    <h2>${escapeHtml(t.label)}</h2>
    <div class="metrics">
      <span><b>${t.seconds}s</b>时长</span>
      <span><b>${t.atten} dB</b>抑制强度</span>
      <span><b>${t.reduction_db > 0 ? "+" : ""}${t.reduction_db} dB</b>电平变化 level Δ</span>
      <span><b>${t.change_rms}</b>差异 change RMS</span>
      <span><b>${escapeHtml(t.device)}</b>设备</span>
    </div>
    <div class="track">
      <h3><span class="dot raw"></span>原始 Raw (mic)</h3>
      <canvas id="wf-raw"></canvas>
      <audio id="au-raw" controls preload="auto" src="${base}/raw.wav"></audio>
    </div>
    <div class="track">
      <h3><span class="dot proc"></span>处理后 Processed (Hush)</h3>
      <canvas id="wf-proc"></canvas>
      <audio id="au-proc" controls preload="auto" src="${base}/processed.wav"></audio>
    </div>
    <div class="detail-actions">
      <button id="delBtn">删除 Delete</button>
    </div>
  `;
  $("delBtn").onclick = () => deleteTest(t.id);
  // Pause one player when the other starts, for clean A/B.
  const ar = $("au-raw"), ap = $("au-proc");
  ar.onplay = () => ap.pause();
  ap.onplay = () => ar.pause();
  drawWaveform(`${base}/raw.wav`, "wf-raw", getCss("--raw"));
  drawWaveform(`${base}/processed.wav`, "wf-proc", getCss("--proc"));
}

async function deleteTest(id) {
  if (!confirm("删除这个测试？Delete this test?")) return;
  await fetch(`/api/delete?id=${encodeURIComponent(id)}`, { method: "POST" });
  if (activeId === id) { activeId = null; $("detail").innerHTML = '<p class="empty">已删除 Deleted.</p>'; }
  await loadTests();
}

async function record() {
  const btn = $("recordBtn");
  const seconds = parseFloat($("seconds").value) || 6;
  const atten = parseFloat($("atten").value);
  const label = $("label").value.trim();
  btn.disabled = true;
  let remaining = Math.ceil(seconds);
  $("recStatus").textContent = `🔴 录制中 Recording… 请说话 talk now (${remaining}s)`;
  const tick = setInterval(() => {
    remaining -= 1;
    if (remaining > 0) $("recStatus").textContent = `🔴 录制中 Recording… (${remaining}s)`;
    else $("recStatus").textContent = "⏳ 用 Hush 处理中 Processing…";
  }, 1000);
  try {
    const res = await fetch("/api/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seconds, atten, label }),
    });
    const data = await res.json();
    clearInterval(tick);
    if (data.error) {
      $("recStatus").textContent = `❌ ${data.error}`;
    } else {
      $("recStatus").textContent = `✅ 完成 Done: ${data.label} (${data.reduction_db > 0 ? "+" : ""}${data.reduction_db} dB)`;
      await loadTests();
      selectTest(data.id);
    }
  } catch (e) {
    clearInterval(tick);
    $("recStatus").textContent = `❌ ${e}`;
  } finally {
    btn.disabled = false;
  }
}

// --- waveform rendering via WebAudio decode ---
const actx = new (window.AudioContext || window.webkitAudioContext)();
async function drawWaveform(url, canvasId, color) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const W = (canvas.width = canvas.clientWidth * dpr);
  const H = (canvas.height = canvas.clientHeight * dpr);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, W, H);
  try {
    const buf = await (await fetch(url)).arrayBuffer();
    const audio = await actx.decodeAudioData(buf);
    const data = audio.getChannelData(0);
    const step = Math.max(1, Math.floor(data.length / W));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1 * dpr;
    ctx.beginPath();
    const mid = H / 2;
    for (let x = 0; x < W; x++) {
      let min = 1, max = -1;
      for (let i = 0; i < step; i++) {
        const v = data[x * step + i] || 0;
        if (v < min) min = v;
        if (v > max) max = v;
      }
      ctx.moveTo(x, mid + min * mid);
      ctx.lineTo(x, mid + max * mid);
    }
    ctx.stroke();
  } catch (e) {
    ctx.fillStyle = "#666";
    ctx.fillText("waveform unavailable", 8, H / 2);
  }
}

function getCss(varName) {
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("recordBtn").onclick = record;
loadTests();
