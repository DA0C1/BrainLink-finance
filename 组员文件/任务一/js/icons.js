/* =============================================================================
 * icons.js — 单色线性图标集（v3.12「仪器面板」视觉系统）
 *
 * 为什么自制而不引第三方图标库：
 *   1) 便携包要求离线可运行，不能依赖 CDN / npm；
 *   2) 原来用的是 emoji（⚡🌫️😴🪫💭🌊🎯🍃⏳），Windows 上由 Segoe UI Emoji 渲染，
 *      字重、基线、彩色填充都不受控 —— 在深色主题里尤其突兀，且无法跟随 currentColor；
 *   3) 8 个状态各有一个语义符号，是这块界面上唯一有"记忆点"的图形语言，
 *      用通用图标库反而丢掉这层含义。
 *
 * 规格（与 CSS 的 .ic 配合）：
 *   viewBox 24×24 · stroke 1.7 · 圆头圆角 · 颜色 = currentColor · 尺寸由 CSS 决定
 *   纯描边路径，无填充（play / pause / ellipsis / theme 的实心部分显式 fill）
 * ========================================================================== */
(function (global) {
  'use strict';

  /* ---- 图形库：键名即语义，不在业务代码里写 SVG ---- */
  var DEF = {
    /* 品牌标记：一段脑电搏动 */
    pulse: '<path d="M2.6 12.2h3.9l2.2-5.8 4.2 11.4 2.4-5.6h6.1"/>',

    /* 播放控制 */
    play: '<path d="M8.6 5.4v13.2L19.4 12z" fill="currentColor" stroke="none"/>',
    pause: '<path d="M8.1 5.4h3.1v13.2H8.1zM12.8 5.4h3.1v13.2h-3.1z" fill="currentColor" stroke="none"/>',
    step: '<path d="M7 12h9"/><path d="M13.4 8.2 17.4 12l-4 3.8"/>',
    reset: '<path d="M4.2 12a7.8 7.8 0 1 0 2.5-5.7"/><path d="M3.9 3.9v5h5"/>',

    /* 状态 / 提示 */
    alert: '<path d="M12 3.9 2.9 19.7h18.2z"/><path d="M12 10v4.1"/><path d="M12 17.1h.01"/>',
    check: '<path d="M4.6 12.6 9.3 17.4 19.4 6.6"/>',
    close: '<path d="M6.4 6.4 17.6 17.6"/><path d="M17.6 6.4 6.4 17.6"/>',
    info: '<circle cx="12" cy="12" r="8.6"/><path d="M12 11v5.2"/><path d="M12 7.9h.01"/>',
    theme: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor" stroke="none"/>',
    route: '<path d="M4 7.2h4.3l7.1 9.6H20"/><path d="M4 16.8h4.2"/><path d="M17.6 4.8 20 7.2l-2.4 2.4"/><path d="M17.6 14.4 20 16.8l-2.4 2.4"/>',
    bell: '<path d="M18 8.8a6 6 0 1 0-12 0c0 4.2-1.4 5.6-1.4 5.6h14.8S18 13 18 8.8z"/><path d="M10.3 18a1.9 1.9 0 0 0 3.4 0"/>',
    megaphone: '<path d="M4 9.4v5.2h3.2l7.8 4V5.4l-7.8 4z"/><path d="M18.6 9.2a4 4 0 0 1 0 5.6"/>',
    document: '<path d="M6.4 3.8h7.2l4 4v12.4H6.4z"/><path d="M13.4 3.9v4.1h4.1"/><path d="M9.4 12.4h5.2"/><path d="M9.4 15.6h5.2"/>',
    download: '<path d="M12 4.4v10.4"/><path d="M8.2 11.2 12 15l3.8-3.8"/><path d="M4.8 17.6v2h14.4v-2"/>',
    upload: '<path d="M12 15.2V4.8"/><path d="M8.2 8.6 12 4.8l3.8 3.8"/><path d="M4.8 17.6v2h14.4v-2"/>',
    sliders: '<path d="M5 7.6h9"/><path d="M18 7.6h1.2"/><path d="M5 16.4h3.4"/><path d="M12.2 16.4h7"/><circle cx="16.2" cy="7.6" r="1.9"/><circle cx="10.4" cy="16.4" r="1.9"/>',
    pulseSmall: '<path d="M3 12h3.4l1.9-4.6 3.4 9.2 2-4.6H21"/>',

    /* 8 状态语义符号 */
    bolt: '<path d="M13.3 2.9 5.1 13.4h5.9l-1.1 7.7 8.4-10.9h-5.7z"/>',
    cloud: '<path d="M7.3 18.1a3.7 3.7 0 0 1 .2-7.4 5.1 5.1 0 0 1 9.8-1.2 3.8 3.8 0 0 1 .1 8.6z"/>',
    moon: '<path d="M20.3 14.7A8.6 8.6 0 0 1 9.5 3.9a8.6 8.6 0 1 0 10.8 10.8z"/>',
    battery: '<path d="M3.2 8.3h12.9a1.7 1.7 0 0 1 1.7 1.7v4a1.7 1.7 0 0 1-1.7 1.7H3.2z"/><path d="M20.3 10.7v2.6"/><path d="M5.9 10.9v2.2"/>',
    bubble: '<path d="M20 5.9H4.1v9.3h4.7v3.5l4.5-3.5H20z"/>',
    wave: '<path d="M2.5 12.1c2.2 0 2.5-4.3 4.9-4.3s2.6 8.6 5 8.6 2.6-8.6 5-8.6 2.2 4.3 4.4 4.3"/>',
    target: '<circle cx="12" cy="12" r="8.3"/><circle cx="12" cy="12" r="3.5"/><path d="M12 1.9v2.9M12 19.2v2.9M1.9 12h2.9M19.2 12h2.9"/>',
    leaf: '<path d="M20.6 3.7C10.6 4.1 4.6 8.6 4.2 15.4c-.1 2.4 1.4 3.9 3.8 3.8 6.8-.4 11.3-6.5 12.6-15.5z"/><path d="M4.5 19.5 12 12"/>',
    ellipsis: '<circle cx="6" cy="12" r="1.35" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.35" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.35" fill="currentColor" stroke="none"/>'
  };

  /* ---- 状态 → 图形。8 态各一个专属符号，中性态用省略号（"未确认"） ---- */
  var STATE_ICON = {
    stress: 'bolt',
    anxiety: 'cloud',
    drowsy: 'moon',
    fatigue: 'battery',
    distracted: 'bubble',
    flow: 'wave',
    focus: 'target',
    relaxed: 'leaf',
    neutral: 'ellipsis'
  };

  /**
   * 生成一个图标。
   * @param {string} name  DEF 里的键名
   * @param {string} [cls] 附加 class
   * @returns {string} SVG 字符串；未知键返回空串（宁可缺图，也不要画出半个符号）
   */
  function svg(name, cls) {
    var body = DEF[name];
    if (!body) return '';
    return '<svg class="ic' + (cls ? ' ' + cls : '') + '" viewBox="0 0 24 24"'
      + ' fill="none" stroke="currentColor" stroke-width="1.7"'
      + ' stroke-linecap="round" stroke-linejoin="round"'
      + ' aria-hidden="true" focusable="false">' + body + '</svg>';
  }

  /** 状态键 → 图标名（未知状态回落到中性） */
  function stateIcon(state) {
    return STATE_ICON[state] || 'ellipsis';
  }

  global.Icons = { svg: svg, stateIcon: stateIcon, names: Object.keys(DEF) };
})(window);
