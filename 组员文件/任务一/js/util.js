/* =============================================================================
 * util.js — 无依赖工具（v3.16 从 app.js 拆出）
 *
 * 只放纯函数 / 存储封装，不碰 DOM、不碰引擎状态。classic script，顶层声明
 * 对后续脚本可见（index.html 必须先加载本文件）。
 * ========================================================================== */
'use strict';

const STORE_PREFIX = 'bsui:';

function fmt(v, d) {
  if (v == null || !Number.isFinite(v)) return '—';
  return Number(v).toFixed(d == null ? 2 : d);
}

function fmtT(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return (m > 0 ? m + 'm' : '') + s + 's';
}

function save(k, v) {
  try { localStorage.setItem(STORE_PREFIX + k, JSON.stringify(v)); } catch (e) { /* 隐私模式降级 */ }
}

function load(k, dflt) {
  try {
    const raw = localStorage.getItem(STORE_PREFIX + k);
    return raw == null ? dflt : JSON.parse(raw);
  } catch (e) { return dflt; }
}

function debounce(fn, ms) {
  let t = null;
  return function () { clearTimeout(t); t = setTimeout(fn, ms); };
}

function hexA(color, a) {
  const c = (color || '').trim();
  if (c.startsWith('#')) {
    const hex = c.length === 4
      ? '#' + c[1] + c[1] + c[2] + c[2] + c[3] + c[3]
      : c.slice(0, 7);
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
  const m = c.match(/(\d+)[,\s]+(\d+)[,\s]+(\d+)/);
  if (m) return `rgba(${m[1]},${m[2]},${m[3]},${a})`;
  return c;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** 输入自净：坏值回落默认（值域 clamp） */
function numIn(v, lo, hi, dflt) {
  const n = Number(v);
  return Number.isFinite(n) && n >= lo && n <= hi ? n : dflt;
}
