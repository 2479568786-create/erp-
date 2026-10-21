/**
 * AI 广告监控 - Ad Monitor Page
 *
 * 【模块职责】
 * 业务员在 Ozon 后台自己开广告（登上顶端 / 按点击付费 / 按订单付费…），
 * ERP 不代投，只负责把效果盯住。本页做两件事：
 *
 *   1. 检测商品级问题
 *      · 已完成测评：累计点击 ≥ 30，可据此判断投放效果
 *      · 点击率偏低：曝光 ≥ 100 且 CTR < 4%
 *      · 高花费无转化：花费 > 1000₽ 且零订单
 *
 *   2. 盯住每日花费
 *      · 单店当日（莫斯科时间）广告总花费 ≥ 500₽ → 弹窗警告业务员
 *        弹窗在登录后立即触发，进入本页时再提示一次（见文件末尾的自启动块）
 *
 * 【数据来源 / 权限】
 *   数据来自 Ozon Performance API（独立于商品 Seller API 的另一套凭证）。
 *   业务员仅能看到自己名下店铺；管理员可看全部、可同步、可调阈值、可配凭证。
 *
 * 【时区】所有「当天」均按莫斯科时间（MSK, UTC+3）计算，与 Ozon 报表口径一致。
 */
const AdMonitorPage = (() => {
  const state = {
    data: null,        // overview 响应
    tab: 'alerts',
    typeFilter: '',
    loading: false,
  };

  const ALERT_META = {
    test_complete: { label: '已完成测评', color: '#2563eb', icon: 'check-circle-2', tone: 'info' },
    low_ctr: { label: '点击率偏低', color: '#d97706', icon: 'trending-down', tone: 'warning' },
    no_conversion: { label: '高花费无转化', color: '#dc2626', icon: 'alert-triangle', tone: 'danger' },
    daily_spend: { label: '当日花费超线', color: '#dc2626', icon: 'wallet', tone: 'danger' },
  };

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function money(v) {
    const n = Number(v || 0);
    return n.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ₽';
  }

  function pct(v) {
    return (Number(v || 0) * 100).toFixed(2) + '%';
  }

  function isAdmin() {
    return Store?.getUser()?.role === 'admin';
  }

  // ======================= 数据 =======================

  async function load() {
    const res = await API.get('/api/ad-monitor/overview');
    if (res && res.code === 200) {
      state.data = res.data;
      return res.data;
    }
    Toast.show((res && res.msg) || '加载广告监控数据失败', 'error');
    return null;
  }

  async function doSync(storePk) {
    if (state.loading) return;
    state.loading = true;
    const btn = document.getElementById('amSyncBtn');
    if (btn) { btn.disabled = true; btn.textContent = '同步中…'; }
    try {
      const res = await API.post('/api/ad-monitor/sync', storePk ? { storePk } : {});
      if (res && res.code === 200) {
        const d = res.data || {};
        const okCount = (d.synced || []).length;
        const errCount = (d.errors || []).length;
        if (okCount === 0 && errCount === 0) {
          Toast.show(d.message || '没有可同步的店铺', 'warning');
        } else {
          Toast.show(res.msg || `同步完成：成功 ${okCount} 家，失败 ${errCount} 家`,
                     errCount ? 'warning' : 'success');
        }
        if (errCount) {
          const first = d.errors[0];
          setTimeout(() => {
            Modal.alert('部分店铺同步失败：\n\n' + d.errors.map(e => `· 店铺#${e.storePk}：${e.message}`).join('\n'),
                        '同步异常');
          }, 400);
        }
      } else {
        Toast.show((res && res.msg) || '同步失败', 'error');
      }
    } finally {
      state.loading = false;
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步广告数据';
        if (window.lucide) lucide.createIcons();
      }
      await refresh();
    }
  }

  // ======================= 页面骨架 =======================

  function render() {
    return `
      <div style="animation: pageEnter .35s ease;">
        <div class="store-page-header">
          <div>
            <h2 class="store-page-title">AI 广告监控</h2>
            <p class="store-page-desc">
              盯住业务员自行投放的 Ozon 广告（登上顶端 / 按点击付费 / 按订单付费）。
              自动标出已完成测评、点击率偏低、高花费无转化的商品，并在单店当日花费超线时弹窗告警。
              <span style="color:#98a2b3;">统计口径：莫斯科时间(MSK)</span>
            </p>
          </div>
          <div class="store-header-actions">
            <button class="btn btn-sm btn-ghost" onclick="AdMonitorPage.refresh()">
              <i data-lucide="rotate-cw" style="width:14px;height:14px;"></i> 刷新
            </button>
            ${isAdmin() ? `
            <button class="btn btn-sm btn-ghost" onclick="AdMonitorPage.openSettings()">
              <i data-lucide="sliders-horizontal" style="width:14px;height:14px;"></i> 阈值设置
            </button>` : ''}
            <button class="btn btn-sm btn-primary" id="amSyncBtn" onclick="AdMonitorPage.sync()">
              <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 同步广告数据
            </button>
          </div>
        </div>
        <div id="amBanner"></div>
        <div id="amBody"><div style="padding:40px;text-align:center;color:#98a2b3;">加载中…</div></div>
      </div>`;
  }

  function renderBanner(d) {
    const alerts = state.dailyAlerts;
    const pending = (d.totals && d.totals.storesPending) || 0;
    let html = '';
    if (alerts && alerts.length) {
      html += `
        <div style="margin:14px 0;padding:14px 16px;border:1px solid #fecaca;background:#fef2f2;
                    border-radius:12px;display:flex;gap:12px;align-items:flex-start;">
          <i data-lucide="alert-triangle" style="width:20px;height:20px;color:#dc2626;flex:none;margin-top:1px;"></i>
          <div style="flex:1;">
            <div style="font-weight:600;color:#b91c1c;margin-bottom:6px;">
              广告花费告警：${alerts.length} 家店铺今日花费已达告警线</div>
            ${alerts.map(a => `<div style="font-size:13px;color:#7f1d1d;margin-top:3px;">${esc(a.message)}</div>`).join('')}
          </div>
        </div>`;
    }
    if (pending > 0) {
      html += `
        <div style="margin:14px 0;padding:12px 16px;border:1px solid #fde68a;background:#fffbeb;
                    border-radius:12px;display:flex;gap:10px;align-items:center;">
          <i data-lucide="key-round" style="width:18px;height:18px;color:#d97706;flex:none;"></i>
          <div style="flex:1;font-size:13px;color:#92400e;">
            有 <strong>${pending}</strong> 家店铺尚未配置 Ozon 广告 API 凭证，无法拉取广告数据。
            ${isAdmin() ? '请到下方「授权状态」填写 client_id / client_secret。' : '请联系管理员配置。'}
          </div>
          ${isAdmin() ? `<button class="btn btn-xs btn-primary"
             onclick="AdMonitorPage.switchTab('stores')">去配置</button>` : ''}
        </div>`;
    }
    return html;
  }

  function renderStats(d) {
    const t = d.totals || {};
    const card = (label, value, hint, color) => `
      <div style="flex:1 1 150px;min-width:140px;padding:14px 16px;border:1px solid #e4e7ec;
                  border-radius:12px;background:#fff;">
        <div style="font-size:12px;color:#667085;margin-bottom:6px;">${esc(label)}</div>
        <div style="font-size:22px;font-weight:600;color:${color || '#101828'};line-height:1.2;">${value}</div>
        ${hint ? `<div style="font-size:11px;color:#98a2b3;margin-top:4px;">${esc(hint)}</div>` : ''}
      </div>`;
    return `
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;">
        ${card('今日广告总花费', money(t.todaySpend), '莫斯科时间 ' + esc(d.statDate),
                (state.dailyAlerts && state.dailyAlerts.length) ? '#dc2626' : '#101828')}
        ${card('今日订单', t.todayOrders || 0, '广告带来的订单数')}
        ${card('今日点击', t.todayClicks || 0, '全部店铺合计')}
        ${card('告警店铺', t.warningStores || 0, '当日花费超线', (t.warningStores ? '#dc2626' : '#16a34a'))}
        ${card('监控店铺', `${t.storesConfigured || 0}/${t.stores || 0}`, '已配凭证 / 全部')}
      </div>`;
  }

  function renderTabs() {
    const d = state.data || {};
    const b = d.buckets || {};
    const counts = {
      alerts: (b.noConversion || []).length + (b.lowCtr || []).length + (b.testComplete || []).length,
      spend: (d.dailySpend || []).length,
      campaigns: 0,
      stores: (d.stores || []).length,
    };
    const tabs = [
      ['alerts', '商品告警'],
      ['spend', '当日花费'],
      ['campaigns', '广告活动'],
      ['stores', '授权状态'],
    ];
    return `
      <div style="display:flex;gap:4px;border-bottom:1px solid #e4e7ec;margin-bottom:16px;">
        ${tabs.map(([k, label]) => {
          const active = state.tab === k;
          const n = counts[k];
          return `<button onclick="AdMonitorPage.switchTab('${k}')"
            style="padding:9px 14px;font-size:14px;background:none;border:none;cursor:pointer;
                   color:${active ? '#2563eb' : '#667085'};font-weight:${active ? 600 : 400};
                   border-bottom:2px solid ${active ? '#2563eb' : 'transparent'};margin-bottom:-1px;">
            ${esc(label)}${n ? ` <span style="color:#98a2b3;font-size:12px;">${n}</span>` : ''}
          </button>`;
        }).join('')}
      </div>`;
  }

  function renderBody() {
    switch (state.tab) {
      case 'spend': return renderSpend();
      case 'campaigns': return renderCampaigns();
      case 'stores': return renderStores();
      case 'alerts':
      default: return renderAlerts();
    }
  }

  // ======================= Tab 1：商品告警 =======================

  function renderAlerts() {
    const d = state.data || {};
    const b = d.buckets || {};
    if (!d.products || !d.products.length) {
      const t = d.totals || {};
      const pending = t.storesPending || 0;
      const configured = t.storesConfigured || 0;
      // 三种「没数据」的原因要分清楚，否则用户不知道下一步该做什么
      let why;
      if (pending && !configured) {
        why = '尚未配置 Ozon 广告 API 凭证，无法拉取广告数据。请到「授权状态」填写 client_id / client_secret。';
      } else if (!(t.metricsCount)) {
        why = '已按凭证拉取，但统计区间内该店铺在 Ozon 侧<strong>没有任何广告投放数据</strong>'
            + '（未开广告、广告已停止，或期间无曝光/点击/花费）。这属于正常状态，不是故障。';
      } else {
        why = '暂无可用于判断的商品投放明细。';
      }
      return `<div style="padding:36px 24px;text-align:center;color:#667085;max-width:640px;margin:0 auto;">
        <i data-lucide="inbox" style="width:32px;height:32px;color:#d0d5dd;"></i>
        <div style="margin-top:10px;font-size:14px;line-height:1.9;">${why}</div>
        <div style="margin-top:14px;font-size:12px;color:#98a2b3;">
          统计区间：${esc((d.settings || {}).skuWindowDays || 30)} 天 ·
          口径：莫斯科时间(MSK) · 数据更新需点击右上角「同步广告数据」
        </div>
      </div>`;
    }
    const groups = [
      ['no_conversion', b.noConversion || []],
      ['low_ctr', b.lowCtr || []],
      ['test_complete', b.testComplete || []],
    ];
    return groups.map(([key, items]) => {
      if (state.typeFilter && state.typeFilter !== key) return '';
      const meta = ALERT_META[key];
      const rows = items.length ? items.map(p => `
        <tr>
          <td>
            <strong>${esc(p.title)}</strong>
            <div style="font-size:11px;color:#98a2b3;font-family:ui-monospace,monospace;">SKU ${esc(p.sku)}</div>
          </td>
          <td style="font-size:12px;color:#475467;">${esc(p.storeName)}</td>
          <td style="text-align:right;">${p.clicks}</td>
          <td style="text-align:right;">${p.views}</td>
          <td style="text-align:right;color:${p.ctr < ((d.settings || {}).lowCtrThreshold || 0.04) ? '#d97706' : '#16a34a'};
                     font-weight:600;">${pct(p.ctr)}</td>
          <td style="text-align:right;">${money(p.expense)}</td>
          <td style="text-align:right;">${p.orders}</td>
          <td style="font-size:12px;color:#667085;">${esc(p.message)}</td>
        </tr>`).join('')
        : `<tr><td colspan="8" style="text-align:center;padding:20px;color:#98a2b3;">无</td></tr>`;
      return `
        <div style="margin-bottom:22px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <i data-lucide="${meta.icon}" style="width:16px;height:16px;color:${meta.color};"></i>
            <strong style="font-size:14px;color:${meta.color};">${esc(meta.label)}</strong>
            <span style="font-size:12px;color:#98a2b3;">${items.length} 个商品</span>
          </div>
          <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
            <table class="collect-table" style="width:100%;font-size:13px;">
              <thead><tr>
                <th style="text-align:left;">商品</th>
                <th style="text-align:left;">店铺</th>
                <th style="text-align:right;">点击</th>
                <th style="text-align:right;">曝光</th>
                <th style="text-align:right;">点击率</th>
                <th style="text-align:right;">花费</th>
                <th style="text-align:right;">订单</th>
                <th style="text-align:left;">说明</th>
              </tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        </div>`;
    }).join('');
  }

  // ======================= Tab 2：当日花费 =======================

  function renderSpend() {
    const d = state.data || {};
    const list = d.dailySpend || [];
    const threshold = (d.settings || {}).dailySpendThreshold || 500;
    if (!list.length) {
      return `<div style="padding:40px;text-align:center;color:#98a2b3;">
        暂无当日花费数据（${esc(d.statDate)}）。</div>`;
    }
    const rows = list.map(s => `
      <tr style="${s.exceeded ? 'background:#fef2f2;' : ''}">
        <td>${esc(s.storeName)}</td>
        <td style="font-family:ui-monospace,monospace;font-size:12px;">${esc(s.storeId)}</td>
        <td style="text-align:right;font-weight:600;color:${s.exceeded ? '#dc2626' : '#101828'};">
          ${money(s.expense)}</td>
        <td style="text-align:right;">${s.clicks}</td>
        <td style="text-align:right;">${s.orders}</td>
        <td style="text-align:right;">${money(s.ordersAmount)}</td>
        <td style="text-align:right;">${money(s.threshold)}</td>
        <td>${s.exceeded
          ? `<span style="color:#dc2626;font-weight:600;">超线 ${money(s.overBy)}</span>`
          : '<span style="color:#16a34a;">正常</span>'}</td>
      </tr>`).join('');
    return `
      <p style="font-size:13px;color:#667085;margin-bottom:10px;">
        统计日期：<strong>${esc(d.statDate)}</strong>（莫斯科时间）。
        单店当日花费达到 <strong>${money(threshold)}</strong> 即触发弹窗告警。
      </p>
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:left;">店铺ID</th>
            <th style="text-align:right;">当日花费</th>
            <th style="text-align:right;">点击</th>
            <th style="text-align:right;">订单</th>
            <th style="text-align:right;">订单金额</th>
            <th style="text-align:right;">告警线</th>
            <th style="text-align:left;">状态</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  // ======================= Tab 3：广告活动 =======================

  async function loadCampaigns() {
    const res = await API.get('/api/ad-monitor/campaigns');
    return (res && res.code === 200) ? (res.data.list || []) : [];
  }

  function renderCampaigns() {
    return '<div id="amCampaignBox" style="color:#98a2b3;padding:20px;text-align:center;">加载中…</div>';
  }

  async function paintCampaigns() {
    const box = document.getElementById('amCampaignBox');
    if (!box) return;
    const list = await loadCampaigns();
    if (!list.length) {
      box.innerHTML = `<div style="padding:40px;text-align:center;color:#98a2b3;">
        暂无已同步的广告活动。点击「同步广告数据」拉取。</div>`;
      return;
    }
    const stateText = {
      CAMPAIGN_STATE_RUNNING: '投放中', CAMPAIGN_STATE_STOPPED: '已停止',
      CAMPAIGN_STATE_PAUSED: '已暂停', CAMPAIGN_STATE_ARCHIVED: '已归档',
      CAMPAIGN_STATE_FINISHED: '已结束', CAMPAIGN_STATE_PLANNED: '待开始',
    };
    box.innerHTML = `
      <div style="overflow:auto;border:1px solid #e4e7ec;border-radius:12px;background:#fff;max-height:600px;">
        <table class="collect-table" style="width:100%;font-size:13px;">
          <thead><tr>
            <th style="text-align:left;">活动名称</th>
            <th style="text-align:left;">店铺</th>
            <th style="text-align:left;">付费方式</th>
            <th style="text-align:left;">展示位置</th>
            <th style="text-align:left;">状态</th>
            <th style="text-align:right;">日预算</th>
          </tr></thead>
          <tbody>
            ${list.map(c => `
              <tr>
                <td>${esc(c.title || c.campaignId)}</td>
                <td style="font-size:12px;color:#475467;">${esc(c.storeName)}</td>
                <td>${esc(c.paymentTypeText)}</td>
                <td>${esc(c.placementText)}</td>
                <td>${esc(stateText[c.state] || c.state)}</td>
                <td style="text-align:right;">${esc(c.dailyBudget || '-')}</td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  }

  // ======================= Tab 4：授权状态 =======================

  function renderStores() {
    const d = state.data || {};
    const list = d.stores || [];
    if (!list.length) {
      return '<div style="padding:40px;text-align:center;color:#98a2b3;">没有可监控的店铺。</div>';
    }
    const cards = list.map(s => `
      <div style="border:1px solid ${s.credentialsReady ? '#d1fae5' : '#fde68a'};border-radius:12px;
                  background:#fff;padding:16px;">
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;">
          <div>
            <div style="font-size:15px;font-weight:600;color:#101828;">${esc(s.storeName)}</div>
            <div style="font-size:12px;color:#98a2b3;font-family:ui-monospace,monospace;">${esc(s.storeId)}</div>
          </div>
          <span style="font-size:12px;padding:2px 8px;border-radius:10px;white-space:nowrap;
            background:${s.credentialsReady ? '#ecfdf5' : '#fffbeb'};
            color:${s.credentialsReady ? '#047857' : '#b45309'};">
            ${s.credentialsReady ? '已授权' : '未授权'}
          </span>
        </div>
        <div style="margin-top:10px;font-size:12px;color:#475467;line-height:1.7;">
          广告活动：<strong>${s.campaignCount}</strong> 个<br>
          Client ID：<span style="font-family:ui-monospace,monospace;">${esc(s.perfClientId || '（未填写）')}</span><br>
          最近同步：${esc(s.lastSync || '从未')}
          ${s.lastSyncStatus === 'error' ? `<span style="color:#dc2626;"> · 失败</span>` : ''}
        </div>
        ${s.lastSyncStatus === 'error' && s.lastSyncMessage
          ? `<div style="margin-top:8px;font-size:12px;color:#b91c1c;background:#fef2f2;padding:6px 8px;
               border-radius:6px;">${esc(s.lastSyncMessage)}</div>` : ''}
        ${isAdmin() ? `
        <div style="margin-top:12px;display:flex;gap:6px;flex-wrap:wrap;">
          <button class="btn btn-xs btn-primary" onclick="AdMonitorPage.openCredentials(${s.storePk})">
            ${s.credentialsReady ? '重新配置凭证' : '填写凭证'}</button>
          <button class="btn btn-xs" onclick="AdMonitorPage.testStore(${s.storePk})">连接自检</button>
          <button class="btn btn-xs" onclick="AdMonitorPage.sync(${s.storePk})">仅同步此店</button>
        </div>` : ''}
      </div>`).join('');
    return `
      <div style="margin-bottom:14px;padding:12px 14px;background:#f8fafc;border:1px solid #e2e8f0;
                  border-radius:10px;font-size:12px;color:#475467;line-height:1.8;">
        <strong>广告数据用的是另一套凭证</strong>：Ozon 把商品数据和广告数据拆成了两个 API。
        广告凭证在 <span style="font-family:ui-monospace,monospace;">seller.ozon.ru → 设置 → API-keys → Performance API</span>
        标签页申请，<code>client_id</code> 形如 <code>xxx@advertising.performance.ozon.ru</code>，
        和「店铺管理」里填的商品 API Key <strong>不是同一个东西</strong>。
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;">
        ${cards}
      </div>`;
  }

  // ======================= 凭证配置 / 阈值设置 =======================

  function openCredentials(storePk) {
    if (!isAdmin()) { Toast.show('仅管理员可配置凭证', 'warning'); return; }
    const s = (state.data?.stores || []).find(x => x.storePk === storePk);
    Modal.show({
      title: '配置广告 API 凭证 - ' + (s ? s.storeName : ('#' + storePk)),
      size: 'md',
      body: `
        <div style="font-size:13px;color:#475467;line-height:1.8;margin-bottom:14px;
                    background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;">
          1. 打开 <span style="font-family:ui-monospace,monospace;">seller.ozon.ru/app/settings/api-keys</span>，
             切到 <strong>Performance API</strong> 标签页<br>
          2. 创建/选择服务账号 → <strong>Добавить новый ключ</strong><br>
          3. 把 <code>client_id</code> 与 <code>client_secret</code> 填到下面
        </div>
        <div class="form-group">
          <label class="form-label">Client ID <span class="required">*</span></label>
          <input class="form-input" id="amClientId" placeholder="xxx@advertising.performance.ozon.ru"
                 value="${esc(s ? s.perfClientId : '')}" autocomplete="off">
        </div>
        <div class="form-group">
          <label class="form-label">Client Secret ${s && s.credentialsReady ? '（留空则不修改）' : '<span class="required">*</span>'}</label>
          <input type="password" class="form-input" id="amClientSecret"
                 placeholder="${s && s.credentialsReady ? '已保存，留空保持不变' : '粘贴 client_secret'}"
                 autocomplete="new-password">
        </div>
        <p style="font-size:12px;color:#98a2b3;margin:0;">
          密钥会加密后入库，接口不会回传明文。保存时会立即向 Ozon 验签。
        </p>`,
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
        { text: '保存并验证', class: 'btn-primary', onClick: () => submitCredentials(storePk) },
      ],
    });
  }

  async function submitCredentials(storePk) {
    const clientId = (document.getElementById('amClientId').value || '').trim();
    const clientSecret = (document.getElementById('amClientSecret').value || '').trim();
    if (!clientId && !clientSecret) { Toast.show('请填写凭证', 'warning'); return; }
    const res = await API.post(`/api/ad-monitor/stores/${storePk}/credentials`,
                               { clientId, clientSecret });
    if (res && res.code === 200) {
      const okSaved = !!(res.data && res.data.ok);
      Toast.show(res.msg || '已保存', okSaved ? 'success' : 'warning');
      await Modal.close();
      await refresh();
      if (!okSaved && res.data && res.data.error) {
        setTimeout(() => Modal.alert('凭证已保存，但与 Ozon 通信失败：\n\n' + res.data.error, '验证失败'), 300);
      }
    } else {
      Toast.show((res && res.msg) || '保存失败', 'error');
    }
  }

  async function testStore(storePk) {
    const res = await API.post(`/api/ad-monitor/stores/${storePk}/test`, {});
    if (res && res.code === 200) {
      const d = res.data || {};
      const lines = (d.sample || []).map(c =>
        `· ${c.title || c.id}（${c.paymentType || '-'} / ${c.state || '-'}）`).join('\n');
      Modal.alert(`连接正常 ✅\n\n共发现 ${d.campaignCount} 个广告活动。`
        + (lines ? '\n\n前几个活动：\n' + lines : ''), '连接自检');
    } else {
      Toast.show((res && res.msg) || '自检失败', 'error');
    }
  }

  function openSettings() {
    if (!isAdmin()) { Toast.show('仅管理员可修改阈值', 'warning'); return; }
    const c = (state.data && state.data.settings) || {};
    const lastInfo = c.updatedByName
      ? `最后修改：${esc(c.updatedByName)}${c.updatedAt ? ' · ' + esc(c.updatedAt) : ''}`
      : '尚未修改过（当前为系统默认值）';
    Modal.show({
      title: '广告监控阈值设置',
      size: 'md',
      body: `
        <div style="font-size:12px;color:#475467;background:#f8fafc;border:1px solid #e2e8f0;
                    border-radius:8px;padding:9px 11px;margin-bottom:14px;line-height:1.7;">
          🔒 <strong>仅管理员可修改</strong>这些阈值；其他账号只能查看（用于理解自己收到的告警线）。<br>
          ${lastInfo}
        </div>
        <div class="form-group">
          <label class="form-label">已完成测评：累计点击达到（次）</label>
          <input type="number" class="form-input" id="amTestClicks" min="1" value="${c.testClicksThreshold}">
        </div>
        <div class="form-group">
          <label class="form-label">点击率偏低：CTR 低于（%）</label>
          <input type="number" class="form-input" id="amLowCtr" step="0.1" min="0.1"
                 value="${((c.lowCtrThreshold || 0.04) * 100).toFixed(2)}">
        </div>
        <div class="form-group">
          <label class="form-label">点击率判定所需最低曝光量（次）</label>
          <input type="number" class="form-input" id="amMinViews" min="0" value="${c.lowCtrMinViews}">
          <div style="font-size:12px;color:#98a2b3;margin-top:4px;">
            曝光低于此值不参与点击率判定，避免「2 次曝光 0 点击」这类噪音刷屏。
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">高花费无转化：花费超过（₽）且零订单</label>
          <input type="number" class="form-input" id="amHighSpend" min="0" value="${c.highSpendThreshold}">
        </div>
        <div class="form-group">
          <label class="form-label">当日花费告警线：单店当日花费达到（₽）弹窗</label>
          <input type="number" class="form-input" id="amDailySpend" min="0" value="${c.dailySpendThreshold}">
        </div>`,
      footer: [
        { text: '取消', class: 'btn-ghost', onClick: () => Modal.close() },
        { text: '保存', class: 'btn-primary', onClick: () => submitSettings() },
      ],
    });
  }

  async function submitSettings() {
    const body = {
      testClicksThreshold: Number(document.getElementById('amTestClicks').value),
      lowCtrThreshold: Number(document.getElementById('amLowCtr').value),
      lowCtrMinViews: Number(document.getElementById('amMinViews').value),
      highSpendThreshold: Number(document.getElementById('amHighSpend').value),
      dailySpendThreshold: Number(document.getElementById('amDailySpend').value),
    };
    const res = await API.put('/api/ad-monitor/settings', body);
    if (res && res.code === 200) {
      await Modal.close();
      Toast.show(res.msg || '已保存', 'success');
      await refresh();
    } else {
      Toast.show((res && res.msg) || '保存失败', 'error');
    }
  }

  // ======================= 弹窗告警（登录后 / 进页时）=======================

  /** 拉取当日花费告警；返回超线店铺列表 */
  async function fetchDailyAlerts() {
    try {
      const res = await API.get('/api/ad-monitor/alerts');
      if (res && res.code === 200) {
        state.dailyAlerts = res.data.dailySpendAlerts || [];
        return res.data;
      }
    } catch (e) { /* 网络异常不打扰用户 */ }
    state.dailyAlerts = [];
    return null;
  }

  /** 弹出当日花费告警（每页面会话只弹一次） */
  async function showDailySpendPopup(force) {
    const data = await fetchDailyAlerts();
    if (!data || !data.hasDailyWarning) return;
    if (!force && AdMonitorPage._popupShown) return;
    AdMonitorPage._popupShown = true;

    const alerts = data.dailySpendAlerts;
    Modal.show({
      title: '⚠️ 广告花费告警',
      size: 'md',
      body: `
        <div style="font-size:14px;color:#7f1d1d;background:#fef2f2;border:1px solid #fecaca;
                    border-radius:10px;padding:12px 14px;margin-bottom:14px;">
          以下店铺今日（${esc(data.statDate)}，莫斯科时间）广告花费已达到告警线，请及时检查投放效果。
        </div>
        <div style="display:grid;gap:10px;">
          ${alerts.map(a => `
            <div style="border:1px solid #fecaca;border-radius:10px;padding:12px 14px;">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
                <strong style="font-size:15px;color:#101828;">${esc(a.storeName)}</strong>
                <span style="font-size:18px;font-weight:700;color:#dc2626;">${money(a.expense)}</span>
              </div>
              <div style="font-size:12px;color:#667085;margin-top:6px;">
                告警线 ${money(a.threshold)} · 超出 ${money(a.overBy)} · 今日订单 ${a.orders} 单
              </div>
            </div>`).join('')}
        </div>`,
      footer: [
        { text: '查看明细', class: 'btn-ghost', onClick: () => {
            Modal.close(); Router.navigate('/ad-monitor');
          } },
        { text: '我知道了', class: 'btn-primary', onClick: () => Modal.close() },
      ],
    });
  }

  // ======================= 对外 =======================

  async function refresh() {
    const d = await load();
    if (!d) return;
    await fetchDailyAlerts();
    const banner = document.getElementById('amBanner');
    if (banner) banner.innerHTML = renderBanner(d);
    const body = document.getElementById('amBody');
    if (body) {
      body.innerHTML = renderStats(d) + renderTabs() + renderBody();
    }
    if (state.tab === 'campaigns') await paintCampaigns();
    if (window.lucide) lucide.createIcons();
  }

  async function switchTab(tab) {
    state.tab = tab;
    const body = document.getElementById('amBody');
    if (body) body.innerHTML = renderStats(state.data || {}) + renderTabs() + renderBody();
    if (tab === 'campaigns') await paintCampaigns();
    if (window.lucide) lucide.createIcons();
  }

  async function init() {
    await refresh();
    // 进入本页时再提示一次当日花费告警（登录弹窗之外的第二道提醒）
    await showDailySpendPopup(true);
  }

  return {
    render, init, refresh, switchTab, sync: doSync,
    openSettings, openCredentials, testStore,
    showDailySpendPopup, fetchDailyAlerts,
    _popupShown: false,
  };
})();

window.AdMonitorPage = AdMonitorPage;

Router.register('/ad-monitor', () => {
  const html = AdMonitorPage.render();
  requestAnimationFrame(() => AdMonitorPage.init());
  return html;
});

/* ============================================================================
 * 登录后自动检查广告花费告警
 *
 * 需求：「当天花费 500 卢布及以上必须发出警告，通过弹窗的方式告诉业务员」。
 * 这里订阅 Store —— 登录态解析完成（LoginGate.ensure 是异步的）后触发一次检查。
 * 每页面会话只弹一次，避免刷新/切页反复打扰。
 * 注意：Store 是顶层 const，window.Store 不存在，必须直接引用标识符。
 * ========================================================================== */
(function bindAdSpendAlert() {
  let checked = false;
  async function check() {
    if (checked) return;
    let user = null;
    try { user = Store?.getUser?.() || null; } catch (e) { user = null; }
    if (!user || !user.id) return;   // 未登录不动
    checked = true;
    await AdMonitorPage.showDailySpendPopup(false);
  }
  try { if (typeof Store !== 'undefined' && Store.subscribe) Store.subscribe(check); } catch (e) { /* noop */ }
  document.addEventListener('DOMContentLoaded', () => setTimeout(check, 1500));
})();
