/** 只读 Ozon Seller 商品表现分析 */
const sellerInsightsMetrics = [
  { key: 'revenue', label: '已订购金额', currency: true },
  { key: 'hits_view_search', label: '搜索结果和目录中的展示次数' },
  { key: 'hits_view_pdp', label: '商品卡片访问量' },
  { key: 'hits_tocart_pdp', label: '从商品卡片添加至购物车' },
  { key: 'ordered_units', label: '已订购商品' },
];
const sellerInsightViews = [
  { id: 'analysis', label: '您的分析', metrics: sellerInsightsMetrics.map((item) => item.key) },
  { id: 'funnel', label: '销售漏斗', metrics: ['hits_view_search', 'hits_view_pdp', 'hits_tocart_pdp', 'ordered_units'] },
  { id: 'leaders', label: '您的销售领先产品', metrics: ['revenue', 'ordered_units'] },
  { id: 'search', label: '在搜索中的位置', metrics: ['hits_view_search', 'hits_view_pdp'] },
];
const sellerInsightsState = {
  storeId: '', dateFrom: '', dateTo: '', view: 'analysis', sortByView: {}, result: null, busy: false,
  dimension: 'sku',
};

function sellerInsightsIso(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function sellerInsightsDateRange(range) {
  const end = new Date();
  const start = new Date(end);
  if (range === 'yesterday') {
    start.setDate(start.getDate() - 1);
    end.setTime(start.getTime());
  }
  else if (range === 'week') start.setDate(start.getDate() - 6);
  else if (range === 'month') start.setDate(start.getDate() - 29);
  else if (range === 'quarter') start.setDate(start.getDate() - 89);
  else if (range === 'year') start.setFullYear(start.getFullYear() - 1);
  sellerInsightsState.dateFrom = sellerInsightsIso(start);
  sellerInsightsState.dateTo = sellerInsightsIso(end);
  const from = document.getElementById('sellerInsightFrom');
  const to = document.getElementById('sellerInsightTo');
  if (from) from.value = sellerInsightsState.dateFrom;
  if (to) to.value = sellerInsightsState.dateTo;
  document.querySelectorAll('.si-range').forEach((button) => button.classList.toggle('active', button.dataset.range === range));
}

function renderSellerInsightsPage() {
  sellerInsightsDateRange('today');
  return `
    <section class="si-page">
      <div class="si-topline"><h2>分析</h2><span class="si-source">Ozon Seller API</span></div>
      <div class="si-tabs" role="tablist">${sellerInsightViews.map((view) => `<button type="button" class="si-tab${view.id === sellerInsightsState.view ? ' active' : ''}" role="tab" aria-selected="${view.id === sellerInsightsState.view}" onclick="selectSellerInsightView('${view.id}')">${view.label}</button>`).join('')}</div>
      <div class="si-toolbar">
        <label class="si-control si-store"><span>切换店铺</span><select id="sellerInsightStore"><option value="">加载店铺中…</option></select></label>
        <label class="si-control"><span>显示方式</span><select id="sellerInsightDimension"><option value="sku"${sellerInsightsState.dimension === 'sku' ? ' selected' : ''}>按商品汇总（一行一个商品）</option><option value="sku_day"${sellerInsightsState.dimension === 'sku_day' ? ' selected' : ''}>按天拆分（一行一天）</option></select></label>
        <div class="si-date-presets" aria-label="日期范围">${[['today','今天'],['yesterday','昨天'],['week','周'],['month','月'],['quarter','季度'],['year','年']].map(([range,label]) => `<button type="button" class="si-range${range === 'today' ? ' active' : ''}" data-range="${range}" onclick="sellerInsightsDateRange('${range}')">${label}</button>`).join('')}</div>
        <label class="si-control"><span>开始日期</span><input id="sellerInsightFrom" type="date" value="${sellerInsightsState.dateFrom}"></label>
        <label class="si-control"><span>结束日期</span><input id="sellerInsightTo" type="date" value="${sellerInsightsState.dateTo}"></label>
        <button id="sellerInsightRun" class="si-query" onclick="loadSellerInsights()"><i data-lucide="search"></i> 查询数据</button>
      </div>
      <div id="sellerInsightResults" class="si-results"><div class="si-empty">选择店铺和时间范围后查询。</div></div>
    </section>`;
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
  const dimensionSelect = document.getElementById('sellerInsightDimension');
  if (dimensionSelect) {
    dimensionSelect.value = sellerInsightsState.dimension;
    dimensionSelect.addEventListener('change', () => { sellerInsightsState.dimension = dimensionSelect.value; });
  }
  if (window.lucide) lucide.createIcons();
}

function sellerInsightNumber(value, metric) {
  const number = Number(value || 0);
  if (metric.percent) return `${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`;
  if (metric.currency) return `₽${number.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}`;
  return number.toLocaleString('zh-CN', { maximumFractionDigits: metric.decimal ? 2 : 0 });
}

function sellerInsightDimensionValue(row, pattern) {
  return (row.dimensions || []).find((item) => pattern.test(String(item.id || item.name || '')));
}

function sellerInsightDefaultSort(viewId) {
  if (viewId === 'funnel') return { key: 'hits_view_search', direction: 'desc' };
  if (viewId === 'search') return { key: 'hits_view_search', direction: 'desc' };
  return { key: 'ordered_units', direction: 'desc' };
}

function sellerInsightSortForView(viewId) {
  return sellerInsightsState.sortByView[viewId] || sellerInsightDefaultSort(viewId);
}

function toggleSellerInsightSort(metricKey) {
  const current = sellerInsightSortForView(sellerInsightsState.view);
  const direction = current.key === metricKey
    ? (current.direction === 'desc' ? 'asc' : 'desc')
    : (metricKey === 'position_category' ? 'asc' : 'desc');
  sellerInsightsState.sortByView[sellerInsightsState.view] = { key: metricKey, direction };
  const results = document.getElementById('sellerInsightResults');
  if (results && sellerInsightsState.result) results.innerHTML = renderSellerInsightResults(sellerInsightsState.result);
  if (window.lucide) lucide.createIcons();
}

function renderSellerInsightChart(data) {
  const dayIndex = (data.dimension || []).indexOf('day');
  const unitsIndex = data.metrics.indexOf('ordered_units');
  if (dayIndex < 0 || unitsIndex < 0) return '<div class="si-chart-empty">此报表维度未包含按日数据；切换为“按 SKU + 日期”后可查看趋势。</div>';
  const days = new Map();
  data.rows.forEach((row) => {
    const day = row.dimensions?.[dayIndex]?.id || row.dimensions?.[dayIndex]?.name;
    if (!day) return;
    days.set(String(day), (days.get(String(day)) || 0) + Number(row.metrics?.[unitsIndex] || 0));
  });
  const entries = [...days.entries()].sort(([a], [b]) => a.localeCompare(b));
  const width = 1000; const height = 180; const left = 46; const right = 12; const top = 12; const bottom = 30;
  const chartWidth = width - left - right; const chartHeight = height - top - bottom;
  const max = Math.max(1, ...entries.map(([, value]) => value));
  const bars = entries.map(([day, value], index) => {
    const band = chartWidth / Math.max(1, entries.length);
    const barWidth = Math.max(3, Math.min(24, band * 0.46));
    const x = left + band * index + (band - barWidth) / 2;
    const barHeight = value ? Math.max(2, chartHeight * value / max) : 0;
    return `<rect x="${x.toFixed(1)}" y="${(top + chartHeight - barHeight).toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" rx="2" class="si-chart-bar"><title>${escapeHtml(day)} · ${sellerInsightNumber(value, sellerInsightsMetrics[0])}</title></rect>`;
  }).join('');
  const guides = [0, 0.5, 1].map((portion) => {
    const y = top + chartHeight * (1 - portion);
    return `<line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="si-chart-grid"/><text x="${left - 10}" y="${y + 4}" text-anchor="end" class="si-chart-label">${Math.round(max * portion)}</text>`;
  }).join('');
  const xLabels = entries.filter((_, i) => i === 0 || i === entries.length - 1 || i === Math.floor(entries.length / 2)).map(([day], _, labels) => {
    const index = entries.findIndex(([entryDay]) => entryDay === day);
    const x = left + chartWidth * (index + 0.5) / entries.length;
    return `<text x="${x}" y="${height - 5}" text-anchor="middle" class="si-chart-label">${escapeHtml(day)}</text>`;
  }).join('');
  return `<svg class="si-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="订购商品数量（按天）">${guides}${bars}${xLabels}</svg>`;
}

function renderSellerInsightResults(data) {
  const currentView = sellerInsightViews.find((view) => view.id === sellerInsightsState.view) || sellerInsightViews[0];
  const metrics = currentView.metrics.map((key) => sellerInsightsMetrics.find((metric) => metric.key === key)).filter(Boolean);
  const sort = sellerInsightSortForView(currentView.id);
  const rows = [...data.rows];
  const sortIndex = data.metrics.indexOf(sort.key);
  if (sortIndex >= 0) rows.sort((a, b) => {
    const left = Number(a.metrics?.[sortIndex] || 0);
    const right = Number(b.metrics?.[sortIndex] || 0);
    if (sort.key === 'position_category' && (left <= 0 || right <= 0)) {
      if (left <= 0 && right > 0) return 1;
      if (right <= 0 && left > 0) return -1;
    }
    const difference = sort.direction === 'asc' ? left - right : right - left;
    if (difference || sort.key === 'ordered_units') return difference;
    const unitsIndex = data.metrics.indexOf('ordered_units');
    return unitsIndex < 0 ? 0 : Number(b.metrics?.[unitsIndex] || 0) - Number(a.metrics?.[unitsIndex] || 0);
  });
  const hasDay = (data.dimension || []).includes('day');
  const titleCounts = new Map();
  rows.forEach((row) => {
    const dims = row.dimensions || [];
    const skuItem = dims.find((item) => /^\d+$/.test(String(item.id || '')));
    const product = data.products?.[String(skuItem?.id)];
    const baseTitle = String(product?.title || skuItem?.name || skuItem?.id || '');
    if (baseTitle) titleCounts.set(baseTitle, (titleCounts.get(baseTitle) || 0) + 1);
  });
  const bodyRows = rows.map((row) => {
    const dimensions = row.dimensions || [];
    const skuItem = dimensions.find((item) => /^\d+$/.test(String(item.id || '')));
    const sku = skuItem?.id || dimensions[0]?.id || '-';
    const dayItem = dimensions.find((item) => /^\d{4}-\d{2}-\d{2}$/.test(String(item.id || item.name || '')));
    const product = data.products?.[String(sku)];
    const title = product?.title || skuItem?.name || sku;
    const variantCount = titleCounts.get(String(title)) || 1;
    const image = product?.image
      ? `<img class="si-thumb" src="${escapeAttr(proxyImage(product.image))}" data-original-image="${escapeAttr(product.image)}" alt="${escapeAttr(title)}" loading="lazy" referrerpolicy="no-referrer" onerror="if(this.dataset.originalImage&&this.dataset.originalImage!==this.src){const imageUrl=this.dataset.originalImage;this.dataset.originalImage='';this.src=imageUrl;}else{this.onerror=null;this.classList.add('si-image-missing');this.removeAttribute('src');}">`
      : '<div class="si-thumb si-image-missing">无图</div>';
    const shownTitle = product?.titleZh && product.titleZh !== title ? `${escapeHtml(product.titleZh)}<small>${escapeHtml(title)}</small>` : escapeHtml(title);
    const article = product?.offerId || product?.sku || '—';
    return `<tr><td class="si-product"><div class="si-product-cell">${image}<div class="si-product-text"><a href="https://www.ozon.ru/product/${encodeURIComponent(sku)}" target="_blank" rel="noopener noreferrer">${shownTitle}</a><small>货号：${escapeHtml(article)}${variantCount > 1 ? `　·　<span class="si-variant-tag">同款变体 ×${variantCount}</span>` : ''}${dayItem ? `　·　${escapeHtml(dayItem.id || dayItem.name)}` : ''}</small></div></div></td><td class="si-sku">${escapeHtml(sku)}</td>${metrics.map((metric) => `<td>${sellerInsightNumber(row.metrics?.[data.metrics.indexOf(metric.key)], metric)}</td>`).join('')}</tr>`;
  }).join('');
  const totals = metrics.map((metric) => {
    const index = data.metrics.indexOf(metric.key);
    return `<td>${sellerInsightNumber(data.totals?.[index], metric)}</td>`;
  }).join('');
  const stale = Number(data.timestamp) ? new Date(Number(data.timestamp) * 1000).toLocaleString('zh-CN') : (data.timestamp || '—');
  const rowCap = hasDay ? 500 : 1000;
  const notice = rows.length >= rowCap ? '<span class="si-limit-note">达到行数上限，当前表格可能不含全部商品</span>' : '';
  const countLabel = hasDay ? `${rows.length} 行（按天）` : `${rows.length} 个商品`;
  const chartPanel = hasDay
    ? `<section class="si-chart-panel"><h3>订购商品数量（按天）</h3>${renderSellerInsightChart(data)}</section>`
    : '<section class="si-chart-panel"><h3>订购商品数量（按天）</h3><div class="si-chart-empty">当前是「按商品汇总」视图，表内指标已是整个时间段的累计值。要看按天趋势，把「显示方式」切换成“按天拆分”再查询。</div></section>';
  return `<div class="si-meta">店铺：${escapeHtml(data.store)}　·　${escapeHtml(data.dateFrom)} 至 ${escapeHtml(data.dateTo)}　·　${countLabel}　·　Ozon 实时返回　·　更新时间：${escapeHtml(stale)} ${notice}</div>
    ${chartPanel}
    <section class="si-table-panel"><div class="si-table-heading"><h3>详细指标</h3><label class="si-search"><i data-lucide="search"></i><input id="sellerInsightSearch" type="search" placeholder="搜索 SKU" oninput="filterSellerInsightRows(this.value)"></label></div>
    <div class="si-table-scroll"><table class="si-table"><thead><tr><th>商品</th><th>SKU</th>${metrics.map((metric) => {
      const active = sort.key === metric.key;
      const indicator = active ? (sort.direction === 'desc' ? '▼' : '▲') : '↕';
      return `<th><button type="button" class="si-sort${active ? ' active' : ''}" onclick="toggleSellerInsightSort('${metric.key}')">${escapeHtml(metric.label)}<span aria-hidden="true">${indicator}</span></button></th>`;
    }).join('')}</tr></thead><tbody><tr class="si-total"><td colspan="2">Ozon 汇总</td>${totals}</tr>${bodyRows || `<tr><td class="si-empty" colspan="${metrics.length + 2}">该时间段 Ozon 没有返回明细</td></tr>`}</tbody></table></div></section>
    <div class="si-footnote">数据由 Ozon Seller API 原样返回。「按商品汇总」一行一个商品，指标是所选时间段的累计值；「按天拆分」同一商品会按天出现多行，每行只统计那一天。访客类指标按天去重，不能跨天相加。分析接口每店铺每分钟最多请求一次。</div>`;
}

function selectSellerInsightView(viewId) {
  sellerInsightsState.view = viewId;
  document.querySelectorAll('.si-tab').forEach((button) => {
    const active = button.getAttribute('onclick')?.includes(`'${viewId}'`);
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  const results = document.getElementById('sellerInsightResults');
  if (results && sellerInsightsState.result) results.innerHTML = renderSellerInsightResults(sellerInsightsState.result);
  if (window.lucide) lucide.createIcons();
}

function filterSellerInsightRows(query) {
  const needle = String(query || '').trim().toLowerCase();
  document.querySelectorAll('.si-table tbody tr:not(.si-total)').forEach((row) => {
    row.hidden = Boolean(needle) && !row.textContent.toLowerCase().includes(needle);
  });
}

async function loadSellerInsights() {
  const results = document.getElementById('sellerInsightResults');
  const button = document.getElementById('sellerInsightRun');
  if (!results || sellerInsightsState.busy) return;
  if (!sellerInsightsState.storeId) { results.innerHTML = '<div class="si-error">请先在店铺管理配置并选择 Seller API 店铺。</div>'; return; }
  const dateFrom = document.getElementById('sellerInsightFrom').value;
  const dateTo = document.getElementById('sellerInsightTo').value;
  if (!dateFrom || !dateTo || dateFrom > dateTo) { results.innerHTML = '<div class="si-error">请选择有效的开始和结束日期。</div>'; return; }
  sellerInsightsState.busy = true;
  button.disabled = true;
  button.innerHTML = '查询中…';
  results.innerHTML = '<div class="si-empty">正在读取 Ozon 数据…</div>';
  let response;
  const dimension = sellerInsightsState.dimension === 'sku_day' ? 'sku_day' : 'sku';
  try {
    response = await API.post('/api/seller-insights', {
      storeId: sellerInsightsState.storeId, dateFrom, dateTo,
      dimension, limit: dimension === 'sku' ? 1000 : 500,
      metrics: sellerInsightsMetrics.map((item) => item.key),
    }, { timeout: 240000 });
  } catch (error) {
    response = { code: 0, msg: error?.message || '网络错误' };
  }
  sellerInsightsState.busy = false;
  button.disabled = false;
  button.innerHTML = '<i data-lucide="search"></i> 查询数据';
  if (response?.code === 200 && response.data) {
    sellerInsightsState.result = response.data;
    results.innerHTML = renderSellerInsightResults(response.data);
  } else {
    results.innerHTML = `<div class="si-error">查询失败：${escapeHtml(response?.msg || '网络错误')}</div>`;
  }
  if (window.lucide) lucide.createIcons();
}

Router.register('/seller-insights', () => {
  const html = renderSellerInsightsPage();
  setTimeout(initSellerInsightsPage, 50);
  return html;
});
