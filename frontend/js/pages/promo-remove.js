/**
 * 自动移除促销 - Promo Remove Page
 *
 * 【模块职责】
 * Ozon 平台会把商品「自动拉入」各类促销活动。如果活动价低于业务员在 Ozon 后台
 * 为商品设置的「最低售价」（min_price，即涂总说的"促销活动限制价格"），就会
 * 亏本出单。本页负责：
 *
 *   1. 检测（只读）
 *      · R1 命中：活动价 action_price < 限制价 min_price（严格小于）
 *      · R2 区分来源：add_mode = AUTO（平台自动拉入，重点治理对象）
 *                     add_mode = MANUAL（业务员手动加入，一般不动）
 *      · R3 积分评价：promotions[].type = REVIEWS_PROMO 且 is_enabled = true
 *        （「快速收集评价」与「积分评价」在 Ozon 是同一个功能，统一看这个字段）
 *
 *   2. 移除（写）
 *      · 主操作 POST /v1/actions/products/deactivate（把商品移出活动）
 *      · 辅助   POST /v1/actions/auto-add/products/delete（把商品移出自动添加名单，
 *               否则下次平台会自动把它再加回来）—— 仅 AUTO 且有 auto_add_date 时执行
 *
 * 【执行策略】
 * 涂总拍板：「检测到就自动移除，同时留记录」。
 *   · 进入本页 → 自动扫描 → 命中项自动移除（受「自动移除」开关控制）
 *   · 登录后 → 若有未处理命中 / 移除失败 → 弹窗告知业务员（原始需求的"弹窗提醒"）
 *   · 每一次移除都写 promo_removal_logs 留痕，可回溯
 *
 * 【安全阀】
 *   · 「预演」按钮 dry-run，只看不执行
 *   · 「自动移除」开关（localStorage 持久化），关掉后只检测不动作
 *   · 单次自动移除上限 AUTO_REMOVE_LIMIT，超过则只提示不动作，防误伤
 *   · 移除有配额与店铺评分风险；Ozon 无「删除活动」接口，只能移除商品
 *
 * 【依赖】API / Api / Toast / Modal / Router / Store（均为词法全局，不挂 window）
 */
const PromoRemovePage = (() => {
  const state = {
    data: null,
    hits: [],
    stores: [],
    scanning: false,
    scanAt: '',
    storeFilter: '',
    addModeFilter: '',
    tab: 'hits',
    logs: [],
    products: [],
    actions: [],
  };

  const LS_AUTO_KEY = 'erp_promo_auto_remove';
  const AUTO_REMOVE_LIMIT = 50;
  const SCAN_TIMEOUT = 300000;

  // ======================= 小工具 =======================

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function isAdmin() {
    try { return Store?.getUser?.()?.role === 'admin'; } catch (e) { return false; }
  }

  function price(v) {
    const n = Number(v || 0);
    if (!n) return '—';
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
  }

  function num(v) {
    return Number(v || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
  }

  function autoRemoveEnabled() {
    try {
      const v = localStorage.getItem(LS_AUTO_KEY);
      return v === null ? true : v === '1';
    } catch (e) { return true; }
  }

  function setAutoRemove(on) {
    try { localStorage.setItem(LS_AUTO_KEY, on ? '1' : '0'); } catch (e) { /* noop */ }
  }

  // ======================= 数据 =======================

  async function loadOverview() {
    const res = await API.get('/api/promotions/overview');
    if (res && res.code === 200) {
      state.data = res.data;
      state.stores = res.data.stores || [];
      state.logs = res.data.logs || [];
      state.products = res.data.products || [];
      return res.data;
    }
    if (res && res.msg) Toast?.show?.(res.msg, 'error');
    return null;
  }

  async function scan(silent) {
    if (state.scanning) return null;
    state.scanning = true;
    const btn = document.getElementById('prScanBtn');
    if (btn) { btn.disabled = true; btn.textContent = '扫描中…'; }
    if (!silent) Toast?.show?.('正在扫描促销活动，请稍候…', 'info');
    try {
      const res = await Api.request('/api/promotions/scan', { method: 'GET', timeout: SCAN_TIMEOUT });
      if (res && res.code === 200) {
        state.hits = res.data.hits || [];
        state.scanAt = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        if (!silent) Toast?.show?.(res.msg || ('扫描完成：命中 ' + state.hits.length + ' 个商品'), 'success');
        return res.data;
      }
      if (!silent) Toast?.show?.((res && res.msg) || '扫描失败', 'error');
      return null;
    } catch (e) {
      if (!silent) Toast?.show?.('扫描异常：' + e.message, 'error');
      return null;
    } finally {
      state.scanning = false;
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="scan-line" style="width:14px;height:14px;"></i> 立即扫描';
        if (window.lucide) lucide.createIcons();
      }
    }
  }

  function slim(h) {
    return {
      storePk: h.storePk, storeId: h.storeId, storeName: h.storeName,
      actionId: h.actionId, actionTitle: h.actionTitle,
      productId: h.productId, offerId: h.offerId, sku: h.sku, title: h.title,
      actionPrice: h.actionPrice, basePrice: h.basePrice, minPrice: h.minPrice,
      discountPercent: h.discountPercent, addMode: h.addMode, autoAddDate: h.autoAddDate,
      isAuto: h.isAuto,
    };
  }

  async function doRemove(hits, quiet, dryRun) {
    if (!hits || !hits.length) return null;
    const payload = { hits: hits.map(slim) };
    if (dryRun) payload.dryRun = true;
    try {
      const res = await Api.request('/api/promotions/remove', {
        method: 'POST',
        body: JSON.stringify(payload),
        timeout: 300000,
      });
      if (res && res.code === 200) {
        if (!quiet) {
          Toast?.show?.(res.msg || ('移除完成：' + ((res.data || {}).removedCount || 0) + ' 个'),
            (((res.data || {}).failedCount) ? 'warning' : 'success'));
        }
        return res.data;
      }
      if (!quiet) Toast?.show?.((res && res.msg) || '移除失败', 'error');
      return null;
    } catch (e) {
      if (!quiet) Toast?.show?.('移除异常：' + e.message, 'error');
      return null;
    }
  }

  // ======================= 页面骨架 =======================

  function render() {
    return `
      <div style="animation: pageEnter .35s ease;">
        <div class="store-page-header">
          <div>
            <h2 class="store-page-title">自动移除促销</h2>
            <p class="store-page-desc">
              盯住 Ozon 平台<strong>自动拉入</strong>的促销活动。当活动价低于业务员在 Ozon 后台设置的
              <strong>最低售价（限制价）</strong>时，自动把商品移出活动并留痕；
              同时识别开启了<strong>积分评价</strong>（含快速收集评价）的商品。
              <span style="color:#98a2b3;">限制价来源：Ozon 后台 min_price</span>
            </p>
          </div>
          <div class="store-header-actions">
            ${autoToggleHtml()}
            <button class="btn btn-sm btn-ghost" onclick="PromoRemovePage.refresh()">
              <i data-lucide="rotate-cw" style="width:14px;height:14px;"></i> 刷新
            </button>
            <button class="btn btn-sm btn-ghost" onclick="PromoRemovePage.dryRun()">
              <i data-lucide="flask-conical" style="width:14px;height:14px;"></i> 预演
            </button>
            <button class="btn btn-sm btn-primary" id="prScanBtn" onclick="PromoRemovePage.scanNow()">
              <i data-lucide="scan-line" style="width:14px;height:14px;"></i> 立即扫描
            </button>
          </div>
        </div>
        <div id="prBanner"></div>
        <div id="prBody"></div>
      </div>`;
  }

  function autoToggleHtml() {
    const on = autoRemoveEnabled();
    return `
      <label title="开启后：进入本页自动扫描，命中即自动移除并留痕。关闭后只检测不动作。"
             style="display:inline-flex;align-items:center;gap:8px;padding:7px 12px;border:1px solid #e4e7ec;
                    border-radius:8px;background:#fff;cursor:pointer;font-size:13px;color:#344054;user-select:none;">
        <input type="checkbox" ${on ? 'checked' : ''} onchange="PromoRemovePage.toggleAuto(this.checked)"
               style="width:15px;height:15px;cursor:pointer;accent-color:#4A90D9;">
        自动移除
      </label>`;
  }

  function renderBanner() {
    const s = (state.data || {}).stats || {};
    const pendingCred = (state.stores || []).filter(x => !x.credentialsReady).length;
    const failed = s.failed || 0;
    let html = '';

    if (state.hits.length) {
      const autoCnt = state.hits.filter(h => h.isAuto).length;
      html += `
        <div style="margin:14px 0;padding:14px 16px;border:1px solid #fecaca;background:#fef2f2;
                    border-radius:12px;display:flex;gap:12px;align-items:flex-start;">
          <i data-lucide="alert-triangle" style="width:20px;height:20px;color:#dc2626;flex:none;margin-top:1px;"></i>
          <div style="flex:1;">
            <div style="font-weight:600;color:#b91c1c;margin-bottom:6px;">
              本次扫描命中 ${state.hits.length} 个商品（活动价低于限制价），其中平台自动拉入 ${autoCnt} 个</div>
            <div style="font-size:13px;color:#7f1d1d;">
              低于限制价出单会造成亏损。
              ${autoRemoveEnabled()
                ? '「自动移除」已开启，命中数不超过 ' + AUTO_REMOVE_LIMIT + ' 个时会自动处理。'
                : '「自动移除」已关闭，请手动处理。'}
            </div>
          </div>
          <button class="btn btn-sm btn-primary" style="flex:none;" onclick="PromoRemovePage.removeAll()">
            <i data-lucide="shield-off" style="width:14px;height:14px;"></i> 全部移除
          </button>
        </div>`;
    }

    if (failed > 0) {
      html += `
        <div style="margin:14px 0;padding:12px 16px;border:1px solid #fde68a;background:#fffbeb;
                    border-radius:12px;display:flex;gap:10px;align-items:center;">
          <i data-lucide="circle-alert" style="width:18px;height:18px;color:#d97706;flex:none;"></i>
          <div style="flex:1;font-size:13px;color:#92400e;">
            历史上有 <strong>${failed}</strong> 次移除失败，可能是活动已开始禁止退出或触发配额限制，
            请到「移除留痕」查看原因，必要时到 Ozon 后台手动处理。
          </div>
        </div>`;
    }

    if (pendingCred > 0) {
      html += `
        <div style="margin:14px 0;padding:12px 16px;border:1px solid #e4e7ec;background:#f9fafb;
                    border-radius:12px;display:flex;gap:10px;align-items:center;">
          <i data-lucide="key-round" style="width:18px;height:18px;color:#667085;flex:none;"></i>
          <div style="flex:1;font-size:13px;color:#475467;">
            有 <strong>${pendingCred}</strong> 家店铺未配置 Ozon Seller API 凭证，无法扫描。
            ${isAdmin() ? '请到「店铺管理」补全 client_id / api_key。' : '请联系管理员配置。'}
          </div>
        </div>`;
    }
    return html;
  }

  function renderStats() {
    const s = (state.data || {}).stats || {};
    const card = (label, value, hint, color) => `
      <div style="flex:1 1 150px;min-width:140px;padding:14px 16px;border:1px solid #e4e7ec;
                  border-radius:12px;background:#fff;">
        <div style="font-size:12px;color:#667085;margin-bottom:6px;">${esc(label)}</div>
        <div style="font-size:22px;font-weight:600;color:${color || '#101828'};line-height:1.2;">${value}</div>
        ${hint ? `<div style="font-size:11px;color:#98a2b3;margin-top:4px;">${esc(hint)}</div>` : ''}
      </div>`;
    const hitNow = state.hits.length || s.hitProducts || 0;
    return `
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;">
        ${card('命中商品', hitNow, state.scanAt ? '扫描于 ' + state.scanAt : '点击「立即扫描」',
                hitNow ? '#dc2626' : '#101828')}
        ${card('积分评价开启', s.reviewPromos || 0, '含快速收集评价',
                s.reviewPromos ? '#d97706' : '#101828')}
        ${card('累计已移除', s.removed || 0, '历史成功移除次数', '#16a34a')}
        ${card('移除失败', s.failed || 0, '需人工处理', s.failed ? '#dc2626' : '#101828')}
        ${card('可见店铺', (state.stores || []).length, '当前账号权限范围内')}
      </div>`;
  }

  function renderTabs() {
    const tabs = [
      { key: 'hits',     label: '命中清单', count: state.hits.length },
      { key: 'review',   label: '积分评价', count: ((state.data || {}).stats || {}).reviewPromos || 0 },
      { key: 'logs',     label: '移除留痕', count: state.logs.length },
      { key: 'products', label: '商品快照', count: state.products.length },
      { key: 'actions',  label: '活动清单', count: state.actions.length },
    ];
    return `
      <div style="display:flex;gap:4px;border-bottom:1px solid #e4e7ec;margin-bottom:16px;flex-wrap:wrap;">
        ${tabs.map(t => {
          const on = state.tab === t.key;
          return `<button onclick="PromoRemovePage.switchTab('${t.key}')"
            style="padding:9px 14px;border:none;background:none;cursor:pointer;font-size:13px;
                   font-weight:${on ? 600 : 400};color:${on ? '#4A90D9' : '#667085'};
                   border-bottom:2px solid ${on ? '#4A90D9' : 'transparent'};margin-bottom:-1px;">
            ${esc(t.label)}${t.count ? ` <span style="color:#98a2b3;">(${t.count})</span>` : ''}
          </button>`;
        }).join('')}
      </div>`;
  }

  function renderBody() {
    switch (state.tab) {
      case 'review':   return renderReview();
      case 'logs':     return renderLogs();
      case 'products': return renderProducts();
      case 'actions':  return renderActions();
      default:         return renderHits();
    }
  }

  function emptyState(icon, title, desc) {
    return `
      <div style="padding:56px 20px;text-align:center;border:1px dashed #e4e7ec;border-radius:12px;background:#fff;">
        <i data-lucide="${icon}" style="width:36px;height:36px;color:#cbd5e1;"></i>
        <div style="font-size:15px;font-weight:600;color:#475467;margin:12px 0 6px;">${esc(title)}</div>
        <div style="font-size:13px;color:#98a2b3;max-width:480px;margin:0 auto;line-height:1.7;">${desc}</div>
      </div>`;
  }

  // ======================= Tab：命中清单 =======================

  function renderHits() {
    if (!state.scanAt) {
      return emptyState('scan-line', '尚未扫描',
        '点击右上角「立即扫描」从 Ozon 拉取当前活动商品，比对业务员设置的限制价。'
        + '<br>扫描会实时调用 Ozon 接口，店铺多时可能耗时较久。');
    }
    let hits = state.hits.slice();
    if (state.storeFilter) hits = hits.filter(h => String(h.storePk) === String(state.storeFilter));
    if (state.addModeFilter) hits = hits.filter(h => (h.isAuto ? 'AUTO' : 'MANUAL') === state.addModeFilter);

    if (!hits.length) {
      return emptyState('check-circle-2', '没有命中',
        '当前没有「活动价低于限制价」的商品，状况良好。');
    }

    const storeOpts = [{ v: '', t: '全部店铺' }].concat(
      (state.stores || []).map(s => ({ v: s.storePk, t: s.storeName })));
    const rows = hits.map(h => {
      const isAuto = !!h.isAuto;
      const sev = isAuto ? '#dc2626' : '#d97706';
      const idx = state.hits.indexOf(h);
      return `
        <tr style="border-top:1px solid #f2f4f7;">
          <td style="padding:10px 8px;">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
              <span style="display:inline-block;padding:1px 6px;border-radius:5px;font-size:11px;
                           background:${isAuto ? '#fef2f2' : '#fffbeb'};color:${sev};
                           border:1px solid ${isAuto ? '#fecaca' : '#fde68a'};">
                ${isAuto ? '平台自动' : '人工加入'}
              </span>
              <span style="font-size:11px;color:#98a2b3;">活动 #${esc(h.actionId)}</span>
            </div>
            <div style="font-weight:500;color:#101828;max-width:320px;overflow:hidden;
                        text-overflow:ellipsis;white-space:nowrap;" title="${esc(h.title)}">
              ${esc(h.title || h.offerId || h.productId)}
            </div>
            <div style="font-size:11px;color:#98a2b3;margin-top:2px;">
              ${esc(h.offerId || '')} · 商品ID ${esc(h.productId)}
            </div>
          </td>
          <td style="padding:10px 8px;font-size:12px;color:#475467;">${esc(h.storeName || '')}</td>
          <td style="padding:10px 8px;font-size:12px;color:#475467;max-width:170px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap;" title="${esc(h.actionTitle)}">
            ${esc(h.actionTitle || '—')}
          </td>
          <td style="padding:10px 8px;text-align:right;">${price(h.basePrice)}</td>
          <td style="padding:10px 8px;text-align:right;font-weight:600;color:${sev};">
            ${price(h.actionPrice)}
          </td>
          <td style="padding:10px 8px;text-align:right;color:#101828;">${price(h.minPrice)}</td>
          <td style="padding:10px 8px;text-align:right;font-weight:600;color:#dc2626;">
            ${h.gapPercent !== undefined && h.gapPercent !== null ? num(h.gapPercent) + '%' : '—'}
          </td>
          <td style="padding:10px 8px;text-align:right;color:#667085;">
            ${h.discountPercent !== undefined && h.discountPercent !== null ? num(h.discountPercent) + '%' : '—'}
          </td>
          <td style="padding:10px 8px;text-align:right;white-space:nowrap;">
            <button class="btn btn-xs btn-primary" onclick="PromoRemovePage.removeOne(${idx})">移除</button>
          </td>
        </tr>`;
    }).join('');

    return `
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px;">
        <select class="form-select" style="width:auto;min-width:150px;"
                onchange="PromoRemovePage.filterStore(this.value)">
          ${storeOpts.map(o => `<option value="${esc(o.v)}" ${String(o.v) === String(state.storeFilter) ? 'selected' : ''}>${esc(o.t)}</option>`).join('')}
        </select>
        <select class="form-select" style="width:auto;min-width:140px;"
                onchange="PromoRemovePage.filterMode(this.value)">
          <option value="" ${state.addModeFilter === '' ? 'selected' : ''}>全部来源</option>
          <option value="AUTO" ${state.addModeFilter === 'AUTO' ? 'selected' : ''}>平台自动拉入</option>
          <option value="MANUAL" ${state.addModeFilter === 'MANUAL' ? 'selected' : ''}>业务员手动加入</option>
        </select>
        <span style="font-size:12px;color:#98a2b3;">当前显示 ${hits.length} 条</span>
        <div style="flex:1;"></div>
        <button class="btn btn-sm btn-primary" onclick="PromoRemovePage.removeFiltered()">
          <i data-lucide="shield-off" style="width:14px;height:14px;"></i> 移除当前筛选（${hits.length}）
        </button>
      </div>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">商品</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:left;">促销活动</th>
            <th style="text-align:right;">原价</th>
            <th style="text-align:right;">活动价</th>
            <th style="text-align:right;">限制价</th>
            <th style="text-align:right;">低于限制</th>
            <th style="text-align:right;">折扣</th>
            <th style="text-align:right;">操作</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div style="margin-top:10px;font-size:12px;color:#98a2b3;line-height:1.8;">
        判定规则：<code>活动价 &lt; 限制价</code>（严格小于，相等不算命中）。限制价 = Ozon 后台为商品设置的 <code>min_price</code>。<br>
        「平台自动」= Ozon 自动拉入（<code>add_mode = AUTO</code>）；移除时会同时把商品移出「自动添加名单」，防止下次被重新拉入。
      </div>`;
  }

  // ======================= Tab：积分评价 =======================

  function renderReview() {
    const list = state.products.filter(p => p.reviewsPromoEnabled);
    if (!list.length) {
      return emptyState('sparkles', '没有开启积分评价的商品',
        '「快速收集评价」与「积分评价」在 Ozon 是同一个功能，这里统一按 <code>REVIEWS_PROMO</code> 字段识别。'
        + '<br>扫描一次后才有数据。');
    }
    const rows = list.map(p => `
      <tr style="border-top:1px solid #f2f4f7;">
        <td style="padding:10px 8px;">
          <div style="font-weight:500;color:#101828;">${esc(p.title || p.offer_id || p.product_id)}</div>
          <div style="font-size:11px;color:#98a2b3;margin-top:2px;">
            ${esc(p.offer_id || '')} · 商品ID ${esc(p.product_id)}
          </div>
        </td>
        <td style="padding:10px 8px;font-size:12px;color:#475467;">${esc(p.storeName || '')}</td>
        <td style="padding:10px 8px;text-align:right;">${price(p.base_price)}</td>
        <td style="padding:10px 8px;text-align:right;">${price(p.min_price)}</td>
        <td style="padding:10px 8px;text-align:right;">
          <span style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;
                       background:#fffbeb;color:#d97706;border:1px solid #fde68a;">已开启</span>
        </td>
        <td style="padding:10px 8px;text-align:right;font-size:12px;color:#98a2b3;">
          ${Number(p.auto_action_count || 0) > 0 ? '同时在 ' + p.auto_action_count + ' 个自动促销中' : '—'}
        </td>
      </tr>`).join('');
    return `
      <div style="margin-bottom:12px;padding:12px 14px;border:1px solid #fde68a;background:#fffbeb;
                  border-radius:10px;font-size:13px;color:#92400e;line-height:1.7;">
        <strong>积分评价</strong>（含快速收集评价）会让 Ozon 用积分补贴买家评价，可能带来额外成本。
        ERP 只能读取识别，<strong>无法代业务员开关该功能</strong>，请到 Ozon 后台「评价管理」自行处理。
      </div>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">商品</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:right;">售价</th>
            <th style="text-align:right;">限制价</th>
            <th style="text-align:right;">积分评价</th>
            <th style="text-align:right;">备注</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= Tab：移除留痕 =======================

  function renderLogs() {
    if (!state.logs.length) {
      return emptyState('history', '暂无留痕', '还没有执行过移除操作。每一次移除（含失败）都会记录在此。');
    }
    const badge = (r) => {
      const map = {
        removed: ['已移除', '#16a34a', '#f0fdf4', '#bbf7d0'],
        failed:  ['失败',   '#dc2626', '#fef2f2', '#fecaca'],
        kept:    ['保留',   '#667085', '#f9fafb', '#e4e7ec'],
      };
      const m = map[r] || ['—', '#667085', '#f9fafb', '#e4e7ec'];
      return `<span style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;
                   background:${m[2]};color:${m[1]};border:1px solid ${m[3]};">${m[0]}</span>`;
    };
    const rows = state.logs.map(l => `
      <tr style="border-top:1px solid #f2f4f7;">
        <td style="padding:9px 8px;text-align:right;color:#667085;white-space:nowrap;">${esc(l.created_at || '')}</td>
        <td style="padding:9px 8px;font-size:12px;color:#475467;">${esc(l.storeName || l.store_name || '')}</td>
        <td style="padding:9px 8px;">
          <div style="color:#101828;">${esc(l.title || l.offer_id || l.product_id)}</div>
          <div style="font-size:11px;color:#98a2b3;">${esc(l.action_title || '')}</div>
        </td>
        <td style="padding:9px 8px;text-align:right;">${price(l.action_price)}</td>
        <td style="padding:9px 8px;text-align:right;">${price(l.min_price)}</td>
        <td style="padding:9px 8px;text-align:right;">${badge(l.result)}</td>
        <td style="padding:9px 8px;text-align:right;font-size:12px;color:#667085;">
          ${esc(l.trigger_type === 'auto' ? '自动' : '手动')}
        </td>
        <td style="padding:9px 8px;font-size:11px;color:#98a2b3;max-width:240px;">${esc(l.message || '')}</td>
      </tr>`).join('');
    return `
      <div style="margin-bottom:12px;">
        <button class="btn btn-sm btn-ghost" onclick="PromoRemovePage.loadLogs()">
          <i data-lucide="rotate-cw" style="width:14px;height:14px;"></i> 刷新留痕
        </button>
      </div>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:right;">时间</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:left;">商品 / 活动</th>
            <th style="text-align:right;">活动价</th>
            <th style="text-align:right;">限制价</th>
            <th style="text-align:right;">结果</th>
            <th style="text-align:right;">触发</th>
            <th style="text-align:left;">说明</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= Tab：商品快照 =======================

  function renderProducts() {
    if (!state.products.length) {
      return emptyState('package', '暂无快照',
        '扫描一次后，这里会保存每个商品的促销状态，供「登录提醒」使用（避免每次登录都打 Ozon 接口）。');
    }
    const rows = state.products.map(p => {
      const auto = Number(p.auto_action_count || 0);
      return `
      <tr style="border-top:1px solid #f2f4f7;">
        <td style="padding:10px 8px;">
          <div style="font-weight:500;color:#101828;">${esc(p.title || p.offer_id || p.product_id)}</div>
          <div style="font-size:11px;color:#98a2b3;margin-top:2px;">
            ${esc(p.offer_id || '')} · 商品ID ${esc(p.product_id)}
          </div>
        </td>
        <td style="padding:10px 8px;font-size:12px;color:#475467;">${esc(p.storeName || '')}</td>
        <td style="padding:10px 8px;text-align:right;">${price(p.base_price)}</td>
        <td style="padding:10px 8px;text-align:right;">${price(p.min_price)}</td>
        <td style="padding:10px 8px;text-align:right;">
          ${auto > 0 ? '<span style="color:#dc2626;font-weight:600;">' + auto + '</span>'
                     : '<span style="color:#98a2b3;">0</span>'}
        </td>
        <td style="padding:10px 8px;text-align:right;">
          ${p.reviewsPromoEnabled ? '<span style="color:#d97706;">已开启</span>'
                                  : '<span style="color:#98a2b3;">未开启</span>'}
        </td>
        <td style="padding:10px 8px;text-align:right;font-size:12px;color:#98a2b3;">
          ${esc(p.updated_at || '')}
        </td>
      </tr>`;
    }).join('');
    return `
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">商品</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:right;">售价</th>
            <th style="text-align:right;">限制价</th>
            <th style="text-align:right;">自动促销数</th>
            <th style="text-align:right;">积分评价</th>
            <th style="text-align:right;">更新于</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= Tab：活动清单 =======================

  function renderActions() {
    if (!state.actions.length) {
      return emptyState('tag', '尚未拉取活动清单',
        '点击下方「拉取活动」从 Ozon 读取当前所有促销活动及其自动添加时间。'
        + '<br>「自动添加时间」是调用自动添加名单接口的必填参数。');
    }
    const rows = state.actions.map(a => {
      const dates = Array.isArray(a.autoAddDates) ? a.autoAddDates : [];
      return `
      <tr style="border-top:1px solid #f2f4f7;">
        <td style="padding:10px 8px;">
          <div style="font-weight:500;color:#101828;">${esc(a.title || '—')}</div>
          <div style="font-size:11px;color:#98a2b3;margin-top:2px;">活动 ID ${esc(a.id)}</div>
        </td>
        <td style="padding:10px 8px;font-size:12px;color:#475467;">${esc(a.storeName || '')}</td>
        <td style="padding:10px 8px;font-size:12px;color:#475467;white-space:nowrap;">
          ${esc(a.dateStart || a.date_start || '')} ~ ${esc(a.dateEnd || a.date_end || '')}
        </td>
        <td style="padding:10px 8px;text-align:right;">
          ${a.isParticipating || a.is_participating
            ? '<span style="color:#16a34a;">参与中</span>'
            : '<span style="color:#98a2b3;">未参与</span>'}
        </td>
        <td style="padding:10px 8px;text-align:right;">${Number(a.participatingProductsCount || a.participating_products_count || 0)}</td>
        <td style="padding:10px 8px;text-align:right;">
          ${dates.length
            ? '<span style="color:#d97706;">' + dates.map(d => esc(d)).join('<br>') + '</span>'
            : '<span style="color:#98a2b3;">—</span>'}
        </td>
      </tr>`;
    }).join('');
    return `
      <div style="margin-bottom:12px;">
        <button class="btn btn-sm btn-ghost" onclick="PromoRemovePage.loadActions()">
          <i data-lucide="download" style="width:14px;height:14px;"></i> 拉取活动
        </button>
      </div>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">活动</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:left;">活动期</th>
            <th style="text-align:right;">状态</th>
            <th style="text-align:right;">参与商品数</th>
            <th style="text-align:right;">自动添加时间</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= 交互动作 =======================

  function paint() {
    const banner = document.getElementById('prBanner');
    const body = document.getElementById('prBody');
    if (banner) banner.innerHTML = renderBanner();
    if (body) body.innerHTML = renderStats() + renderTabs() + renderBody();
    if (window.lucide) lucide.createIcons();
  }

  async function refresh(silent) {
    await loadOverview();
    paint();
    if (!silent) Toast?.show?.('已刷新', 'success');
  }

  async function scanNow() {
    const data = await scan(false);
    if (!data) return;
    paint();
    if (state.hits.length && autoRemoveEnabled() && state.hits.length <= AUTO_REMOVE_LIMIT) {
      await autoRemoveNow(state.hits, 'scan');
    }
  }

  function toggleAuto(on) {
    setAutoRemove(on);
    Toast?.show?.(on ? '已开启自动移除：命中即自动处理并留痕' : '已关闭自动移除：仅检测不动作', 'info');
    paint();
  }

  async function dryRun() {
    if (!state.hits.length) {
      Toast?.show?.('先点「立即扫描」拿到命中清单再预演', 'info');
      return;
    }
    const res = await doRemove(state.hits, true, true);
    if (!res) { Toast?.show?.('预演失败', 'error'); return; }
    const lines = (res.skipped || []).concat(res.skippedList || []);
    Modal.show({
      title: '预演结果（未真实调用 Ozon）',
      size: 'md',
      body: `
        <div style="font-size:13px;color:#475467;line-height:1.9;">
          <div>将处理命中商品：<strong>${state.hits.length}</strong> 个</div>
          <div>其中平台自动拉入：<strong style="color:#dc2626;">${state.hits.filter(h => h.isAuto).length}</strong> 个</div>
          <div>跳过（缺活动日期等）：<strong>${res.skippedCount || 0}</strong> 个</div>
        </div>
        ${lines.length ? `<div style="margin-top:12px;padding:10px 12px;background:#f9fafb;border-radius:8px;
              font-size:12px;color:#667085;max-height:220px;overflow:auto;">
          ${lines.map(x => esc(typeof x === 'string' ? x : ((x.title || x.offerId || x.productId || '') + '：' + (x.message || '')))).join('<br>')}</div>` : ''}`,
      footer: [{ text: '知道了', class: 'btn-primary' }],
    });
  }

  async function doRemoveFilteredSafe(hits, label) {
    if (!hits.length) { Toast?.show?.('当前筛选下没有命中商品', 'info'); return; }
    const ok = await Modal.confirm(
      `确认移除 ${hits.length} 个商品的活动资格？\n\n` +
      `范围：${label}\n` +
      `说明：移除后商品退出当前活动；平台自动加入的会同时移出自动添加名单，避免被再次拉入。\n` +
      `该操作会调用 Ozon 正式接口，且会写入留痕记录。`);
    if (!ok) return;
    const res = await doRemove(hits, false);
    if (res) { await refresh(true); afterRemove(res); }
  }

  async function removeAll() {
    await doRemoveFilteredSafe(state.hits, '全部命中');
  }

  async function removeFiltered() {
    await doRemoveFilteredSafe(filteredHits(), '当前筛选');
  }

  function filteredHits() {
    let hits = state.hits.slice();
    if (state.storeFilter) hits = hits.filter(h => String(h.storePk) === String(state.storeFilter));
    if (state.addModeFilter) hits = hits.filter(h => (h.isAuto ? 'AUTO' : 'MANUAL') === state.addModeFilter);
    return hits;
  }

  async function removeOne(idx) {
    const h = state.hits[idx];
    if (!h) return;
    const ok = await Modal.confirm(
      `确认把「${h.title || h.offerId || h.productId}」移出活动 ${h.actionId}？\n\n` +
      `活动价 ${price(h.actionPrice)} < 限制价 ${price(h.minPrice)}，低于限制价出单会亏损。`);
    if (!ok) return;
    const res = await doRemove([h], false);
    if (res) { await refresh(true); afterRemove(res); }
  }

  function afterRemove(res) {
    const failed = (res.failed || 0);
    if (failed > 0) {
      const items = (res.failedList || res.failedItems || []).slice(0, 10);
      Modal.show({
        title: '部分商品移除失败',
        size: 'md',
        body: `
          <div style="font-size:13px;color:#475467;line-height:1.8;">
            成功 <strong style="color:#16a34a;">${res.removedCount || 0}</strong> 个，
            失败 <strong style="color:#dc2626;">${failed}</strong> 个。
            失败项多为活动已开始/已锁定、或触发接口配额限制，请到 Ozon 后台手动处理。
          </div>
          ${items.length ? `<div style="margin-top:12px;padding:10px 12px;background:#fef2f2;border-radius:8px;
                font-size:12px;color:#7f1d1d;max-height:200px;overflow:auto;">
            ${items.map(x => esc(typeof x === 'string' ? x : `${x.title || x.productId || ''}：${x.message || x.msg || ''}`)).join('<br>')}
          </div>` : ''}`,
        footer: [{ text: '知道了', class: 'btn-primary' }],
      });
    } else if ((res.removedCount || 0) > 0) {
      Toast?.show?.(`已移除 ${res.removedCount} 个商品并留痕`, 'success');
    }
  }

  async function autoRemoveNow(hits, trigger) {
    if (!hits.length) return;
    const res = await doRemove(hits, true);
    if (!res) return;
    if ((res.removedCount || 0) > 0) {
      Toast?.show?.(`自动移除完成：成功 ${res.removedCount} 个${res.failedCount ? `，失败 ${res.failedCount} 个` : ''}`,
        res.failedCount ? 'warning' : 'success');
    }
    await refresh(true);
    afterRemove(res);
  }

  function switchTab(tab) {
    state.tab = tab;
    paint();
    if (tab === 'logs' && !state.logs.length) loadLogs();
    if (tab === 'actions' && !state.actions.length) loadActions();
  }

  function filterStore(v) { state.storeFilter = v || ''; paint(); }
  function filterMode(v)  { state.addModeFilter = v || ''; paint(); }

  async function loadLogs() {
    try {
      const res = await API.get('/api/promotions/logs', { limit: 200 });
      if (res && res.code === 200) {
        const d = res.data || {};
        state.logs = d.logs || d.list || [];
        paint();
      } else if (res && res.msg) Toast?.show?.(res.msg, 'error');
    } catch (e) { Toast?.show?.('加载留痕失败：' + e.message, 'error'); }
  }

  async function loadActions() {
    try {
      const res = await API.get('/api/promotions/actions');
      if (res && res.code === 200) {
        const d = res.data || {};
        state.actions = d.actions || d.list || [];
        paint();
        Toast?.show?.(res.msg || ('已拉取 ' + state.actions.length + ' 条活动'), 'success');
      } else if (res && res.msg) Toast?.show?.(res.msg, 'error');
    } catch (e) { Toast?.show?.('拉取活动失败：' + e.message, 'error'); }
  }

  // ======================= 登录后弹窗提醒 =======================

  // 需求原文：「如不行，请通过弹窗信息告诉业务员要消除」。
  // 本功能可自动移除，所以弹窗改为「结果告知 + 失败兜底」：
  // 登录后 / 进入页面时若有未处理命中或历史移除失败，弹窗提醒业务员。
  function showPromoPopup(alerts) {
    const counts = (alerts && alerts.counts) || {};
    const autoProducts = (alerts && alerts.autoProducts) || [];
    const failedRemovals = (alerts && alerts.failedRemovals) || [];
    const reviewPromos = (alerts && alerts.reviewPromos) || [];
    const lines = [];
    if (counts.autoProducts) {
      lines.push(`<div style="margin-bottom:6px;">
        <span style="color:#dc2626;font-weight:600;">${counts.autoProducts}</span> 个商品的活动价低于限制价（平台自动拉入），已自动移除或待处理</div>`);
    }
    if (counts.failedRemovals) {
      lines.push(`<div style="margin-bottom:6px;">
        <span style="color:#d97706;font-weight:600;">${counts.failedRemovals}</span> 次移除失败，需到 Ozon 后台手动处理</div>`);
    }
    if (counts.reviewPromos) {
      lines.push(`<div><span style="color:#d97706;font-weight:600;">${counts.reviewPromos}</span> 个商品开启了积分评价（含快速收集评价），请注意积分成本</div>`);
    }
    const sample = autoProducts.slice(0, 5).map(p => `
      <tr>
        <td style="padding:6px 8px;font-size:12px;color:#101828;max-width:220px;overflow:hidden;
                   text-overflow:ellipsis;white-space:nowrap;">${esc(p.title || p.offerId || p.productId)}</td>
        <td style="padding:6px 8px;text-align:right;font-size:12px;color:#475467;">${price(p.actionPrice != null ? p.actionPrice : p.action_price)}</td>
        <td style="padding:6px 8px;text-align:right;font-size:12px;color:#dc2626;">${price(p.minPrice != null ? p.minPrice : p.min_price)}</td>
      </tr>`).join('');
    Modal.show({
      title: '促销活动提醒',
      size: 'md',
      body: `
        <div style="font-size:13px;color:#475467;line-height:1.8;">${lines.join('') || '暂无异常'}</div>
        ${sample ? `
          <div style="margin-top:12px;border:1px solid #fecaca;border-radius:10px;overflow:hidden;">
            <table style="width:100%;border-collapse:collapse;">
              <thead><tr style="background:#fef2f2;">
                <th style="padding:6px 8px;text-align:left;font-size:11px;color:#b91c1c;">商品</th>
                <th style="padding:6px 8px;text-align:right;font-size:11px;color:#b91c1c;">活动价</th>
                <th style="padding:6px 8px;text-align:right;font-size:11px;color:#b91c1c;">限制价</th>
              </tr></thead>
              <tbody>${sample}</tbody>
            </table>
          </div>` : ''}
        ${(failedRemovals.length || reviewPromos.length) ? `
          <div style="margin-top:10px;font-size:12px;color:#98a2b3;">
            ${failedRemovals.length ? `移除失败 ${failedRemovals.length} 条 · ` : ''}${reviewPromos.length ? `积分评价 ${reviewPromos.length} 个` : ''}
          </div>` : ''}`,
      footer: [
        { text: '稍后处理', class: 'btn-ghost' },
        { text: '查看详情', class: 'btn-primary', onClick: () => { Router.navigate('/promo-remove'); return true; } },
      ],
    });
  }

  async function checkAlerts() {
    try {
      const res = await API.get('/api/promotions/alerts');
      if (res && res.code === 200 && res.data && res.data.hasWarning) {
        showPromoPopup(res.data);
        return true;
      }
    } catch (e) { /* 静默：登录后提醒失败不影响主流程 */ }
    return false;
  }

  // ======================= 生命周期 =======================

  let _booted = false;

  async function init() {
    if (!_booted) {
      _booted = true;
      await loadOverview();
      paint();
    } else {
      paint();
    }
    // 涂总的策略：检测到就自动移除。进页面先静默扫一次，命中则自动处理（受开关与上限约束）
    if (autoRemoveEnabled()) {
      const data = await scan(true);
      if (data) {
        paint();
        const hits = data.hits || [];
        if (hits.length && hits.length <= AUTO_REMOVE_LIMIT) {
          await autoRemoveNow(hits, 'enter');
        } else if (hits.length > AUTO_REMOVE_LIMIT) {
          Toast?.show?.(`命中 ${hits.length} 个，超过自动处理上限 ${AUTO_REMOVE_LIMIT} 个，请人工确认后手动移除`, 'warning');
        }
      }
    }
  }

  return {
    render, init, refresh,
    scanNow, dryRun,
    removeAll, removeFiltered, removeOne,
    switchTab, filterStore, filterMode,
    toggleAuto, loadLogs, loadActions,
    checkAlerts, showPromoPopup,
  };
})();

window.PromoRemovePage = PromoRemovePage;

Router.register('/promo-remove', () => {
  const html = PromoRemovePage.render();
  requestAnimationFrame(() => { PromoRemovePage.init(); });
  return html;
});

// ======================= 登录后 / 首屏 弹窗提醒 =======================
// 与 ad-monitor.js 的 bindAdSpendAlert() 同一模式；每次会话只提醒一次。
(function bindPromoAlert() {
  let checked = false;
  function run() {
    if (checked) return;
    const token = localStorage.getItem('geekozon_token') || localStorage.getItem('erp_token');
    if (!token) return;
    checked = true;
    PromoRemovePage.checkAlerts();
  }
  try {
    if (typeof Store !== 'undefined' && Store && typeof Store.subscribe === 'function') {
      Store.subscribe(() => setTimeout(run, 1200));
    }
  } catch (e) { /* noop */ }
  document.addEventListener('DOMContentLoaded', () => setTimeout(run, 1500));
  setTimeout(run, 2500);
})();
