/**
 * Seerfar Ozon 选品工作区。
 * Seerfar Ozon 大盘总览。
 * 查询和导入逻辑复用 research.js 的 Seerfar API 适配函数。
 */
const SEERFAR_SELECTION_NAV = [
  { path: '/seerfar-selection', label: '大盘总览', icon: 'layout-dashboard' },
];

function _seerfarWorkspaceNav(activePath) {
  return `<div class="card" style="padding:8px;display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px;">
    ${SEERFAR_SELECTION_NAV.map(item => `
      <button class="btn btn-sm ${item.path === activePath ? 'btn-primary' : 'btn-ghost'}"
              onclick="Router.navigate('${item.path}')">
        <i data-lucide="${item.icon}" style="width:14px;height:14px;"></i>${item.label}
      </button>`).join('')}
  </div>`;
}

function _seerfarWorkspaceHeader(title, description) {
  return `<div class="online-page-header">
    <div>
      <div style="font-size:12px;color:var(--text-tertiary);margin-bottom:6px;">选品分析 / Seerfar Ozon</div>
      <h2 class="online-page-title">${title}</h2>
      <p class="online-page-desc">${description}</p>
    </div>
    <div style="display:flex;gap:8px;align-items:center;">
      <span class="badge" style="background:#eff6ff;color:#2563eb;">仅 Ozon</span>
      <button class="btn btn-sm btn-ghost" onclick="Router.navigate('/research')">
        <i data-lucide="database" style="width:14px;height:14px;"></i> 选品池
      </button>
    </div>
  </div>`;
}

function _seerfarResultsTable() {
  return `<div class="card" style="padding:0;overflow:hidden;">
    <div style="padding:14px 18px;border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;align-items:center;">
      <h3 style="margin:0;font-size:15px;">分析结果</h3>
      <span style="font-size:12px;color:var(--text-tertiary);">结果来自 Seerfar Ozon 数据</span>
    </div>
    <div style="overflow:auto;max-height:560px;">
      <table class="table" style="width:100%;font-size:12px;">
        <thead><tr><th>商品/关键词</th><th>价格</th><th>月销售额/搜索量</th><th>月销量/增长</th><th>卖家/竞品</th><th>操作</th></tr></thead>
        <tbody id="seerfarSelectionBody"><tr><td colspan="6" style="text-align:center;padding:45px;color:var(--text-tertiary);">设置筛选条件后点击查询</td></tr></tbody>
      </table>
    </div>
  </div>`;
}

function _seerfarDonut(title, metric, categories) {
  const values = categories.map(item => Number(item[metric]) || 0);
  const total = values.reduce((sum, value) => sum + value, 0);
  const colors = ['#6366f1', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#f97316', '#8b5cf6', '#06b6d4'];
  let cursor = 0;
  const stops = categories.slice(0, 8).map((item, index) => {
    const start = cursor;
    cursor += total ? ((Number(item[metric]) || 0) / total) * 100 : 0;
    return `${colors[index % colors.length]} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
  });
  const background = stops.length ? `conic-gradient(${stops.join(',')})` : 'conic-gradient(#334155 0 100%)';
  return `<div class="card" style="padding:18px;min-height:360px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;"><h3 style="margin:0;font-size:15px;">${title}</h3></div>
    <div style="display:flex;gap:20px;align-items:center;justify-content:center;flex-wrap:wrap;">
      <div style="width:210px;height:210px;border-radius:50%;background:${background};display:grid;place-items:center;flex:none;">
        <div style="width:112px;height:112px;border-radius:50%;background:var(--card-bg,#111827);display:grid;place-items:center;text-align:center;"><strong style="font-size:18px;">${total ? total.toLocaleString() : '—'}</strong><small style="color:var(--text-tertiary);">样本合计</small></div>
      </div>
      <div style="display:grid;gap:7px;min-width:180px;">${categories.slice(0, 8).map((item, index) => `<div style="display:flex;align-items:center;gap:7px;font-size:12px;"><i style="width:9px;height:9px;border-radius:50%;background:${colors[index % colors.length]};"></i><span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:145px;">${_seerfarEsc(item.name)}</span><span>${total ? ((Number(item[metric]) || 0) / total * 100).toFixed(2) : '0.00'}%</span></div>`).join('') || '<span style="color:var(--text-tertiary);">暂无 Seerfar 数据</span>'}</div>
    </div>
  </div>`;
}

function _seerfarCacheRead(key) {
  try { return JSON.parse(localStorage.getItem(`seerfar:${key}`) || 'null'); } catch (_) { return null; }
}

function _seerfarCacheWrite(key, value) {
  try { localStorage.setItem(`seerfar:${key}`, JSON.stringify(value)); } catch (_) { /* cache is optional */ }
}

function _renderSeerfarOverview(data) {
  const target = document.getElementById('seerfarOverviewCharts');
  if (!target) return;
  const categories = data?.categories || [];
  target.innerHTML = _seerfarDonut('类目月销量占比', 'monthlySales', categories) + _seerfarDonut('类目月销售额占比', 'monthlyRevenue', categories);
  if (window.lucide) lucide.createIcons();
}

async function loadSeerfarOverview(refresh = false) {
  const target = document.getElementById('seerfarOverviewCharts');
  if (!target) return;
  const cached = _seerfarCacheRead('overview');
  if (!refresh && cached?.data) { _renderSeerfarOverview(cached.data); return; }
  target.innerHTML = '<div class="card" style="padding:40px;text-align:center;grid-column:1/-1;">正在刷新 Seerfar 类目数据...</div>';
  const resp = await API.post('/api/seerfar/overview', { pageNumber: 1, pageSize: 20 });
  if (resp?.code !== 200) {
    target.innerHTML = `<div class="card" style="padding:30px;color:var(--danger);grid-column:1/-1;text-align:center;">${_seerfarEsc(resp?.msg || 'Seerfar 数据加载失败')}</div>`;
    return;
  }
  _seerfarCacheWrite('overview', { savedAt: Date.now(), data: resp.data });
  _renderSeerfarOverview(resp.data);
}

let _seerfarCategoryTree = [];
let _seerfarSelectedCategoryId = '';

function _seerfarCategoryName(node) {
  return node?.category_name_zh || node?.category_name || node?.name || '未命名类目';
}

function _renderSeerfarCategoryTabs() {
  const el = document.getElementById('seerfarCategoryTabs');
  if (!el) return;
  el.innerHTML = _seerfarCategoryTree.map(node => {
    const id = node.description_category_id;
    const active = String(id) === String(_seerfarSelectedCategoryId);
    return `<button class="btn btn-sm ${active ? 'btn-primary' : 'btn-ghost'}" onclick="selectSeerfarCategory('${_seerfarEsc(id)}')">${_seerfarEsc(_seerfarCategoryName(node))}</button>`;
  }).join('') || '<span style="color:var(--text-tertiary);">ERP 类目树暂无数据</span>';
  if (window.lucide) lucide.createIcons();
}

async function loadSeerfarCategoryTree() {
  const resp = await API.get('/api/categories');
  if (resp?.code !== 200 || !Array.isArray(resp.data)) {
    const el = document.getElementById('seerfarCategoryTabs');
    if (el) el.textContent = resp?.msg || 'ERP 类目树加载失败';
    return;
  }
  _seerfarCategoryTree = resp.data;
  _seerfarSelectedCategoryId = _seerfarCategoryTree[0]?.description_category_id || '';
  _renderSeerfarCategoryTabs();
  if (_seerfarSelectedCategoryId) loadSeerfarCategoryAnalysis(false);
}

function selectSeerfarCategory(categoryId) {
  _seerfarSelectedCategoryId = categoryId;
  _renderSeerfarCategoryTabs();
  loadSeerfarCategoryAnalysis(false);
}

function resetSeerfarCategoryFilters() {
  ['seerfarMinSales', 'seerfarMaxSales', 'seerfarMinPrice', 'seerfarMaxPrice', 'seerfarMinRevenue', 'seerfarMaxRevenue', 'seerfarMinGrowth', 'seerfarMaxGrowth'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  loadSeerfarCategoryAnalysis(true);
}

function _seerfarCategoryCell(record, ...keys) {
  return _seerfarEsc(_seerfarValue(record, ...keys));
}

function _seerfarCategoryCacheKey() {
  return `category:${_seerfarSelectedCategoryId}:${['seerfarMinSales', 'seerfarMaxSales', 'seerfarMinPrice', 'seerfarMaxPrice', 'seerfarMinRevenue', 'seerfarMaxRevenue', 'seerfarMinGrowth', 'seerfarMaxGrowth'].map(id => document.getElementById(id)?.value || '').join(',')}`;
}

function _seerfarCategoryPath(record) {
  return record?.categoryInfo?.cnTitlePath || record?.categoryInfo?.category?.cnTitle || record?.categoryName || '接口未提供';
}

function _renderSeerfarCategoryResults(data) {
  const body = document.getElementById('seerfarCategoryBody');
  if (!body) return;
  const records = Array.isArray(data?.records) ? data.records : [];
  const selectedCategory = _seerfarCategoryTree.find(item => String(item.description_category_id) === String(_seerfarSelectedCategoryId));
  const meta = document.getElementById('seerfarCategoryMeta');
  if (meta) meta.textContent = `${_seerfarCategoryName(selectedCategory)} · 共 ${data?.total || records.length} 条`;
  if (!records.length) { body.innerHTML = '<tr><td colspan="13" style="text-align:center;padding:40px;color:var(--text-tertiary);">暂无数据，请点击刷新</td></tr>'; return; }
  body.innerHTML = records.map((record, index) => `<tr>
    <td>${index + 1}</td><td>${_seerfarEsc(_seerfarCategoryPath(record))}</td><td><div style="font-weight:600;max-width:260px;">${_seerfarCategoryCell(record, 'title', 'name')}</div><small>${_seerfarCategoryCell(record, 'sku', 'ozonId', 'id')}</small></td>
    <td>${_seerfarCategoryCell(record, 'monthlySales', 'sales')}</td><td>${_seerfarCategoryCell(record, 'monthlyRevenue', 'revenue')}</td><td>${_seerfarCategoryCell(record, 'monthlySalesRate', 'salesRate', 'revenueGrowthRate')}</td><td>${_seerfarCategoryCell(record, 'avgPrice', 'price')}</td><td>${_seerfarCategoryCell(record, 'sellers', 'sellerCount')}</td><td>${_seerfarCategoryCell(record, 'brands', 'brandCount')}</td><td>${_seerfarCategoryCell(record, 'brandSalesShare')}</td><td>${_seerfarCategoryCell(record, 'fbsSalesShare')}</td><td>${_seerfarCategoryCell(record, 'returnCancellationRate')}</td><td>${_seerfarCategoryCell(record, 'bigSellerShare', 'topSellerShare')}</td>
  </tr>`).join('');
}

async function loadSeerfarCategoryAnalysis(refresh = false) {
  const body = document.getElementById('seerfarCategoryBody');
  if (!body || !_seerfarSelectedCategoryId) return;
  const cacheKey = _seerfarCategoryCacheKey();
  const cached = _seerfarCacheRead(cacheKey);
  if (!refresh && cached?.data) { _renderSeerfarCategoryResults(cached.data); return; }
  if (!refresh) { body.innerHTML = '<tr><td colspan="13" style="text-align:center;padding:40px;color:var(--text-tertiary);">请选择条件后点击刷新</td></tr>'; return; }
  body.innerHTML = '<tr><td colspan="13" style="text-align:center;padding:40px;">正在刷新...</td></tr>';
  const fields = [
    ['seerfarMinSales', 'monthlySalesMin'], ['seerfarMaxSales', 'monthlySalesMax'],
    ['seerfarMinPrice', 'priceMin'], ['seerfarMaxPrice', 'priceMax'],
    ['seerfarMinRevenue', 'monthlyRevenueMin'], ['seerfarMaxRevenue', 'monthlyRevenueMax'],
    ['seerfarMinGrowth', 'monthlySalesRateMin'], ['seerfarMaxGrowth', 'monthlySalesRateMax'],
  ];
  const filters = { pageNumber: 1, pageSize: 20 };
  fields.forEach(([id, key]) => { const value = document.getElementById(id)?.value; if (value !== '') filters[key] = Number(value); });
  const selectedCategory = _seerfarCategoryTree.find(item => String(item.description_category_id) === String(_seerfarSelectedCategoryId));
  filters.categoryName = _seerfarCategoryName(selectedCategory);
  const resp = await API.post(`/api/seerfar/categories/${encodeURIComponent(_seerfarSelectedCategoryId)}/products`, filters);
  if (resp?.code !== 200) { body.innerHTML = `<tr><td colspan="13" style="text-align:center;padding:40px;color:var(--danger);">${_seerfarEsc(resp?.msg || '查询失败')}</td></tr>`; return; }
  _seerfarCacheWrite(cacheKey, { savedAt: Date.now(), data: resp.data });
  _renderSeerfarCategoryResults(resp.data);
}

function _seerfarQueryControls(mode) {
  const categoryVisible = mode === 'category';
  const keywordVisible = mode === 'keywords';
  const revenueVisible = ['rankings', 'keywords', 'category'].includes(mode);
  if (categoryVisible) return `<div class="card" style="padding:16px;margin-bottom:16px;">
    <div id="seerfarCategoryTabs" style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;color:var(--text-secondary);">正在加载 ERP 类目树...</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <input id="seerfarMinSales" class="form-input" type="number" placeholder="月销量 ≥" style="width:130px;">
      <input id="seerfarMaxSales" class="form-input" type="number" placeholder="月销量 ≤" style="width:130px;">
      <input id="seerfarMinPrice" class="form-input" type="number" placeholder="平均价格 ≥ ₽" style="width:150px;">
      <input id="seerfarMaxPrice" class="form-input" type="number" placeholder="平均价格 ≤ ₽" style="width:150px;">
      <input id="seerfarMinRevenue" class="form-input" type="number" placeholder="月销售额 ≥ ₽" style="width:155px;">
      <input id="seerfarMaxRevenue" class="form-input" type="number" placeholder="月销售额 ≤ ₽" style="width:155px;">
      <input id="seerfarMinGrowth" class="form-input" type="number" placeholder="增长率 ≥ %" style="width:135px;">
      <input id="seerfarMaxGrowth" class="form-input" type="number" placeholder="增长率 ≤ %" style="width:135px;">
      <button class="btn btn-primary btn-sm" onclick="loadSeerfarCategoryAnalysis(true)"><i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 刷新数据</button>
      <button class="btn btn-ghost btn-sm" onclick="resetSeerfarCategoryFilters()">重置</button>
    </div>
  </div>`;
  return `<div class="card" style="padding:18px;margin-bottom:16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div><h3 style="margin:0;font-size:16px;">筛选条件</h3><p style="margin:4px 0 0;color:var(--text-tertiary);font-size:12px;">筛选后查询 Seerfar Ozon 数据。</p></div>
      <span style="font-size:12px;color:var(--text-tertiary);">管理员可将候选商品加入选品池</span>
    </div>
    <select id="seerfarMode" style="display:none;"><option value="${mode}" selected></option></select>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <input id="seerfarCategoryId" class="form-input" placeholder="Seerfar 类目 ID" style="width:180px;display:${categoryVisible ? '' : 'none'};">
      <input id="seerfarKeyword" class="form-input" placeholder="关键词" style="width:240px;display:${keywordVisible ? '' : 'none'};">
      <input id="seerfarMinRevenue" class="form-input" type="number" placeholder="最低月销售额 ₽" style="width:190px;display:${revenueVisible ? '' : 'none'};">
      <button class="btn btn-primary btn-sm" onclick="loadSeerfarSelection()"><i data-lucide="search" style="width:14px;height:14px;"></i> 查询</button>
    </div>
  </div>`;
}

function renderSeerfarSelectionPage() {
  return `<div class="online-page" style="animation:pageEnter .35s ease;">
    ${_seerfarWorkspaceHeader('Seerfar Ozon 选品大盘', '汇总 Seerfar Ozon 市场数据，查看 Ozon 选品大盘。')}
    ${_seerfarWorkspaceNav('/seerfar-selection')}
    <div style="display:flex;justify-content:flex-end;margin-bottom:10px;"><button class="btn btn-primary btn-sm" onclick="loadSeerfarOverview(true)"><i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 刷新数据</button></div>
    <div id="seerfarOverviewCharts" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:16px;"><div class="card" style="padding:40px;text-align:center;grid-column:1/-1;">暂无缓存数据，请点击“刷新数据”</div></div>
  </div>`;
}

function renderSeerfarCategoryPage() {
  return `<div class="online-page" style="animation:pageEnter .35s ease;">
    ${_seerfarWorkspaceHeader('类目分析', '按 Seerfar 类目查看商品规模、销售表现和竞争情况。')}
    ${_seerfarWorkspaceNav('/seerfar-selection/category')}
    ${_seerfarQueryControls('category')}
    <div class="card" style="padding:0;overflow:hidden;">
      <div style="padding:14px 18px;border-bottom:1px solid var(--border-color);display:flex;justify-content:space-between;align-items:center;"><h3 style="margin:0;font-size:15px;">类目商品分析</h3><span id="seerfarCategoryMeta" style="font-size:12px;color:var(--text-tertiary);">请选择 ERP 类目</span></div>
      <div style="overflow:auto;max-height:620px;"><table class="table" style="width:100%;font-size:12px;min-width:1250px;"><thead><tr><th>序号</th><th>中文类目</th><th>商品信息（原文）</th><th>月销量</th><th>月销售额</th><th>月销售额增长率</th><th>平均价格</th><th>卖家数量</th><th>品牌数量</th><th>品牌销量占比</th><th>FBS销量占比</th><th>退货取消率</th><th>大卖家占比</th></tr></thead><tbody id="seerfarCategoryBody"><tr><td colspan="13" style="text-align:center;padding:45px;color:var(--text-tertiary);">请选择类目后点击刷新</td></tr></tbody></table></div>
    </div>
  </div>`;
}

async function loadSeerfarShop(refresh = false) {
  const shopId = document.getElementById('seerfarShopId')?.value.trim();
  const body = document.getElementById('seerfarShopBody');
  if (!body || !shopId) { if (body) body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--danger);">请输入店铺 ID</td></tr>'; return; }
  const page = Number(document.getElementById('seerfarShopPage')?.value || 1);
  const cacheKey = `shop:${shopId}:${page}`;
  const cached = _seerfarCacheRead(cacheKey);
  if (!refresh && cached?.data) { _renderSeerfarShop(cached.data); return; }
  body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;">正在刷新店铺数据...</td></tr>';
  const resp = await API.post(`/api/seerfar/shops/${encodeURIComponent(shopId)}/products`, { pageNumber: page, pageSize: 20 });
  if (resp?.code !== 200) { body.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--danger);">${_seerfarEsc(resp?.msg || '查询失败')}</td></tr>`; return; }
  _seerfarCacheWrite(cacheKey, { savedAt: Date.now(), data: resp.data });
  _renderSeerfarShop(resp.data);
}

function _renderSeerfarShop(data) {
  const body = document.getElementById('seerfarShopBody');
  if (!body) return;
  const products = data?.products || [];
  const summary = document.getElementById('seerfarShopSummary');
  if (summary) summary.innerHTML = (data?.productCount !== undefined ? `<span>商品数：${data.productCount}</span>` : '') + (data?.rating !== undefined ? `<span>评分：${data.rating}</span>` : '') + (data?.totalRevenue !== undefined ? `<span>近30天销售额：${data.totalRevenue}</span>` : '') + (data?.totalSales !== undefined ? `<span>近30天销量：${data.totalSales}</span>` : '') + (data?.dailySales !== undefined ? `<span>日均销量：${data.dailySales}</span>` : '') + (data?.fulfillment ? `<span>配送：${Object.entries(data.fulfillment).map(([key, value]) => `${key} ${value}`).join(' / ')}</span>` : '');
  const categoryMap = new Map();
  const categoryRecords = data?.categorySummary?.records || [];
  categoryRecords.forEach(item => { const name = item.categoryInfo?.cnTitlePath || item.categoryInfo?.category?.cnTitle || '接口未提供'; categoryMap.set(name, item.totalSales ?? item.products ?? 0); });
  if (!categoryRecords.length) products.forEach(item => { const name = item.categoryInfo?.cnTitlePath || item.categoryInfo?.category?.cnTitle || '接口未提供'; categoryMap.set(name, (categoryMap.get(name) || 0) + 1); });
  const cats = document.getElementById('seerfarShopCategories');
  if (cats) cats.innerHTML = Array.from(categoryMap.entries()).map(([name, count]) => `<span class="badge">${_seerfarEsc(name)} (${count})</span>`).join('') || '当前页暂无中文类目';
  if (!products.length) { body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;">暂无商品数据</td></tr>'; return; }
  body.innerHTML = products.map((item, index) => `<tr><td>${index + 1}</td><td>${_seerfarEsc(item.categoryInfo?.cnTitlePath || item.categoryInfo?.category?.cnTitle || '接口未提供')}</td><td><div style="font-weight:600;max-width:280px;">${_seerfarEsc(item.title)}</div><small>${_seerfarEsc(item.sku)}</small></td><td>${_seerfarEsc(item.sales)}</td><td>${_seerfarEsc(item.revenue)}</td><td>${_seerfarEsc(item.salesRate)}</td><td>${_seerfarEsc(item.price)}</td><td>${_seerfarEsc(item.reviewRating)}</td><td>${_seerfarEsc(item.sellers)}</td><td>${_seerfarEsc((item.fulfillment || []).join(', ') || '接口未提供')}</td></tr>`).join('');
}

function renderSeerfarShopPage() {
  return `<div class="online-page" style="animation:pageEnter .35s ease;">
    ${_seerfarWorkspaceHeader('店铺查询', '输入 Ozon 店铺 ID，查询近 30 天商品、店铺汇总和商品类目。')}
    ${_seerfarWorkspaceNav('/seerfar-selection/shop-lookup')}
    <div class="card" style="padding:16px;margin-bottom:16px;"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;"><input id="seerfarShopId" class="form-input" placeholder="Ozon 店铺 ID" style="width:220px;"><input id="seerfarShopPage" class="form-input" type="number" min="1" value="1" style="width:100px;"><button class="btn btn-primary btn-sm" onclick="loadSeerfarShop(true)"><i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 刷新查询</button></div></div>
    <div id="seerfarShopSummary" class="card" style="padding:16px;display:flex;gap:18px;flex-wrap:wrap;color:var(--text-secondary);">输入店铺 ID 后点击刷新查询</div>
    <div class="card" style="padding:16px;margin:16px 0;"><h3 style="margin:0 0 12px;font-size:15px;">店铺经营类目（当前页）</h3><div id="seerfarShopCategories" style="display:flex;gap:8px;flex-wrap:wrap;color:var(--text-tertiary);">暂无数据</div></div>
    <div class="card" style="padding:0;overflow:hidden;"><div style="padding:14px 18px;border-bottom:1px solid var(--border-color);"><h3 style="margin:0;font-size:15px;">近 30 天店铺商品</h3></div><div style="overflow:auto;max-height:620px;"><table class="table" style="width:100%;font-size:12px;min-width:1200px;"><thead><tr><th>序号</th><th>中文类目</th><th>商品信息（原文）</th><th>销量</th><th>销售额</th><th>销量增长率</th><th>价格</th><th>评分</th><th>卖家数</th><th>配送方式</th></tr></thead><tbody id="seerfarShopBody"><tr><td colspan="10" style="text-align:center;padding:45px;color:var(--text-tertiary);">请输入店铺 ID 后点击刷新查询</td></tr></tbody></table></div></div>
  </div>`;
}

function renderSeerfarKeywordPage() {
  return `<div class="online-page" style="animation:pageEnter .35s ease;">
    ${_seerfarWorkspaceHeader('标签查询', '输入市场关键词，查看搜索量、增长趋势和竞争商品数量。')}
    ${_seerfarWorkspaceNav('/seerfar-selection/keyword-lookup')}
    ${_seerfarQueryControls('keywords')}
    ${_seerfarResultsTable()}
  </div>`;
}

Router.register('/seerfar-selection', (route) => { const html = renderSeerfarSelectionPage(); setTimeout(() => { const cached = _seerfarCacheRead('overview'); if (cached?.data) _renderSeerfarOverview(cached.data); }, 0); return html; });
