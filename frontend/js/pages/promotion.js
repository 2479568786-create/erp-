let promotionState = { stores: [], products: [] };

function renderPromotionPage(mode) {
  const isMonitor = mode === 'monitor';
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">${isMonitor ? 'AI 广告监控' : '自动移除促销'}</h2>
          <p class="online-page-desc">${isMonitor ? '按店铺查看已同步的在线商品，作为广告与促销分析入口。' : '先筛选商品并人工确认；当前版本不会自动修改 Ozon 促销活动。'}</p>
        </div>
        <button class="btn btn-sm btn-primary" onclick="loadPromotionData('${mode}')"><i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 刷新数据</button>
      </div>
      <div class="card" style="margin-bottom:14px;display:flex;gap:10px;align-items:center;">
        <label style="font-size:13px;color:var(--text-secondary);">店铺
          <select id="promotionStore" class="form-select" style="display:inline-block;width:220px;margin-left:6px;" onchange="filterPromotionProducts()"><option value="">全部店铺</option></select>
        </label>
        <span id="promotionSummary" style="font-size:13px;color:var(--text-tertiary);"></span>
      </div>
      <div class="card" style="padding:0;overflow:hidden;">
        <table class="collect-table" style="width:100%;">
          <thead><tr><th>商品</th><th>SKU</th><th>店铺</th><th>售价</th><th>库存</th><th>状态</th></tr></thead>
          <tbody id="promotionTableBody"><tr><td colspan="6" style="text-align:center;padding:32px;color:var(--text-tertiary);">加载中...</td></tr></tbody>
        </table>
      </div>
    </div>`;
}

function _promotionEscape(value) {
  return String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

async function loadPromotionData(mode) {
  const body = document.getElementById('promotionTableBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--text-tertiary);">加载中...</td></tr>';
  const [storeResp, productResp] = await Promise.all([
    API.get('/api/stores'),
    API.get('/api/online-products?page=1&pageSize=100'),
  ]);
  promotionState.stores = storeResp?.code === 200 ? (storeResp.data?.list || []) : [];
  promotionState.products = productResp?.code === 200 ? (productResp.data?.list || []) : [];
  const select = document.getElementById('promotionStore');
  if (select) {
    const selected = select.value;
    select.innerHTML = '<option value="">全部店铺</option>' + promotionState.stores.map((store) =>
      `<option value="${_promotionEscape(store.store_id || store.storeId)}">${_promotionEscape(store.alias || store.store_id || store.storeId)}</option>`
    ).join('');
    select.value = selected;
  }
  filterPromotionProducts(mode);
}

function filterPromotionProducts() {
  const selectedStore = document.getElementById('promotionStore')?.value || '';
  const rows = promotionState.products.filter((product) => !selectedStore || String(product.storeId || product.store_id || '') === selectedStore);
  const body = document.getElementById('promotionTableBody');
  const summary = document.getElementById('promotionSummary');
  if (!body) return;
  if (summary) summary.textContent = `已同步 ${rows.length} 个在线商品`;
  body.innerHTML = rows.length ? rows.map((product) => `
    <tr>
      <td>${_promotionEscape(product.title || product.name || '-')}</td>
      <td>${_promotionEscape(product.sku || product.ozonOfferId || '-')}</td>
      <td>${_promotionEscape(product.storeAlias || product.storeName || product.storeId || '-')}</td>
      <td>${_promotionEscape(product.price || '-')}</td>
      <td>${_promotionEscape(product.stock ?? '-')}</td>
      <td>${_promotionEscape(product.status || '-')}</td>
    </tr>`).join('') : '<tr><td colspan="6" style="text-align:center;padding:32px;color:var(--text-tertiary);">暂无已同步的在线商品</td></tr>';
}

Router.register('/ad-monitor', () => {
  const html = renderPromotionPage('monitor');
  requestAnimationFrame(() => loadPromotionData('monitor'));
  return html;
});

Router.register('/promo-remove', () => {
  const html = renderPromotionPage('remove');
  requestAnimationFrame(() => loadPromotionData('remove'));
  return html;
});
