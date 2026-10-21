/**
 * 选品池页 - Research Pool Page
 * 从老 ERP 迁移：参考商品/标杆店铺调研、MPStats 量化指标、蓝海评分
 */
let researchState = {
  kind: '',
  status: '',
  keyword: '',
  items: [],
  counts: {},
};

const RESEARCH_STATUSES = ['待调研', '调研中', '已立项', '已放弃'];
const STATUS_COLORS = { '待调研': '#6b7280', '调研中': '#2563eb', '已立项': '#16a34a', '已放弃': '#dc2626' };

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
          <p class="online-page-desc">参考商品与标杆店铺调研台账，MPStats 量化指标 + 蓝海评分（从精铺助手迁移）</p>
        </div>
        <div>
          ${isAdmin ? `
          <button class="btn btn-primary btn-sm" onclick="openResearchModal()">
            <i data-lucide="plus" style="width:14px;height:14px;"></i> 加入选品
          </button>` : `<span style="font-size:12px;color:var(--text-tertiary);">仅管理员可维护选品池</span>`}
        </div>
      </div>

      <div class="batch-action-bar">
        <div class="batch-action-left">
          <select id="researchKind" class="form-select" style="width:auto;height:34px;font-size:13px;"
                  onchange="researchState.kind=this.value;refreshResearchView()">
            <option value="">全部类型</option>
            <option value="reference_product">参考商品</option>
            <option value="benchmark_store">标杆店铺</option>
          </select>
          <select id="researchStatus" class="form-select" style="width:auto;height:34px;font-size:13px;"
                  onchange="researchState.status=this.value;refreshResearchView()">
            <option value="">全部状态</option>
            ${RESEARCH_STATUSES.map(s => `<option value="${s}">${s}<span id="rs-cnt-${s}"></span></option>`).join('')}
          </select>
        </div>
        <div class="batch-action-right">
          <input type="text" id="researchKeyword" class="form-input" placeholder="搜索名称/备注/链接"
                 style="width:220px;height:34px;font-size:13px;"
                 onkeydown="if(event.key==='Enter'){researchState.keyword=this.value.trim();refreshResearchView();}">
        </div>
      </div>

      <div class="card" style="padding:0;overflow:hidden;">
        <table class="table" style="width:100%;font-size:13px;">
          <thead>
            <tr>
              <th style="min-width:180px;">商品 / 店铺</th>
              <th>类型</th>
              <th>状态</th>
              <th title="月销售额₽">月销</th>
              <th title="增速%">增速</th>
              <th title="Top3品牌占比%">Top3</th>
              <th title="卖家数">卖家</th>
              <th title="蓝海评分 0-100">蓝海分</th>
              <th>负责人</th>
              <th style="min-width:120px;">操作</th>
            </tr>
          </thead>
          <tbody id="researchTableBody">
            <tr><td colspan="10" style="text-align:center;padding:40px;">加载中...</td></tr>
          </tbody>
        </table>
      </div>
      <div id="researchInfo" style="margin-top:8px;font-size:12px;color:var(--text-tertiary);"></div>
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
            <label class="form-label">状态
              <select id="rf-status" class="form-select">
                ${RESEARCH_STATUSES.map(s => `<option value="${s}">${s}</option>`).join('')}
              </select></label>
            <label class="form-label" style="grid-column:1/3;">来源链接
              <input id="rf-url" class="form-input" placeholder="https://www.ozon.ru/product/..."></label>
            <label class="form-label">建议售价 ₽<input id="rf-price" class="form-input" type="number"></label>
            <label class="form-label">负责人<input id="rf-owner" class="form-input"></label>
          </div>
          <div style="margin:12px 0 6px;font-weight:600;font-size:13px;">MPStats 量化指标（可截图识别自动填充）</div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;">
            <label class="form-label" style="font-size:12px;">月销₽<input id="rf-rev" class="form-input" type="number"></label>
            <label class="form-label" style="font-size:12px;">增速%<input id="rf-growth" class="form-input" type="number"></label>
            <label class="form-label" style="font-size:12px;">Top3%<input id="rf-top3" class="form-input" type="number"></label>
            <label class="form-label" style="font-size:12px;">卖家数<input id="rf-sellers" class="form-input" type="number"></label>
            <label class="form-label" style="font-size:12px;">品牌数<input id="rf-brands" class="form-input" type="number"></label>
          </div>
          <div style="margin:8px 0;">
            <label class="btn btn-xs" style="cursor:pointer;">
              📷 上传 MPStats 截图识别指标
              <input type="file" accept="image/*" style="display:none;" onchange="extractMpstatsScreenshot(this)">
            </label>
            <span id="rf-extract-msg" style="font-size:12px;color:var(--text-tertiary);margin-left:8px;"></span>
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
  await refreshResearchView();
}

async function refreshResearchView() {
  const body = document.getElementById('researchTableBody');
  if (body) body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;">加载中...</td></tr>';
  const resp = await API.get('/api/product-research', {
    kind: researchState.kind, status: researchState.status, keyword: researchState.keyword,
  });
  if (resp && resp.code === 200) {
    researchState.items = resp.data || [];
    renderResearchTable();
  } else {
    if (body) body.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--danger);">${resp?.msg || '加载失败'}</td></tr>`;
  }
  const stats = await API.get('/api/product-research/stats');
  if (stats && stats.data) {
    researchState.counts = stats.data.counts || {};
    Object.entries(researchState.counts).forEach(([s, c]) => {
      const el = document.getElementById('rs-cnt-' + s);
      if (el) el.textContent = ` (${c})`;
    });
    const info = document.getElementById('researchInfo');
    if (info) info.textContent = `共 ${stats.data.total} 条`;
  }
}

function _fmtNum(v, suffix = '') {
  if (v === null || v === undefined || v === '') return '-';
  const n = Number(v);
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M' + suffix;
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K' + suffix;
  return n + suffix;
}

function _scoreColor(score) {
  if (score === null || score === undefined) return 'var(--text-tertiary)';
  if (score >= 70) return '#16a34a';
  if (score >= 50) return '#d97706';
  return '#dc2626';
}

function renderResearchTable() {
  const body = document.getElementById('researchTableBody');
  if (!body) return;
  const isAdmin = _researchIsAdmin();
  if (!researchState.items.length) {
    body.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:40px;color:var(--text-tertiary);">${isAdmin ? '选品池为空，点击右上角「加入选品」开始' : '选品池暂无数据'}</td></tr>`;
    return;
  }
  body.innerHTML = researchState.items.map(it => {
    const statusColor = STATUS_COLORS[it.status] || '#6b7280';
    return `<tr>
      <td>
        <div style="font-weight:600;">${it.product_name}</div>
        ${it.source_url ? `<a href="${it.source_url}" target="_blank" style="font-size:11px;color:var(--primary);">查看来源 ↗</a>` : ''}
      </td>
      <td>${it.kind === 'benchmark_store' ? '标杆店铺' : '参考商品'}</td>
      <td><span style="color:${statusColor};font-weight:600;">${it.status}</span></td>
      <td>${_fmtNum(it.monthly_revenue)}</td>
      <td>${it.revenue_growth_rate != null ? it.revenue_growth_rate + '%' : '-'}</td>
      <td>${it.top3_brand_share != null ? it.top3_brand_share + '%' : '-'}</td>
      <td>${it.seller_count ?? '-'}</td>
      <td style="font-weight:700;color:${_scoreColor(it.blue_ocean_score)};">${it.blue_ocean_score ?? '-'}</td>
      <td>${it.owner_name || '-'}</td>
      <td>
        ${isAdmin ? `
        <button class="btn btn-xs" onclick="openResearchModal(${it.id})">编辑</button>
        <button class="btn btn-xs" onclick="copyResearch(${it.id})">复制</button>
        <button class="btn btn-xs" style="color:var(--danger);" onclick="deleteResearch(${it.id})">删除</button>
        ` : '<span style="color:var(--text-tertiary);">-</span>'}
      </td>
    </tr>`;
  }).join('');
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
  document.getElementById('rf-status').value = it ? it.status : '待调研';
  document.getElementById('rf-url').value = it ? it.source_url : '';
  document.getElementById('rf-price').value = it?.suggested_price ?? '';
  document.getElementById('rf-owner').value = it?.owner_name && it.owner_name !== '未分配' ? it.owner_name : '';
  document.getElementById('rf-rev').value = it?.monthly_revenue ?? '';
  document.getElementById('rf-growth').value = it?.revenue_growth_rate ?? '';
  document.getElementById('rf-top3').value = it?.top3_brand_share ?? '';
  document.getElementById('rf-sellers').value = it?.seller_count ?? '';
  document.getElementById('rf-brands').value = it?.brand_count ?? '';
  document.getElementById('rf-notes').value = it ? it.notes : '';
  document.getElementById('rf-extract-msg').textContent = '';
  modal.style.display = 'flex';
}

function closeResearchModal() {
  const modal = document.getElementById('researchModal');
  if (modal) modal.style.display = 'none';
}

async function saveResearch() {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  const id = document.getElementById('rf-id').value;
  const data = {
    product_name: document.getElementById('rf-name').value.trim(),
    kind: document.getElementById('rf-kind').value,
    status: document.getElementById('rf-status').value,
    source_url: document.getElementById('rf-url').value.trim(),
    suggested_price: document.getElementById('rf-price').value ? Number(document.getElementById('rf-price').value) : null,
    owner_name: document.getElementById('rf-owner').value.trim() || '未分配',
    monthly_revenue: document.getElementById('rf-rev').value ? Number(document.getElementById('rf-rev').value) : null,
    revenue_growth_rate: document.getElementById('rf-growth').value ? Number(document.getElementById('rf-growth').value) : null,
    top3_brand_share: document.getElementById('rf-top3').value ? Number(document.getElementById('rf-top3').value) : null,
    seller_count: document.getElementById('rf-sellers').value ? Number(document.getElementById('rf-sellers').value) : null,
    brand_count: document.getElementById('rf-brands').value ? Number(document.getElementById('rf-brands').value) : null,
    notes: document.getElementById('rf-notes').value,
    mpstats_source: document.getElementById('rf-rev').value ? 'screenshot_or_manual' : 'manual',
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

async function extractMpstatsScreenshot(input) {
  if (!_researchIsAdmin()) { Toast.show('仅管理员可维护选品池', 'warning'); return; }
  const file = input.files && input.files[0];
  if (!file) return;
  const msg = document.getElementById('rf-extract-msg');
  msg.textContent = '识别中，请稍候...';
  const reader = new FileReader();
  reader.onload = async () => {
    const resp = await API.post('/api/product-research/extract-mpstats', { image: reader.result });
    if (resp && resp.code === 200 && resp.data && resp.data.detected) {
      const d = resp.data;
      const set = (id, v) => { if (v != null) document.getElementById(id).value = v; };
      set('rf-rev', d.monthly_revenue);
      set('rf-growth', d.revenue_growth_rate);
      set('rf-top3', d.top3_brand_share);
      set('rf-sellers', d.seller_count);
      set('rf-brands', d.brand_count);
      msg.textContent = '✅ 已识别并填充指标，请核对';
      msg.style.color = '#16a34a';
    } else {
      msg.textContent = resp?.msg || '截图中未识别到量化指标，请手动填写';
      msg.style.color = '#d97706';
    }
  };
  reader.readAsDataURL(file);
  input.value = '';
}

// 路由自注册
Router.register('/research', (route) => {
  const html = renderResearchPage();
  setTimeout(() => initResearchPage(), 50);
  return html;
});
