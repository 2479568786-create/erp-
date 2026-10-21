/**
 * 我的选品页 - My Selection Page
 * 每个账号独立维护自己的选品台账（账号隔离）；管理员可查看/操作所有账号的选品
 * 字段：产品名称、采购链接、贴单费、购买成本；日常上品自动带入产品名称，其余人工补充
 */
let mySelState = {
  keyword: '',
  items: [],
};

function _mySelIsAdmin() {
  return Store?.getUser?.()?.role === 'admin';
}
function _mySelColspan() {
  return _mySelIsAdmin() ? 8 : 7;
}

function renderMySelectionPage() {
  const isAdmin = _mySelIsAdmin();
  const colspan = _mySelColspan();
  return `
    <div style="animation: pageEnter 0.35s ease;">
      <div class="online-page-header">
        <div>
          <h2 class="online-page-title">我的选品</h2>
          <p class="online-page-desc">${isAdmin ? '管理员视图：可查看并管理所有账号的选品台账' : '每个账号独立维护自己的选品台账；日常上品会自动带入产品名称，采购链接、贴单费、购买成本需人工补充'}</p>
        </div>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-sm" style="color:var(--danger);" onclick="batchDeleteMySelection()">
            <i data-lucide="trash-2" style="width:14px;height:14px;"></i> 批量删除
          </button>
          <button class="btn btn-primary btn-sm" onclick="openMySelectionModal()">
            <i data-lucide="plus" style="width:14px;height:14px;"></i> 添加选品
          </button>
        </div>
      </div>

      <div class="batch-action-bar">
        <div class="batch-action-right">
          <input type="text" id="mySelKeyword" class="form-input" placeholder="搜索名称/链接"
                 style="width:240px;height:34px;font-size:13px;"
                 onkeydown="if(event.key==='Enter'){mySelState.keyword=this.value.trim();refreshMySelectionView();}">
        </div>
      </div>

      <div class="card" style="padding:0;overflow:hidden;">
        <table class="table" style="width:100%;font-size:13px;">
          <thead>
            <tr>
              <th style="width:36px;"><input type="checkbox" onclick="toggleAllMySelection(this)" title="全选"></th>
              <th style="min-width:180px;">产品名称</th>
              <th style="min-width:220px;">采购链接</th>
              <th>贴单费</th>
              <th>购买成本</th>
              ${isAdmin ? '<th>所属账号</th>' : ''}
              <th>来源</th>
              <th style="min-width:120px;">操作</th>
            </tr>
          </thead>
          <tbody id="mySelectionTableBody">
            <tr><td colspan="${colspan}" style="text-align:center;padding:40px;">加载中...</td></tr>
          </tbody>
        </table>
      </div>
      <div id="mySelectionInfo" style="margin-top:8px;font-size:12px;color:var(--text-tertiary);"></div>
    </div>

    <div id="mySelectionModal" class="modal-overlay" style="display:none;">
      <div class="modal-dialog" style="max-width:560px;">
        <div class="modal-header">
          <span class="modal-title" id="mySelectionModalTitle">添加选品</span>
          <button class="modal-close" onclick="closeMySelectionModal()">×</button>
        </div>
        <div class="modal-body" style="padding:16px 20px;">
          <input type="hidden" id="ms-id">
          <label class="form-label">产品名称 *
            <input id="ms-name" class="form-input" placeholder="例如：XX 无线鼠标"></label>
          <label class="form-label">采购链接
            <input id="ms-url" class="form-input" placeholder="https://detail.1688.com/..."></label>
          <label class="form-label">贴单费
            <input id="ms-fee" class="form-input" type="number" placeholder="0.00"></label>
          <label class="form-label">购买成本
            <input id="ms-cost" class="form-input" type="number" placeholder="0.00"></label>
        </div>
        <div class="modal-footer" style="padding:12px 20px;">
          <button class="btn btn-sm" onclick="closeMySelectionModal()">取消</button>
          <button class="btn btn-primary btn-sm" onclick="saveMySelection()">保存</button>
        </div>
      </div>
    </div>
  `;
}

async function initMySelectionPage() {
  await refreshMySelectionView();
}

async function refreshMySelectionView() {
  const body = document.getElementById('mySelectionTableBody');
  const colspan = _mySelColspan();
  if (body) body.innerHTML = `<tr><td colspan="${colspan}" style="text-align:center;padding:40px;">加载中...</td></tr>`;
  const resp = await API.get('/api/my-selections', { keyword: mySelState.keyword });
  if (resp && resp.code === 200) {
    mySelState.items = resp.data || [];
    renderMySelectionTable();
  } else {
    if (body) body.innerHTML = `<tr><td colspan="${colspan}" style="text-align:center;padding:40px;color:var(--danger);">${resp?.msg || '加载失败'}</td></tr>`;
  }
  const info = document.getElementById('mySelectionInfo');
  if (info) info.textContent = `共 ${mySelState.items.length} 条`;
}

function renderMySelectionTable() {
  const body = document.getElementById('mySelectionTableBody');
  if (!body) return;
  const isAdmin = _mySelIsAdmin();
  const colspan = _mySelColspan();
  if (!mySelState.items.length) {
    body.innerHTML = `<tr><td colspan="${colspan}" style="text-align:center;padding:40px;color:var(--text-tertiary);">还没有选品，点右上角「添加选品」或去采集箱上品自动带入</td></tr>`;
    return;
  }
  body.innerHTML = mySelState.items.map(it => {
    const fee = (it.sticker_fee != null && it.sticker_fee !== '') ? Number(it.sticker_fee) : 0;
    const cost = (it.purchase_cost != null && it.purchase_cost !== '') ? Number(it.purchase_cost) : 0;
    const owner = it.owner_name || it.owner_username || '-';
    return `<tr>
      <td><input type="checkbox" class="my-sel-check" value="${it.id}"></td>
      <td style="font-weight:600;">${it.product_name}</td>
      <td>${it.purchase_url ? `<a href="${it.purchase_url}" target="_blank" style="color:var(--primary);">${it.purchase_url}</a>` : '<span style="color:var(--text-tertiary);">待填写</span>'}</td>
      <td>${fee ? fee.toFixed(2) : '<span style="color:var(--text-tertiary);">待填写</span>'}</td>
      <td>${cost ? cost.toFixed(2) : '<span style="color:var(--text-tertiary);">待填写</span>'}</td>
      ${isAdmin ? `<td>${owner}</td>` : ''}
      <td>${it.auto_filled ? '<span style="color:var(--primary);">日常上品</span>' : '<span style="color:var(--text-tertiary);">手动</span>'}</td>
      <td>
        <button class="btn btn-xs" onclick="openMySelectionModal(${it.id})">编辑</button>
        <button class="btn btn-xs" style="color:var(--danger);" onclick="deleteMySelection(${it.id})">删除</button>
      </td>
    </tr>`;
  }).join('');
}

function toggleAllMySelection(el) {
  document.querySelectorAll('.my-sel-check').forEach(c => { c.checked = el.checked; });
}

async function batchDeleteMySelection() {
  const ids = [...document.querySelectorAll('.my-sel-check:checked')].map(c => c.value);
  if (!ids.length) { Toast.show('请先勾选要删除的条目', 'warning'); return; }
  if (!confirm('确定删除选中的 ' + ids.length + ' 条选品记录？')) return;
  const resp = await API.post('/api/my-selections/batch-delete', { ids });
  if (resp && resp.code === 200) {
    refreshMySelectionView();
    Toast.show('已删除 ' + ((resp.data && resp.data.deleted) || ids.length) + ' 条', 'success');
  } else {
    Toast.show(resp?.msg || '删除失败', 'error');
  }
}

function openMySelectionModal(id) {
  const modal = document.getElementById('mySelectionModal');
  if (!modal) return;
  const it = id ? mySelState.items.find(x => x.id === id) : null;
  document.getElementById('mySelectionModalTitle').textContent = it ? '编辑选品' : '添加选品';
  document.getElementById('ms-id').value = it ? it.id : '';
  document.getElementById('ms-name').value = it ? it.product_name : '';
  document.getElementById('ms-url').value = it ? it.purchase_url : '';
  document.getElementById('ms-fee').value = it && it.sticker_fee != null ? it.sticker_fee : '';
  document.getElementById('ms-cost').value = it && it.purchase_cost != null ? it.purchase_cost : '';
  modal.style.display = 'flex';
}

function closeMySelectionModal() {
  const modal = document.getElementById('mySelectionModal');
  if (modal) modal.style.display = 'none';
}

async function saveMySelection() {
  const id = document.getElementById('ms-id').value;
  const data = {
    product_name: document.getElementById('ms-name').value.trim(),
    purchase_url: document.getElementById('ms-url').value.trim(),
    sticker_fee: document.getElementById('ms-fee').value ? Number(document.getElementById('ms-fee').value) : 0,
    purchase_cost: document.getElementById('ms-cost').value ? Number(document.getElementById('ms-cost').value) : 0,
  };
  if (!data.product_name) { Toast.show('请填写产品名称', 'warning'); return; }
  const resp = id
    ? await API.put('/api/my-selections/' + id, data)
    : await API.post('/api/my-selections', data);
  if (resp && resp.code === 200) {
    closeMySelectionModal();
    refreshMySelectionView();
    Toast.show('保存成功', 'success');
  } else {
    Toast.show(resp?.msg || '保存失败', 'error');
  }
}

async function deleteMySelection(id) {
  if (!confirm('确定删除这条选品记录？')) return;
  const resp = await API.del('/api/my-selections/' + id);
  if (resp && resp.code === 200) {
    refreshMySelectionView();
    Toast.show('已删除', 'success');
  } else {
    Toast.show(resp?.msg || '删除失败', 'error');
  }
}

// 路由自注册
Router.register('/selection', (route) => {
  const html = renderMySelectionPage();
  setTimeout(() => initMySelectionPage(), 50);
  return html;
});
