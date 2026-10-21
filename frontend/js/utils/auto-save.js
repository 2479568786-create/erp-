/**
 * 自动保存通用模块 (AutoSave)
 * ----------------------------------------------------------------------------
 * 业务目标：业务员填的所有表单 + AI 生成的所有结果 → 后端持久化
 *   - 刷新页面 / 误关浏览器 / 换电脑 / 断网 → 一键恢复
 *   - per-user 隔离（草稿不串），admin 可看全员（per-user + 共享）
 *   - 落库是后端 SQLite，跨设备可靠
 *
 * 后端接口：
 *   GET    /api/auto-save/drafts?scope=&key=   取单条
 *   GET    /api/auto-save/drafts?scope=        列自己该 scope 全部
 *   PUT    /api/auto-save/drafts               UPSERT
 *   DELETE /api/auto-save/drafts?scope=&key=   删
 *   GET    /api/auto-save/drafts/all?scope=    admin 看全员（不在前端用，监控用）
 *   POST   /api/ai-results                     落一条 AI 结果
 *
 * 使用模式（推荐）：
 *   1) 进入页面 → AutoSave.restore({scope, key}) 拉回草稿 + AutoSave.bind 自动绑定
 *   2) 用户输入 → 防抖 800ms 自动 PUT
 *   3) 保存成功 → AutoSave.clear({scope, key}) 清理草稿
 *   4) AI 生成成功 → AutoSave.saveAiResult({type, productId, result, meta})
 *
 * 注意：
 *   - 草稿 / AI 结果的 schema 由各业务页决定（payload 自由 JSON）
 *   - 网络错误不弹 Toast（避免骚扰），仅 console.warn
 *   - 不依赖任何第三方库，复用前端现有的 API/Ajax 客户端
 */
(function (global) {
  'use strict';

  /** 内部：把 selector 解析成 Element 列表 */
  function _resolve(selectorOrEl) {
    if (!selectorOrEl) return [];
    if (typeof selectorOrEl === 'string') {
      try { return Array.from(document.querySelectorAll(selectorOrEl)); } catch (e) { return []; }
    }
    if (selectorOrEl instanceof Element) return [selectorOrEl];
    if (Array.isArray(selectorOrEl)) return selectorOrEl.filter(Boolean);
    if (selectorOrEl.length && selectorOrEl[0] instanceof Element) {
      return Array.from(selectorOrEl);
    }
    return [];
  }

  /** 内部：从元素取值（input/textarea 取 value；其他取 data-value 或 textContent） */
  function _readValue(el) {
    if (!el) return '';
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return el.value;
    return el.getAttribute('data-value') || el.textContent || '';
  }

  /** 内部：把值写回元素（input/textarea 用 value；其他用 data-value） */
  function _writeValue(el, val) {
    if (!el) return;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      // 注意：避免触发 input 事件，否则会再次触发自动保存
      el.value = val == null ? '' : val;
    } else {
      el.setAttribute('data-value', val == null ? '' : val);
    }
  }

  /** 内部：拿 Api 客户端（前端 api.js 暴露的全局） */
  function _api() {
    if (global.API) return global.API;
    if (global.Api) return global.Api;
    throw new Error('[AutoSave] 找不到前端 API 客户端 (API / Api)');
  }

  /** 内部：发请求的统一封装（不抛异常，错误 console.warn 后返回 null） */
  async function _safe(reqPromise) {
    try {
      const r = await reqPromise;
      if (r && r.code === 200) return r;
      console.warn('[AutoSave] 接口返回非 200:', r);
      return r || null;
    } catch (e) {
      console.warn('[AutoSave] 网络错误:', e && e.message);
      return null;
    }
  }

  const AutoSave = {
    /** 默认防抖 800ms（涂总原话"全自动跟我保存"——不能太慢让人感觉不到） */
    DEFAULT_DEBOUNCE: 800,

    /** 全局：安装 beforeunload 兜底（关闭页面前最后写一次挂起中的草稿） */
    _pendingFlush: new Set(),  // 集合：{scope, draft_key, payload, meta, elements}
    _guardInstalled: false,
    installBeforeUnloadGuard() {
      if (this._guardInstalled) return;
      this._guardInstalled = true;
      const self = this;
      window.addEventListener('beforeunload', function () {
        // 同步模式：发一个最后写入，不等响应
        for (const item of self._pendingFlush) {
          try {
            const body = JSON.stringify({
              scope: item.scope,
              draft_key: item.draft_key,
              payload: item.payload,
              meta: item.meta,
            });
            // 用 sendBeacon 兜底（最稳），失败也无所谓
            if (navigator.sendBeacon && /api\/auto-save\/drafts/.test(self._beaconUrl)) {
              navigator.sendBeacon(self._beaconUrl, new Blob([body], { type: 'application/json' }));
            }
          } catch (e) { /* noop */ }
        }
      });
    },
    _beaconUrl: '/api/auto-save/drafts',

    /**
     * 绑定元素的 input/change 事件 → 防抖 → PUT 草稿
     * @param {string|Element|Element[]} selectorOrEls
     * @param {Object} opts
     *   - scope: 业务域（必填，如 'product_card'）
     *   - key: 业务键（必填，如 'pc_points_<productId>'），函数形式 fn(payloadObj) -> string
     *   - debounceMs: 防抖毫秒，默认 800
     *   - meta: 元信息对象（任意），可在 onChange 时动态调整
     *   - onSaved(payload): 写入成功的回调
     *   - onError(err): 写入失败的回调
     *   - computePayload(elements): 从元素列表拼装 payload（默认用 { values: [...] }）
     * @returns {Function} 取消绑定的 cleanup
     */
    bind(selectorOrEls, opts) {
      if (!opts || !opts.scope || !opts.key) {
        console.warn('[AutoSave.bind] 缺少 scope/key');
        return () => {};
      }
      const elements = _resolve(selectorOrEls);
      if (!elements.length) {
        console.warn('[AutoSave.bind] 未找到元素:', selectorOrEls);
        return () => {};
      }
      const self = this;
      const debounceMs = opts.debounceMs || this.DEFAULT_DEBOUNCE;
      const meta = opts.meta || {};
      const keyFn = typeof opts.key === 'function' ? opts.key : () => opts.key;
      const computePayload = opts.computePayload || function (els) {
        const values = els.map(_readValue);
        return { values: values.length === 1 ? values[0] : values };
      };

      let timer = null;
      let lastSaved = '';
      const doSave = function () {
        const payload = computePayload(elements);
        const payloadStr = JSON.stringify(payload);
        if (payloadStr === lastSaved) return;  // 没变就不打后端
        const key = keyFn(payload);
        if (!key) return;
        const req = _api().put('/api/auto-save/drafts', {
          scope: opts.scope,
          draft_key: key,
          payload: payload,
          meta: typeof meta === 'function' ? meta(payload) : meta,
        });
        _safe(req).then(function (r) {
          if (r && r.code === 200) {
            lastSaved = payloadStr;
            // 登记 pending（beforeunload 兜底用）
            self._pendingFlush.add({ scope: opts.scope, draft_key: key, payload: payload, meta: meta });
            if (typeof opts.onSaved === 'function') opts.onSaved(payload, r);
          } else {
            if (typeof opts.onError === 'function') opts.onError(r);
          }
        });
      };
      const onInput = function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(doSave, debounceMs);
      };
      elements.forEach(function (el) {
        el.addEventListener('input', onInput);
        el.addEventListener('change', onInput);
      });

      // 首次绑定立即落一次（把当前 DOM 值也保存上）
      // 跳过：用户未登录时不要触发（避免没 token 的 401 刷屏）
      // 简单判断：localStorage 里有 token 就算登录
      let isLoggedIn = false;
      try { isLoggedIn = !!localStorage.getItem('geekozon_token') || !!localStorage.getItem('erp_token'); } catch (e) {}
      if (isLoggedIn) {
        setTimeout(doSave, 200);
      }

      // 返回 cleanup
      return function () {
        if (timer) { clearTimeout(timer); timer = null; }
        elements.forEach(function (el) {
          el.removeEventListener('input', onInput);
          el.removeEventListener('change', onInput);
        });
      };
    },

    /**
     * 拉回草稿并填入元素
     * @param {Object} opts
     *   - scope, key
     *   - fillElements: selectorOrEls，回调 (payload) → 决定如何写回
     *   - onFound(payload): 找到草稿的回调
     * @returns {Promise<null|Object>} 草稿对象
     */
    async restore(opts) {
      if (!opts || !opts.scope || !opts.key) return null;
      const r = await _safe(_api().get('/api/auto-save/drafts', { scope: opts.scope, key: opts.key }));
      if (r && r.code === 200 && r.data) {
        const payload = r.data.payload;
        if (typeof opts.fillElements === 'function') {
          opts.fillElements(payload, r.data);
        } else if (opts.selectorOrEls) {
          // 默认行为：按 { values: [...] } 形态逐个写回
          const els = _resolve(opts.selectorOrEls);
          const values = payload && payload.values;
          if (Array.isArray(values)) {
            if (els.length === 1 && !Array.isArray(values)) {
              _writeValue(els[0], values);
            } else {
              els.forEach(function (el, i) { _writeValue(el, values[i]); });
            }
          }
        }
        if (typeof opts.onFound === 'function') opts.onFound(payload, r.data);
        return r.data;
      }
      return null;
    },

    /** 删除草稿（保存成功或用户主动放弃时调用） */
    async clear(opts) {
      if (!opts || !opts.scope || !opts.key) return;
      // API.del 只接 endpoint，query 手动拼
      const qs = '?scope=' + encodeURIComponent(opts.scope) + '&key=' + encodeURIComponent(opts.key);
      await _safe(_api().del('/api/auto-save/drafts' + qs));
    },

    /**
     * 列出当前用户某 scope 下所有草稿
     * @returns {Promise<Array>} 草稿列表
     */
    async list(scope) {
      if (!scope) return [];
      const r = await _safe(_api().get('/api/auto-save/drafts', { scope: scope }));
      return (r && r.code === 200 && r.data) ? r.data : [];
    },

    /**
     * 落一条 AI 生成结果到后端
     * @param {Object} opts
     *   - type: 'product_card' | 'ai_write' | ...
     *   - productId: 关联商品（可空）
     *   - result: AI 输出对象
     *   - meta: { model, provider, ... }
     * @returns {Promise<Object|null>} 后端返回或 null
     */
    async saveAiResult(opts) {
      if (!opts || !opts.type) return null;
      const r = await _safe(_api().post('/api/ai-results', {
        type: opts.type,
        product_id: opts.productId || '',
        result: opts.result || {},
        meta: opts.meta || {},
      }));
      return (r && r.code === 200) ? r : null;
    },

    /**
     * 静默 Toast 提示（仅在用户可见操作时调用，避免骚扰）
     * 用法：AutoSave.toast('草稿已保存', 1200)
     */
    toast(msg, ms) {
      try {
        if (global.Toast && typeof global.Toast.show === 'function') {
          global.Toast.show(msg, 'info', ms || 1200);
        }
      } catch (e) { /* noop */ }
    },
  };

  global.AutoSave = AutoSave;

  // 自动安装 beforeunload 兜底
  AutoSave.installBeforeUnloadGuard();
})(typeof window !== 'undefined' ? window : globalThis);
