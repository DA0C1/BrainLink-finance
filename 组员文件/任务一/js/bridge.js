/* =============================================================================
 * bridge.js — 真机 / 回放数据桥客户端（v2.9.2）
 *
 * 作用：把 live_bridge.py 推送的帧（SSE）接到界面，补齐验收要求
 *       "T1 输出层：真机换数据源即用" —— 此前界面只有 mock，真机数据无路可进。
 *
 * 协议：EventSource(`${base}/stream`)，每行 data 为 JSON：
 *       {theta, alpha, beta, delta, gamma, attention, meditation,
 *        rr_ms, gyro:[x,y,z], ts, seq, source}
 * 只用到浏览器原生 EventSource（单向 server→browser），不需要 WebSocket 依赖，
 * 也不需要给 Python 侧装任何第三方包。
 * ========================================================================== */
(function (global) {
  'use strict';

  const MAX_QUEUE = 300;          // 队列上限：浏览器卡顿时丢旧帧，防内存堆积

  const LiveBridge = {
    es: null,
    url: '',
    token: '',
    status: 'idle',               // idle | connecting | open | error | closed
    queue: [],
    seq: 0,
    dropped: 0,
    source: '',
    lastGap: null,                // 相邻帧真实间隔（秒）——真机按真实节奏计时，而非假定 1s/帧
    lastTs: null,
    onstatus: null,               // (status, detail) => void
    onframe: null,                // (frameObj) => void

    _emit(status, detail) {
      this.status = status;
      if (typeof this.onstatus === 'function') {
        try { this.onstatus(status, detail || ''); } catch (e) { /* 界面回调异常不影响数据流 */ }
      }
    },

    connect(base, token) {
      this.close();
      this.flush();                 // 新连接不许继承上一数据源积压的旧帧
      this.token = token || '';
      const root = String(base || '').replace(/\/+$/, '');
      if (!root) { this._emit('error', '桥地址为空'); return; }
      this.url = root;
      this._emit('connecting', root);
      let es;
      try {
        const q = this.token ? ('?token=' + encodeURIComponent(this.token)) : '';
        es = new EventSource(root + '/stream' + q);
      } catch (e) {
        this._emit('error', '无法创建连接：' + e.message);
        return;
      }
      this.es = es;
      es.onopen = () => this._emit('open', root);
      es.onmessage = (ev) => {
        let obj;
        try { obj = JSON.parse(ev.data); } catch (e) { return; }
        if (!obj || !Number.isFinite(obj.theta)) return;
        // 用 Number.isFinite 而非真值判断：seq=0 是合法首帧序号，真值判断会漏掉
        if (Number.isFinite(obj.seq)) this.seq = obj.seq;
        if (obj.source) this.source = obj.source;
        // 真实帧间隔：用于界面按真机节奏推进时钟（BrainLink 约 1.2–1.5s/帧，假定 1s 会低估 30%）
        if (Number.isFinite(obj.ts)) {
          if (Number.isFinite(this.lastTs)) {
            const gap = obj.ts - this.lastTs;
            if (gap > 0) this.lastGap = Math.min(Math.max(gap, 0.1), 10);
          }
          this.lastTs = obj.ts;
        }
        if (this.queue.length >= MAX_QUEUE) { this.queue.shift(); this.dropped++; }
        this.queue.push(obj);
        if (typeof this.onframe === 'function') {
          try { this.onframe(obj); } catch (e) { /* 同上 */ }
        }
      };
      es.onerror = () => {
        // EventSource 会自动重连（readyState=0）；这里只更新状态，不关闭
        this._emit(es.readyState === 2 ? 'closed' : 'error', '连接中断，正在重试…');
      };
    },

    close() {
      if (this.es) {
        try { this.es.close(); } catch (e) { /* 已关闭 */ }
        this.es = null;
        this._emit('closed', '已断开');
      }
      this.flush();
    },

    /** 清空积压帧与间隔基准。
     *  典型场景：停在「自动轮播」时 SSE 仍在收帧，队列最高积到 300 帧（约 5 分钟）；
     *  切回「真机/回放」时若不清，界面会先回放几百帧过期数据，再追上现在。 */
    flush() {
      this.queue.length = 0;
      this.lastTs = null;
      this.lastGap = null;
    },

    /** 取一帧（队列空返回 null） */
    shift() { return this.queue.length ? this.queue.shift() : null; },

    get pending() { return this.queue.length; }
  };

  global.LiveBridge = LiveBridge;
})(window);
