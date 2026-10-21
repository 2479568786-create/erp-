/** 只读 Ozon Seller 商品表现分析 */
const sellerInsightsMetrics = [
  { key: 'session_view_pdp', label: '商品访客会话数' },
  { key: 'hits_view_pdp', label: '商品卡片浏览次数' },
  { key: 'hits_tocart_pdp', label: '卡片加购次数' },
  { key: 'conv_tocart_pdp', label: '卡片加购转化率', percent: true },
  { key: 'ordered_units', label: '下单商品数' },
  { key: 'revenue', label: '下单金额', currency: true },
];
const sellerInsightsState = {
  storeId: '',
  dateFrom: '',
  dateTo: '',
  dimension: 'sku',
  metrics: sellerInsightsMetrics.map((item) => item.key),
  result: null,
  busy: false,
};

function sellerInsightsDate(days) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - days + 1);
  const iso = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  sellerInsightsState.dateFrom = iso(start);
  sellerInsightsState.dateTo = iso(end);
}

function renderSellerInsightsPage() {
  sellerInsightsDate(14);
  return `
    <div class="online-page-header">
      <div><h2 class="online-page-title">Ozon 商品数据分析</h2>
        <p class="online-page-desc">只读调用 Seller API，查看商品卡浏览、加购、转化和订单表现；凭证沿用 ERP 店铺配置。</p></div>
    </div>
    <div class="card" style="padding:18px;margin-bottom:16px;">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;align-items:end;">
        <label class="form-group"><span>店铺</span><select id="sellerInsightStore" class="form-select"><option value="">加载店铺中…</option></select></label>
        <label class="form-group"><span>开始日期</span><input id="sellerInsightFrom" class="form-input" type="date" value="${sellerInsightsState.dateFrom}"></label>
        <label class="form-group"><span>结束日期</span><input id="sellerInsightTo" class="form-input" type="date" value="${sellerInsightsState.dateTo}"></label>
        <label class="form-group"><span>汇总维度</span><select id="sellerInsightDimension" class="form-select"><option value="sku">按 SKU 汇总</option><option value="sku_day">按 SKU + 日期</option></select></label>
        <label class="form-group"><span>最多行数</span><select id="sellerInsightLimit" class="form-select"><option>100</option><option selected>500</option><option>1000</option></select></label>
        <button id="sellerInsightRun" class="btn btn-primary" onclick="loadSellerInsights()"><i data-lucide="search"></i> 查询数据</button>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center;">
        <span style="font-size:12px;color:var(--text-secondary);">统计指标</span>
        ${sellerInsightsMetrics.map((metric) => `<label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--text-secondary);"><input type="checkbox" class="seller-insight-metric" value="${metric.key}" checked> ${metric.label}</label>`).join('')}
      </div>
      <div style="margin-top:12px;font-size:12px;color:var(--text-tertiary);">提示：Ozon 分析接口每个店铺每分钟最多请求一次。部分指标可能受 Ozon 账号权限限制；页面不缓存或改写接口数据。</div>
    </div>
    <div id="sellerInsightResults"><div class="card" style="padding:42px;text-align:center;color:var(--text-tertiary);">选择店铺和时间范围后查询。</div></div>`;
}

async function initSellerInsightsPage() {
  const select = document.getElementById('sellerInsightStore');
  try {
    const response = await API.get('/api/stores');
    const stores = response?.data?.list || [];
    const usable = stores.filter((store) => store.hasApiKey && store.authStatus === 'active');
    select.innerHTML = usable.length
      ? usable.map((store) => `<option value="${store.id}">${escapeHtml(store.alias || store.store_id)}</option>`).join('')
      : '<option value="">没有已配置 Seller API 的店铺</option>';
    if (sellerInsightsState.storeId && usable.some((store) => String(store.id) === String(sellerInsightsState.storeId))) select.value = sellerInsightsState.storeId;
    else sellerInsightsState.storeId = select.value;
  } catch (_) {
    select.innerHTML = '<option value="">店铺列表加载失败</option>';
  }
  select.addEventListener('change', () => { sellerInsightsState.storeId = select.value; });
  document.getElementById('sellerInsightDimension').value = sellerInsightsState.dimension;
  document.getElementById('sellerInsightDimension').addEventListener('change', (event) => { sellerInsightsState.dimension = event.target.value; });
  if (window.lucide) lucide.createIcons();
}

function sellerInsightNumber(value, metric) {
  const number = Number(value || 0);
  if (metric.percent) return `${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`;
  if (metric.currency) return `₽${number.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}`;
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function renderSellerInsightResults(data) {
  const metrics = data.metrics.map((key) => sellerInsightsMetrics.find((metric) => metric.key === key)).filter(Boolean);
  const totals = metrics.map((metric, index) => `<div class="card" style="padding:16px;"><div style="font-size:12px;color:var(--text-tertiary);">${metric.label}</div><div style="font-size:24px;font-weight:700;margin-top:8px;">${sellerInsightNumber(data.totals[index], metric)}</div></div>`).join('');
  const rows = data.rows.map((row) => {
    const dimensions = row.dimensions || [];
    const sku = dimensions.find((item) => /^\d+$/.test(String(item.id || '')))?.id || dimensions[0]?.id || '-';
    const product = data.products?.[String(sku)];
    const title = product?.title || dimensions.map((item) => item.name || item.id).join(' · ') || sku;
    const image = product?.image ? `<img src="${escapeHtml(product.image)}" alt="" style="width:36px;height:36px;object-fit:cover;border-radius:5px;vertical-align:middle;margin-right:8px;">` : '';
    const displayedTitle = product?.titleZh && product.titleZh !== title
      ? `<span>${escapeHtml(product.titleZh)}</span><small style="display:block;color:var(--text-tertiary);margin:3px 0 0 44px;">${escapeHtml(title)}</small>`
      : escapeHtml(title);
    return `<tr><td>${image}${displayedTitle}</td><td>${escapeHtml(sku)}</td>${metrics.map((metric, index) => `<td>${sellerInsightNumber(row.metrics?.[index], metric)}</td>`).join('')}</tr>`;
  }).join('');
  return `
    <div style="font-size:12px;color:var(--text-tertiary);margin:0 0 10px;">店铺：${escapeHtml(data.store)}　·　${data.dateFrom} 至 ${data.dateTo}　·　${data.rows.length} 行 · Ozon 实时返回</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin-bottom:16px;">${totals}</div>
    <div class="card" style="padding:0;overflow:auto;"><table class="table" style="width:100%;min-width:750px;"><thead><tr><th>商品 / 日期</th><th>SKU</th>${metrics.map((metric) => `<th>${metric.label}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="${metrics.length + 2}" style="text-align:center;padding:36px;color:var(--text-tertiary);">该时间段 Ozon 没有返回明细</td></tr>`}</tbody></table></div>`;
}

async function loadSellerInsights() {
  const results = document.getElementById('sellerInsightResults');
  const button = document.getElementById('sellerInsightRun');
  if (!results || sellerInsightsState.busy) return;
  const metrics = [...document.querySelectorAll('.seller-insight-metric:checked')].map((input) => input.value);
  if (!sellerInsightsState.storeId) { results.innerHTML = '<div class="card" style="padding:32px;color:var(--danger);">请先在店铺管理配置并选择 Seller API 店铺。</div>'; return; }
  if (!metrics.length) { results.innerHTML = '<div class="card" style="padding:32px;color:var(--danger);">至少选择一个指标。</div>'; return; }
  sellerInsightsState.busy = true;
  button.disabled = true;
  button.innerHTML = '查询中…';
  results.innerHTML = '<div class="card" style="padding:42px;text-align:center;color:var(--text-tertiary);">正在读取 Ozon 数据…</div>';
  const response = await API.post('/api/seller-insights', {
    storeId: sellerInsightsState.storeId,
    dateFrom: document.getElementById('sellerInsightFrom').value,
    dateTo: document.getElementById('sellerInsightTo').value,
    dimension: document.getElementById('sellerInsightDimension').value,
    limit: Number(document.getElementById('sellerInsightLimit').value),
    metrics,
  }, { timeout: 240000 });
  sellerInsightsState.busy = false;
  button.disabled = false;
  button.innerHTML = '<i data-lucide="search"></i> 查询数据';
  if (response?.code === 200 && response.data) {
    results.innerHTML = renderSellerInsightResults(response.data);
  } else {
    results.innerHTML = `<div class="card" style="padding:32px;color:var(--danger);">查询失败：${escapeHtml(response?.msg || '网络错误')}</div>`;
  }
  if (window.lucide) lucide.createIcons();
}

Router.register('/seller-insights', () => {
  const html = renderSellerInsightsPage();
  setTimeout(initSellerInsightsPage, 50);
  return html;
});
