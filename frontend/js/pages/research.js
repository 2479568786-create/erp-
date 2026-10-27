/**
 * 选品池页 - Research Pool Page
 * 选品池：参考商品与标杆店铺台账
 */
let researchState = {
  kind: '',
  keyword: '',
  items: [],
  users: [],
};

/** 当前用户是否为管理员（选品池写操作仅管理员可用） */
function _researchIsAdmin() {
  return Store?.getUser?.()?.role === 'admin';
}

function renderResearchPage() {
  const isAdmin = _researchIsAdmin();
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">选品池</h2>
          <p class="online-page-desc">参考商品与标杆店铺台账，按负责人分配和查看</p>
        </div>
        <div>
          <button class="btn btn-sm" onclick="Router.navigate('/seerfar-selection')" style="margin-right:8px;">
            Seerfar Ozon 选品
          </button>
          ${isAdmin ? `
          <button class="btn btn-primary btn-sm" onclick="openResearchModal()">
            <i data-lucide="plus" style="width:14px;height:14px;"></i> 加入选品
          </button>` : `<span style="font-size:12px;color:var(--text-tertiary);">仅管理员可维护选品池</span>`}
        </div>
      </div>

      <div class="batch-action-bar">
        <div class="batch-action-right">
          <input type="text" id="researchKeyword" class="form-input" placeholder="搜索名称/备注/链接"
                 style="width:220px;height:34px;font-size:13px;"
                 onkeydown="if(event.key==='Enter'){researchState.keyword=this.value.trim();refreshResearchView();}">
        </div>
      </div>

      <div id="researchSections"><div class="card" style="padding:40px;text-align:center;">加载中...</div></div>
      <div id="researchInfo" style="margin-top:8px;font-size:12px;color:var(--text-tertiary);"></div>
    </div>

    <div id="seerfarSelectionPanel" class="modal-overlay" style="display:none;">
      <div class="modal-dialog" style="max-width:980px;">
        <div class="modal-header">
          <span class="modal-title">Seerfar Ozon 选品</span>
          <button class="modal-close" onclick="closeSeerfarSelectionPanel()">×</button>
        </div>
        <div class="modal-body" style="padding:16px 20px;">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;">
            <select id="seerfarMode" class="form-select" style="width:auto;">
              <option value="rankings">热销榜单</option>
              <option value="keywords">市场热词</option>
              <option value="category">类目商品</option>
              <option value="product">SKU 详情</option>
              <option value="similar">相似商品</option>
            </select>
            <input id="seerfarMinRevenue" class="form-input" type="number" placeholder="最低月销售额 ₽" style="width:180px;">
            <input id="seerfarKeyword" class="form-input" placeholder="关键词（市场热词）" style="width:220px;display:none;">
            <input id="seerfarCategoryId" class="form-input" placeholder="Seerfar 类目 ID" style="width:170px;display:none;">
            <input id="seerfarSku" class="form-input" placeholder="Ozon SKU" style="width:150px;display:none;">
            <button class="btn btn-primary btn-sm" onclick="loadSeerfarSelection()">查询</button>
          </div>
          <div class="card" style="padding:0;overflow:auto;max-height:480px;">
            <table class="table" style="width:100%;font-size:12px;">
              <thead><tr><th>商品/关键词</th><th>价格</th><th>月销售额/搜索量</th><th>月销量/增长</th><th>卖家/竞品</th><th>操作</th></tr></thead>
              <tbody id="seerfarSelectionBody"><tr><td colspan="6" style="text-align:center;padding:30px;">点击查询加载 Seerfar 数据</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 新增/编辑弹窗 -->
    <div id="researchModal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog" style="max-width:640px;">
        <div class="modal-header">
          <span class="modal-title" id="researchModalTitle">加入选品池</span>
          <button class="modal-close" onclick="closeResearchModal()">×</button>
        </div>
        <div class="modal-body" style="padding:16px 20px;">
          <input type="hidden" id="rf-id">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <label class="form-label" style="grid-column:1/3;">商品/店铺名称 *
              <input id="rf-name" class="form-input"></label>
            <label class="form-label">类型
              <select id="rf-kind" class="form-select">
                <option value="reference_product">参考商品</option>
                <option value="benchmark_store">标杆店铺</option>
              </select></label>
            <label class="form-label" style="grid-column:1/3;">来源链接
              <input id="rf-url" class="form-input" placeholder="https://www.ozon.ru/product/..."></label>
            <label class="form-label">建议售价 ₽<input id="rf-price" class="form-input" type="number"></label>
            <label class="form-label">负责人（已绑定店铺的业务员，可多选）
              <select id="rf-owner" class="form-select" multiple size="4"></select></label>
          </div>
          <label class="form-label">备注<textarea id="rf-notes" class="form-input" rows="2"></textarea></label>
        </div>
        <div class="modal-footer" style="padding:12px 20px;">
          <button class="btn btn-sm" onclick="closeResearchModal()">取消</button>
          <button class="btn btn-primary btn-sm" onclick="saveResearch()">保存</button>
        </div>
      </div>
    </div>
  `;
}

async function initResearchPage() {
  await loadResearchUsers();
  await refreshResearchView();
  const mode = document.getElementById('seerfarMode');
  if (mode) mode.addEventListener('change', () => {
    document.getElementById('seerfarKeyword').style.display = mode.value === 'keywords' ? '' : 'none';
    document.getElementById('seerfarCategoryId').style.display = mode.value === 'category' ? '' : 'none';
    document.getElementById('seerfarSku').style.display = ['product', 'similar'].includes(mode.value) ? '' : 'none';
    document.getElementById('seerfarMinRevenue').style.display = ['rankings', 'keywords', 'category'].includes(mode.value) ? '' : 'none';
  });
}

async function loadResearchUsers() {
  if (!_researchIsAdmin()) return;
  const resp = await API.get('/api/users');
  researchState.users = (resp?.code === 200 ? (resp.data?.list || []) : [])
    .filter(user => user.status !== 'disabled' && user.role === 'salesperson' && Number(user.storeCount) > 0);
  renderResearchOwnerOptions([]);
}

function renderResearchOwnerOptions(selectedIds) {
  const select = document.getElementById('rf-owner');
  if (!select) return;
  const selected = new Set((selectedIds || []).map(String));
  select.innerHTML = researchState.users.map(user =>
    `<option value="${user.id}" ${selected.has(String(user.id)) ? 'selected' : ''}>${_seerfarEsc(user.nickname || user.username)}（${_seerfarEsc(user.username)}）</option>`
  ).join('') || '<option disabled>暂无可分配业务员</option>';
}

function openSeerfarSelectionPanel() {
  const panel = document.getElementById('seerfarSelectionPanel');
  if (panel) panel.style.display = 'flex';
  loadSeerfarQuota();
}

function closeSeerfarSelectionPanel() {
  const panel = document.getElementById('seerfarSelectionPanel');
  if (panel) panel.style.display = 'none';
}

async function loadSeerfarQuota() {
  const resp = await API.get('/api/seerfar/quota');
  const el = document.getElementById('seerfarQuota');
  if (!el) return;
  if (resp?.code === 200 && resp.data) {
    el.textContent = `API 额度：${resp.data.creditUsed || 0}/${resp.data.creditLimit || 0}`;
  } else {
    el.textContent = resp?.msg || '额度读取失败';
  }
}

function _seerfarValue(record, ...keys) {
  for (const key of keys) {
    if (record && record[key] !== null && record[key] !== undefined) return record[key];
  }
  return '-';
}

function _seerfarEsc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
}

async function loadSeerfarSelection() {
  const body = document.getElementById('seerfarSelectionBody');
  if (!body) return;
  body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;">查询中...</td></tr>';
  const mode = document.getElementById('seerfarMode')?.value || 'rankings';
  const minRevenue = document.getElementById('seerfarMinRevenue')?.value;
  const keyword = document.getElementById('seerfarKeyword')?.value.trim();
  const categoryId = document.getElementById('seerfarCategoryId')?.value.trim();
  const sku = document.getElementById('seerfarSku')?.value.trim();
  const filters = { pageNumber: 1, pageSize: 50 };
  if (minRevenue) filters.monthlyRevenueMin = Number(minRevenue);
  if (keyword) filters.keywords = [keyword];
  let resp;
  if (mode === 'keywords') {
    resp = await API.post('/api/seerfar/market-keywords', filters);
  } else if (mode === 'category') {
    if (!categoryId) { body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--danger);">请输入类目 ID</td></tr>'; return; }
    resp = await API.post(`/api/seerfar/categories/${encodeURIComponent(categoryId)}/products`, filters);
  } else if (mode === 'product') {
    if (!sku) { body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--danger);">请输入 Ozon SKU</td></tr>'; return; }
    resp = await API.post(`/api/seerfar/products/${encodeURIComponent(sku)}`, { dateRange: 'past_30_days' });
  } else if (mode === 'similar') {
    if (!sku) { body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--danger);">请输入 Ozon SKU</td></tr>'; return; }
    resp = await API.post(`/api/seerfar/products/${encodeURIComponent(sku)}/similar`, {});
  } else {
    resp = await API.post('/api/seerfar/rankings', filters);
  }
  if (resp?.code !== 200) {
    body.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--danger);">${_seerfarEsc(resp?.msg || '查询失败')}</td></tr>`;
    return;
  }
  const rawRecords = resp.data?.records || resp.data?.items || (resp.data?.title || resp.data?.sku ? [resp.data] : []);
  const records = Array.isArray(rawRecords) ? rawRecords : [];
  if (!records.length) {
    body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:var(--text-tertiary);">暂无数据</td></tr>';
    return;
  }
  body.innerHTML = records.map((record, index) => {
    const sku = _seerfarValue(record, 'sku', 'ozonId', 'id');
    const title = _seerfarValue(record, 'title', 'query', 'queryCn');
    const isKeyword = mode === 'keywords';
    const hasSourceId = sku !== '-';
    return `<tr>
      <td><div style="font-weight:600;max-width:300px;">${_seerfarEsc(title)}</div><small>${_seerfarEsc(sku)}</small></td>
      <td>${_seerfarEsc(_seerfarValue(record, 'price', 'avgPrice'))}</td>
      <td>${_seerfarEsc(_seerfarValue(record, isKeyword ? 'searchVolume' : 'monthlyRevenue'))}</td>
      <td>${_seerfarEsc(_seerfarValue(record, isKeyword ? 'searchChange30' : 'monthlySales'))}</td>
      <td>${_seerfarEsc(_seerfarValue(record, isKeyword ? 'competingProducts' : 'sellers'))}</td>
      <td>${!isKeyword && hasSourceId && _researchIsAdmin() ? `<button class="btn btn-xs" onclick="importSeerfarSelection(${index})">加入选品池</button>` : '-'}</td>
    </tr>`;
  }).join('');
  window._seerfarSelectionRecords = records;
}

async function importSeerfarSelection(index) {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  const record = window._seerfarSelectionRecords?.[index];
  if (!record) return;
  const payload = {
    ...SeerfarSelectionServiceRecord(record),
    source_payload_json: record,
  };
  const resp = await API.post('/api/seerfar/import', payload);
  if (resp?.code === 200) {
    Toast.show(resp.msg || '已加入选品池', 'success');
    refreshResearchView();
  } else {
    Toast.show(resp?.msg || '导入失败', 'error');
  }
}

function SeerfarSelectionServiceRecord(record) {
  const sku = _seerfarValue(record, 'sku', 'ozonId', 'id');
  return {
    source_id: String(sku === '-' ? '' : sku),
    product_name: _seerfarValue(record, 'title', 'name') || `Ozon 商品 ${sku}`,
    source_url: record.url || (sku !== '-' ? `https://www.ozon.ru/product/${sku}/` : ''),
    suggested_price: record.price || record.avgPrice || null,
  };
}

async function refreshResearchView() {
  const sections = document.getElementById('researchSections');
  if (sections) sections.innerHTML = '<div class="card" style="padding:40px;text-align:center;">加载中...</div>';
  const resp = await API.get('/api/product-research', {
    keyword: researchState.keyword,
  });
  if (resp && resp.code === 200) {
    researchState.items = resp.data || [];
    renderResearchTable();
  } else {
    if (sections) sections.innerHTML = `<div class="card" style="padding:40px;text-align:center;color:var(--danger);">${resp?.msg || '加载失败'}</div>`;
  }
  const info = document.getElementById('researchInfo');
  if (info) info.textContent = `共 ${researchState.items.length} 条`;
}

function renderResearchTable() {
  const sections = document.getElementById('researchSections');
  if (!sections) return;
  const isAdmin = _researchIsAdmin();
  const table = (title, kind, emptyText) => {
    const items = researchState.items.filter(item => item.kind === kind);
    const rows = items.length ? items.map(it => `<tr>
      <td><div style="font-weight:600;">${_seerfarEsc(it.product_name)}</div>${it.source_url ? `<a href="${_seerfarEsc(it.source_url)}" target="_blank" style="font-size:11px;color:var(--primary);">查看来源 ↗</a>` : ''}</td>
      <td>${it.suggested_price ?? '-'}</td><td>${_seerfarEsc(it.owner_name || '未分配')}</td><td>${_seerfarEsc(it.notes || '-')}</td>
      <td>${isAdmin ? `<button class="btn btn-xs" onclick="openResearchModal(${it.id})">编辑</button><button class="btn btn-xs" onclick="copyResearch(${it.id})">复制</button><button class="btn btn-xs" style="color:var(--danger);" onclick="deleteResearch(${it.id})">删除</button>` : '-'}</td>
    </tr>`).join('') : `<tr><td colspan="5" style="text-align:center;padding:28px;color:var(--text-tertiary);">${emptyText}</td></tr>`;
    return `<div class="card" style="padding:0;overflow:hidden;margin-bottom:16px;"><div style="padding:14px 18px;border-bottom:1px solid var(--border-color);font-weight:700;">${title} <span style="font-size:12px;color:var(--text-tertiary);font-weight:400;">（${items.length}）</span></div><div style="overflow:auto;"><table class="table" style="width:100%;font-size:13px;min-width:760px;"><thead><tr><th style="min-width:240px;">商品 / 店铺</th><th>建议售价</th><th>负责人</th><th>备注</th><th style="min-width:160px;">操作</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  };
  sections.innerHTML = table('标杆店铺', 'benchmark_store', '暂无标杆店铺') + table('参考商品', 'reference_product', '暂无参考商品');
}

function openResearchModal(id) {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  const modal = document.getElementById('researchModal');
  if (!modal) return;
  const it = id ? researchState.items.find(x => x.id === id) : null;
  document.getElementById('researchModalTitle').textContent = it ? '编辑选品' : '加入选品池';
  document.getElementById('rf-id').value = it ? it.id : '';
  document.getElementById('rf-name').value = it ? it.product_name : '';
  document.getElementById('rf-kind').value = it ? it.kind : 'reference_product';
  document.getElementById('rf-url').value = it ? it.source_url : '';
  document.getElementById('rf-price').value = it?.suggested_price ?? '';
  renderResearchOwnerOptions(it?.owner_ids || []);
  document.getElementById('rf-notes').value = it ? it.notes : '';
  modal.style.display = 'flex';
}

function closeResearchModal() {
  const modal = document.getElementById('researchModal');
  if (modal) modal.style.display = 'none';
}

async function saveResearch() {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  const id = document.getElementById('rf-id').value;
  const ownerSelect = document.getElementById('rf-owner');
  const ownerIds = [...(ownerSelect?.selectedOptions || [])].map(option => Number(option.value));
  const ownerNames = ownerIds.map(ownerId => {
    const user = researchState.users.find(item => item.id === ownerId);
    return user?.nickname || user?.username;
  }).filter(Boolean);
  const data = {
    product_name: document.getElementById('rf-name').value.trim(),
    kind: document.getElementById('rf-kind').value,
    source_url: document.getElementById('rf-url').value.trim(),
    suggested_price: document.getElementById('rf-price').value ? Number(document.getElementById('rf-price').value) : null,
    owner_ids: ownerIds,
    owner_name: ownerNames.join('、') || '未分配',
    notes: document.getElementById('rf-notes').value,
  };
  if (!data.product_name) { alert('请填写商品/店铺名称'); return; }
  const resp = id
    ? await API.put('/api/product-research/' + id, data)
    : await API.post('/api/product-research', data);
  if (resp && resp.code === 200) {
    closeResearchModal();
    refreshResearchView();
  } else {
    alert(resp?.msg || '保存失败');
  }
}

async function copyResearch(id) {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  const resp = await API.post(`/api/product-research/${id}/copy`, {});
  if (resp && resp.code === 200) refreshResearchView();
  else alert(resp?.msg || '复制失败');
}

async function deleteResearch(id) {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  if (!confirm('确定删除这条选品记录？')) return;
  const resp = await API.del('/api/product-research/' + id);
  if (resp && resp.code === 200) refreshResearchView();
  else alert(resp?.msg || '删除失败');
}

// 路由自注册
Router.register('/research', (route) => {
  const html = renderResearchPage();
  setTimeout(() => initResearchPage(), 50);
  return html;
});
