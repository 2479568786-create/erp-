/**
 * AI 询盘助手页 - Insights Page
 * 从老 ERP 迁移：读订单+店铺+商品+上架记录，生成经营诊断与 AI 建议
 */
let insightsState = { days: 30, storeId: '', data: null, loading: false };

function renderInsightsPage() {
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">AI 询盘助手</h2>
          <p class="online-page-desc">用可追溯的订单、店铺、商品与上架记录生成经营诊断（数据源不含搜索分析与财务利润）</p>
        </div>
      </div>

      <div class="batch-action-bar">
        <div class="batch-action-left">
          <select id="insightStoreSelect" class="form-select" style="width:auto;height:34px;font-size:13px;"
                  onchange="insightsState.storeId=this.value;loadInsights()">
            <option value="">全部店铺</option>
          </select>
          <label style="font-size:13px;color:var(--text-secondary);margin-left:8px;">
            统计周期
            <select id="insightDays" class="form-select" style="width:auto;height:34px;font-size:13px;display:inline-block;"
                    onchange="insightsState.days=Number(this.value);loadInsights()">
              <option value="7">近 7 天</option>
              <option value="30" selected>近 30 天</option>
              <option value="90">近 90 天</option>
            </select>
          </label>
        </div>
        <div class="batch-action-right">
          <button class="btn btn-xs btn-primary" onclick="loadInsights()">
            <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 重新诊断
          </button>
        </div>
      </div>

      <div id="insightBody">
        <div class="card" style="text-align:center;padding:60px;color:var(--text-tertiary);">诊断中...</div>
      </div>
    </div>
  `;
}

async function initInsightsPage() {
  try {
    const resp = await API.get('/api/stores');
    const list = (resp && resp.data && (Array.isArray(resp.data) ? resp.data : resp.data.list)) || [];
    const sel = document.getElementById('insightStoreSelect');
    if (sel) {
      sel.innerHTML = '<option value="">全部店铺</option>' +
        list.map(s => `<option value="${s.store_id}">${s.alias || s.store_id}</option>`).join('');
      sel.value = insightsState.storeId || '';
    }
  } catch (e) { /* ignore */ }
  await loadInsights();
}

async function loadInsights() {
  const body = document.getElementById('insightBody');
  if (!body) return;
  insightsState.loading = true;
  body.innerHTML = '<div class="card" style="text-align:center;padding:60px;color:var(--text-tertiary);">AI 诊断中，请稍候（最长 30 秒）...</div>';
  const resp = await API.get('/api/ai-insights', {
    days: insightsState.days, storeId: insightsState.storeId,
  });
  insightsState.loading = false;
  if (resp && resp.code === 200 && resp.data) {
    insightsState.data = resp.data;
    renderInsights(resp.data);
  } else {
    body.innerHTML = `<div class="card" style="text-align:center;padding:40px;color:var(--danger);">诊断失败：${resp?.msg || '后端未连接'}</div>`;
  }
}

function _money(v) {
  return '₽' + Number(v || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function renderInsights(d) {
  const body = document.getElementById('insightBody');
  if (!body) return;
  const s = d.summary || {};

  const kpis = [
    { label: '订单数', value: s.orders ?? '-' },
    { label: '销量(件)', value: s.units ?? '-' },
    { label: '销售额', value: s.sales != null ? _money(s.sales) : '-' },
    { label: '取消率', value: s.cancel_rate != null ? s.cancel_rate + '%' : '-' },
    { label: '上架成功', value: s.published_products ?? '-' },
    { label: '采集商品', value: s.collected_products ?? '-' },
  ];

  const recColors = { '高': '#dc2626', '中': '#d97706', '低': '#2563eb' };
  const recs = (d.recommendations || []).map(r => `
    <div style="border-left:3px solid ${recColors[r.priority] || '#6b7280'};padding:10px 14px;margin-bottom:10px;background:var(--bg-secondary);border-radius:0 8px 8px 0;">
      <div style="font-weight:700;font-size:13px;">
        <span style="color:${recColors[r.priority] || '#6b7280'};">[${r.priority}]</span> ${r.title}
      </div>
      <div style="font-size:13px;margin-top:4px;color:var(--text-secondary);">${r.body}</div>
      <div style="font-size:12px;margin-top:4px;color:var(--text-tertiary);">依据：${r.evidence}</div>
    </div>`).join('');

  const topProducts = (d.products || []).slice(0, 10);
  const productRows = topProducts.map((p, i) => `
    <tr>
      <td>${i + 1}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${p.label}">${p.label}</td>
      <td>${p.orders}</td>
      <td>${p.units}</td>
      <td style="font-weight:600;">${_money(p.sales)}</td>
      <td>${p.cancellations || 0}</td>
    </tr>`).join('');

  const catRows = (d.categories || []).slice(0, 8).map(c => `
    <tr>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${c.label}">${c.label}</td>
      <td>${c.orders}</td>
      <td style="font-weight:600;">${_money(c.sales)}</td>
    </tr>`).join('');

  const storeRows = (d.stores || []).map(st => `
    <tr>
      <td>${st.label}</td>
      <td>${st.orders}</td>
      <td>${st.units}</td>
      <td style="font-weight:600;">${_money(st.sales)}</td>
    </tr>`).join('');

  body.innerHTML = `
    <div class="card" style="margin-bottom:14px;">
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px;">
        ${kpis.map(k => `
          <div style="text-align:center;padding:12px 6px;background:var(--bg-secondary);border-radius:10px;">
            <div style="font-size:20px;font-weight:800;">${k.value}</div>
            <div style="font-size:12px;color:var(--text-tertiary);margin-top:2px;">${k.label}</div>
          </div>`).join('')}
      </div>
      <div style="font-size:12px;color:var(--text-tertiary);margin-top:10px;">
        统计周期：近 ${d.period_days} 天 · 数据源：${d.data_note}
        ${d.ai_used ? ' · <span style="color:#16a34a;">AI 建议已启用</span>' : ' · 未配置 AI Key，当前为规则建议'}
      </div>
    </div>

    <div class="card" style="margin-bottom:14px;">
      <div style="font-weight:700;margin-bottom:10px;">经营建议</div>
      ${recs || '<div style="color:var(--text-tertiary);font-size:13px;">暂无建议</div>'}
    </div>

    ${topProducts.length ? `
    <div class="card" style="margin-bottom:14px;padding:0;overflow:hidden;">
      <div style="font-weight:700;padding:14px 16px 6px;">热销商品 TOP10</div>
      <table class="table" style="width:100%;font-size:13px;">
        <thead><tr><th>#</th><th>商品</th><th>订单</th><th>件数</th><th>销售额</th><th>取消</th></tr></thead>
        <tbody>${productRows}</tbody>
      </table>
    </div>` : ''}

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
      ${catRows ? `
      <div class="card" style="padding:0;overflow:hidden;">
        <div style="font-weight:700;padding:14px 16px 6px;">类目表现</div>
        <table class="table" style="width:100%;font-size:13px;">
          <thead><tr><th>类目</th><th>订单</th><th>销售额</th></tr></thead>
          <tbody>${catRows}</tbody>
        </table>
      </div>` : ''}
      ${storeRows ? `
      <div class="card" style="padding:0;overflow:hidden;">
        <div style="font-weight:700;padding:14px 16px 6px;">店铺表现</div>
        <table class="table" style="width:100%;font-size:13px;">
          <thead><tr><th>店铺</th><th>订单</th><th>件数</th><th>销售额</th></tr></thead>
          <tbody>${storeRows}</tbody>
        </table>
      </div>` : ''}
    </div>
  `;
  if (window.lucide) lucide.createIcons();
}

// 路由自注册
Router.register('/insights', (route) => {
  const html = renderInsightsPage();
  setTimeout(() => initInsightsPage(), 50);
  return html;
});
