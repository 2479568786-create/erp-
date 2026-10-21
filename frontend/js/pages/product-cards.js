/**
 * 商品卡页面（AI 自动生成 + 手动填写双轨制）
 * - 商品下拉 -> 填写核心卖点 -> 「AI 生成」一次完成（三步工作流）
 * - 上架取值：当前生效 = latest EDITED > latest GENERATED > 商品原字段
 * - 版本时间线：徽标 AI 生成/人工编辑 + 生效中；支持编辑 / 重新生成 / 采纳 / 删除
 * - 接口：Api.request（支持自定义 timeout，AI 生成需 300s）
 */
let pcState = {
  productId: '',
  products: [],
  versions: [],
  current: null,
  selectedCard: null,
  loading: false,
  page: 1,
};

function renderProductCardsPage() {
  return `
  <div style="animation: pageEnter 0.35s ease;">
    <div class="online-page-header">
      <div>
        <h2 class="online-page-title">商品卡</h2>
        <p class="online-page-desc">AI 自动生成中俄双语商品卡（标题/描述/关键词/标签），人工可随时编辑采纳。上架时优先采用「人工编辑」版本。</p>
      </div>
      <button class="btn btn-xs btn-primary" id="pcRefreshBtn" onclick="pcLoadVersions()">
        <i data-lucide="refresh-cw" style="width:14px;height:14px;"></i> 刷新
      </button>
    </div>

    <!-- 商品选择 -->
    <div class="card" style="padding:16px;margin-top:12px;">
      <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;">
        <div style="flex:1;min-width:220px;">
          <label style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;display:block;">选择商品</label>
          <select id="pcProductSelect" class="form-select" style="width:100%;height:36px;"
                  onchange="pcState.productId=this.value;pcState.page=1;pcOnLoadProduct()"></select>
        </div>
        <div id="pcCurrentBadge"></div>
      </div>
    </div>

    <!-- AI 生成区 / 手动编辑区 -->
    <div class="card" style="padding:16px;margin-top:12px;">
      <div style="display:flex;gap:16px;align-items:center;margin-bottom:8px;">
        <div style="font-weight:600;">AI 生成</div>
        <span style="font-size:12px;color:var(--text-secondary);">按钮触发 • 生成新增版本不覆盖人工编辑</span>
      </div>
      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;">
        <div style="flex:2;min-width:280px;">
          <label style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;display:block;">核心卖点（每行一条）</label>
          <textarea id="pcSellingPoints" class="form-textarea" rows="4"
                    placeholder="例：高品质不锈钢刀身&#10;防腐蚀久耐用&#10;适合家庭烹饭"></textarea>
        </div>
        <div>
          <button class="btn btn-primary" id="pcGenerateBtn" onclick="pcGenerateCard()" style="min-width:110px;">
            <i data-lucide="sparkles" style="width:16px;height:16px;"></i> 生成商品卡
          </button>
        </div>
      </div>
    </div>

    <!-- 版本时间轴 + 预览 -->
    <div class="card" style="padding:16px;margin-top:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <div style="font-weight:600;">版本列表</div>
        <button class="btn btn-xs btn-ghost" onclick="pcOpenManualCard()">
          <i data-lucide="plus" style="width:14px;height:14px;"></i> 手动新建
        </button>
      </div>
      <div id="pcVersionsBody" style="text-align:center;padding:30px;color:var(--text-tertiary);">
        请选择商品查看版本
      </div>
    </div>

    <div class="card" style="padding:16px;margin-top:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <div style="font-weight:600;">预览</div>
        <span id="pcPreviewHint" style="font-size:12px;color:var(--text-secondary);"></span>
      </div>
      <div id="pcPreviewBody" style="color:var(--text-tertiary);">
        点击任一版本预览
      </div>
    </div>
  </div>
  `;
}

async function initProductCardsPage() {
  await pcLoadProducts();
  // 选商品后绑定核心卖点的自动保存
  bindPcSellingPoints();
  setTimeout(() => { if (pcState.productId) pcOnLoadProduct(); }, 50);
}

/** 核心卖点自动保存（per-product 隔离） */
let _pcPointsCleanup = null;
function bindPcSellingPoints() {
  if (_pcPointsCleanup) { _pcPointsCleanup(); _pcPointsCleanup = null; }
  const ta = document.getElementById('pcSellingPoints');
  if (!ta || !window.AutoSave) return;
  // 先拉回该商品的草稿（如果有）
  const tryRestore = () => {
    if (!pcState.productId) return;
    window.AutoSave.restore({
      scope: 'product_card',
      key: 'pc_points_' + pcState.productId,
      fillElements: (payload) => {
        if (payload && typeof payload.value === 'string') {
          ta.value = payload.value;
          if (window.Toast) window.Toast.show('已恢复上次的核心卖点', 'info', 1200);
        }
      },
    });
  };
  tryRestore();
  _pcPointsCleanup = window.AutoSave.bind(ta, {
    scope: 'product_card',
    key: () => pcState.productId ? 'pc_points_' + pcState.productId : null,
    debounceMs: 800,
    meta: { page: 'product-cards', kind: 'selling_points', productId: pcState.productId },
    computePayload: (els) => ({ value: els[0].value }),
    onSaved: () => { if (window.Toast) window.Toast.show('核心卖点已自动保存', 'info', 800); },
  });
}

async function pcLoadProducts() {
  try {
    const resp = await API.get('/api/products', { page: 1, pageSize: 200 });
    const list = (resp && resp.data && (Array.isArray(resp.data) ? resp.data : resp.data.list)) || [];
    pcState.products = list;
    const sel = document.getElementById('pcProductSelect');
    if (sel) {
      sel.innerHTML = '<option value="">-- 请选择商品 --</option>' +
        list.map(p => `<option value="${p.id}">${p.title || p.sourceName || '商品 ' + p.id}</option>`).join('');
    }
  } catch (e) {
    window.Toast && Toast.show('加载商品失败: ' + (e.message || e), 'error');
  }
}

async function pcOnLoadProduct() {
  const container = document.getElementById('pcCurrentBadge');
  const preview = document.getElementById('pcPreviewBody');
  if (!pcState.productId) {
    container.innerHTML = '';
    preview.innerHTML = '<div style="color:var(--text-tertiary);">请选择商品</div>';
    return;
  }
  container.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);">加载中...</div>';
  pcState.versions = [];
  pcState.current = null;
  pcState.selectedCard = null;
  // 切商品：重新绑定核心卖点（per-product 草稿）
  bindPcSellingPoints();
  try {
    const resp = await API.get('/api/product-cards/current', { product_id: pcState.productId });
    if (resp && resp.code === 200) {
      pcState.current = resp.data;
      renderCurrentBadge();
    } else if (resp && resp.code === 404) {
      pcState.current = null;
    }
  } catch (e) { /* ignore */ }
  await pcLoadVersions();
}

function renderCurrentBadge() {
  const el = document.getElementById('pcCurrentBadge');
  if (!el) return;
  if (!pcState.current) {
    el.innerHTML = '<span style="font-size:12px;color:#d97706;">暂无生效卡，上架将使用商品原字段</span>';
    return;
  }
  const badge = pcState.current.source === 'manual' ? '人工编辑' : 'AI 生成';
  el.innerHTML = `<span style="font-size:12px;color:#05603a;">
    <i data-lucide="check" style="width:13px;height:13px;vertical-align:middle;"></i>
    生效中：v${pcState.current.version} · ${badge}
  </span>`;
  if (window.lucide) lucide.createIcons();
}

async function pcLoadVersions() {
  const body = document.getElementById('pcVersionsBody');
  if (!pcState.productId) { body.innerHTML = ''; return; }
  body.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-tertiary);">加载中...</div>';
  try {
    const resp = await API.get('/api/product-cards', { product_id: pcState.productId, page: pcState.page, page_size: 20 });
    const list = (resp && resp.data && resp.data.list) || [];
    pcState.versions = list;
    renderVersions();
    if (window.lucide) lucide.createIcons();
    // 默认预览最新版本
    if (list.length && !pcState.selectedCard) pcSelectCard(list[0]);
  } catch (e) {
    body.innerHTML = '<div style="color:#d92d20;padding:16px;">加载失败: ' + (e.message || e) + '</div>';
  }
}

function renderVersions() {
  const body = document.getElementById('pcVersionsBody');
  if (!body) return;
  if (!pcState.versions.length) {
    body.innerHTML = '<div style="color:var(--text-tertiary);">暂无版本，点击「AI 生成」或「手动新建」开始</div>';
    return;
  }
  body.innerHTML = pcState.versions.map(c => {
    const badge = c.source === 'manual' ? '#3cb371' : (c.source === 'ai' && c.status === 'EDITED' ? '#6366f1' : '#6b7280');
    const label = c.source === 'manual' ? '人工' : (c.status === 'EDITED' ? 'AI(已编辑)' : 'AI');
    const isCurrent = c.isCurrent;
    return `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;cursor:pointer;border-left:3px solid ${isCurrent?'#2563eb':'#e5e7eb'};background:${isCurrent?'#eff6ff':'#fff'}"
           onclick="pcSelectCard(${c.id})">
        <div style="flex:1;">
          <div style="font-size:13px;font-weight:600;">v${c.version} · ${label}
             <span style="font-size:11px;color:#9ca3af;">${c.model ? ('(' + c.model + ')') : ''}</span>
          </div>
          <div style="font-size:11px;color:#9ca3af;">${c.title_ru ? c.title_ru.slice(0, 40) + (c.title_ru.length > 40 ? '…' : '') : '（未填写标题）'}</div>
        </div>
        <span style="width:8px;height:8px;border-radius:50%;background:${badge};" title="${label}"></span>
        ${isCurrent ? '<span style="font-size:10px;color:#2563eb;">生效中</span>' : ''}
      </div>`.trim().replace(/\n\s+/g, ' ');
  }).join('');
}

function pcSelectCard(id) {
  pcState.selectedCard = pcState.versions.find(v => v.id === id) || null;
  renderPreview();
  if (window.lucide) lucide.createIcons();
}

function renderPreview() {
  const hint = document.getElementById('pcPreviewHint');
  const body = document.getElementById('pcPreviewBody');
  if (!body) return;
  const c = pcState.selectedCard;
  if (!c) { body.innerHTML = '<div style="color:var(--text-tertiary);">点击任一版本预览</div>'; return; }
  hint.textContent = `v${c.version} · ${c.source === 'manual' ? '人工编辑' : (c.status === 'EDITED' ? 'AI 生成（已编辑）' : 'AI 生成')}`;
  const kws = Array.isArray(c.keywords_ru) ? c.keywords_ru.join('，') : '';
  const tags = (Array.isArray(c.main_tags_ru) ? c.main_tags_ru : [])
    .concat(Array.isArray(c.long_tail_tags_ru) ? c.long_tail_tags_ru : []).join(' ');
  const ops = c.isCurrent
    ? `<button class="btn btn-xs btn-ghost" onclick="pcOpenEditCard(${c.id})"><i data-lucide="pencil" style="width:13px;height:13px;"></i> 编辑</button>`
    : `<button class="btn btn-xs btn-primary" onclick="pcOpenEditCard(${c.id})" style="margin-right:6px;"><i data-lucide="pencil" style="width:13px;height:13px;"></i> 编辑采纳</button>`;
  body.innerHTML = `
    <div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px;">
      <div style="margin-bottom:8px;">
        <div style="font-size:12px;color:var(--text-secondary);">俄语标题</div>
        <div style="font-size:14px;font-weight:600;">${escapeHtml(c.title_ru || '（未填写）')}</div>
      </div>
      <div style="margin-bottom:8px;">
        <div style="font-size:12px;color:var(--text-secondary);">中文标题</div>
        <div style="font-size:13px;">${escapeHtml(c.title_zh || '（未填写）')}</div>
      </div>
      <div style="margin-bottom:8px;">
        <div style="font-size:12px;color:var(--text-secondary);">俄语描述</div>
        <div style="font-size:13px;white-space:pre-wrap;color:#374151;">${escapeHtml(c.description_ru || '（未填写）')}</div>
      </div>
      <div style="margin-bottom:8px;">
        <div style="font-size:12px;color:var(--text-secondary);">SEO 关键词</div>
        <div style="font-size:12px;color:#4b5563;">${escapeHtml(kws)}</div>
      </div>
      <div style="margin-bottom:8px;">
        <div style="font-size:12px;color:var(--text-secondary);">主题标签</div>
        <div style="font-size:12px;color:#4b5563;">${escapeHtml(tags)}</div>
      </div>
      <div style="display:flex;gap:8px;padding-top:8px;border-top:1px solid #e5e7eb;">
        ${ops}
        <button class="btn btn-xs btn-ghost" onclick="pcRegenerateCard(${c.id})"><i data-lucide="refresh-cw" style="width:13px;height:13px;"></i> 重新生成</button>
        <button class="btn btn-xs btn-ghost" onclick="pcActivateCard(${c.id})"><i data-lucide="check-circle" style="width:13px;height:13px;"></i> 采纳</button>
        <button class="btn btn-xs btn-ghost" onclick="pcDeleteCard(${c.id})"><i data-lucide="trash-2" style="width:13px;height:13px;"></i> 删除</button>
      </div>
    </div>
  `;
  if (window.lucide) lucide.createIcons();
}

function pcOpenEditCard(id) {
  const c = pcState.versions.find(v => v.id === id) || pcState.selectedCard;
  if (!c) return;
  const kws = Array.isArray(c.keywords_ru) ? c.keywords_ru.join('，') : '';
  const tags = (Array.isArray(c.main_tags_ru) ? c.main_tags_ru.join('，') : '');
  const longTail = (Array.isArray(c.long_tail_tags_ru) ? c.long_tail_tags_ru.join('，') : '');
  // 当前草稿 key：per-card 维度
  const draftKey = 'pc_edit_' + c.id;
  Modal.show({
    title: `编辑商品卡 v${c.version}`,
    size: 'lg',
    body: `
      <div style="display:grid;gap:12px;">
        <div><label style="font-size:12px;color:var(--text-secondary);">俄语标题（≤130）</label>
          <input id="mTitleRu" class="form-input" style="width:100%;" value="${escapeHtmlAttr(c.title_ru || '')}" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">中文标题</label>
          <input id="mTitleZh" class="form-input" style="width:100%;" value="${escapeHtmlAttr(c.title_zh || '')}" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">俄语描述</label>
          <textarea id="mDescRu" class="form-textarea" rows="5" style="width:100%;">${escapeHtml(c.description_ru || '')}</textarea></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">中文描述</label>
          <textarea id="mDescZh" class="form-textarea" rows="3" style="width:100%;">${escapeHtml(c.description_zh || '')}</textarea></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">SEO 关键词（逗号分隔）</label>
          <input id="mKeywords" class="form-input" style="width:100%;" value="${escapeHtmlAttr(kws)}" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">主标签（逗号分隔）</label>
          <input id="mMainTags" class="form-input" style="width:100%;" value="${escapeHtmlAttr(tags)}" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">长尾词（逗号分隔）</label>
          <input id="mLongTail" class="form-input" style="width:100%;" value="${escapeHtmlAttr(longTail)}" /></div>
        <div id="pcEditDraftHint" style="font-size:11px;color:#94a3b8;margin-top:6px;">已加载 v${c.version} 原始值，编辑过程将自动保存为草稿。</div>
      </div>`,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => { _pcEditCleanups.forEach(fn => fn()); _pcEditCleanups = []; Modal.close(); } },
      { text: '保存', class: 'btn-primary', onClick: () => pcSaveEdit(c.id, draftKey) },
    ],
    onOpen: () => {
      // 拉回草稿（如果有）覆盖当前值
      if (window.AutoSave) {
        window.AutoSave.restore({
          scope: 'product_card',
          key: draftKey,
          fillElements: (payload) => {
            if (payload && payload.values) {
              const ids = ['mTitleRu','mTitleZh','mDescRu','mDescZh','mKeywords','mMainTags','mLongTail'];
              ids.forEach((id, i) => {
                const el = document.getElementById(id);
                if (el && payload.values[i] !== undefined) el.value = payload.values[i];
              });
              const hint = document.getElementById('pcEditDraftHint');
              if (hint) hint.textContent = '已恢复上次未提交的编辑草稿，继续即可。';
            }
          }
        }).then(() => {
          // 绑定自动保存（拉回后再 bind，否则首次 put 会覆盖拉回的内容）
          _pcBindModalFields('mTitleRu,mTitleZh,mDescRu,mDescZh,mKeywords,mMainTags,mLongTail', draftKey, '编辑草稿');
        });
      }
    },
  });
}

// 弹窗字段自动保存的 cleanup 句柄栈
let _pcEditCleanups = [];
let _pcManualCleanups = [];

/** 绑定弹窗内字段到 AutoSave */
function _pcBindModalFields(ids, draftKey, hint) {
  _pcEditCleanups.forEach(fn => fn());
  _pcEditCleanups = [];
  const els = ids.split(',').map(id => document.getElementById(id)).filter(Boolean);
  if (!els.length || !window.AutoSave) return;
  const cleanup = window.AutoSave.bind(els, {
    scope: 'product_card',
    key: draftKey,
    debounceMs: 600,
    meta: { page: 'product-cards', kind: 'edit' },
    computePayload: (es) => ({ values: es.map(el => el.value) }),
    onSaved: () => { if (window.Toast) window.Toast.show(hint + '已自动保存', 'info', 800); },
  });
  _pcEditCleanups.push(cleanup);
}

function pcOpenManualCard() {
  if (!pcState.productId) {
    window.Toast && Toast.show('请选择商品后再新建', 'error');
    return;
  }
  // 手动新建草稿 key
  const draftKey = 'pc_manual_' + pcState.productId;
  Modal.show({
    title: '手动新建商品卡',
    size: 'lg',
    body: `
      <div style="display:grid;gap:12px;">
        <div><label style="font-size:12px;color:var(--text-secondary);">俄语标题（≤130）</label>
          <input id="nTitleRu" class="form-input" style="width:100%;" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">中文标题</label>
          <input id="nTitleZh" class="form-input" style="width:100%;" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">俄语描述</label>
          <textarea id="nDescRu" class="form-textarea" rows="5" style="width:100%;"></textarea></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">中文描述</label>
          <textarea id="nDescZh" class="form-textarea" rows="3" style="width:100%;"></textarea></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">SEO 关键词（逗号分隔）</label>
          <input id="nKeywords" class="form-input" style="width:100%;" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">主标签（逗号分隔）</label>
          <input id="nMainTags" class="form-input" style="width:100%;" /></div>
        <div><label style="font-size:12px;color:var(--text-secondary);">长尾词（逗号分隔）</label>
          <input id="nLongTail" class="form-input" style="width:100%;" /></div>
        <div id="pcManualDraftHint" style="font-size:11px;color:#94a3b8;margin-top:6px;">填写过程将自动保存为草稿，刷新或误关可恢复。</div>
      </div>`,
    footer: [
      { text: '取消', class: 'btn-ghost', onClick: () => { _pcManualCleanups.forEach(fn => fn()); _pcManualCleanups = []; Modal.close(); } },
      { text: '保存', class: 'btn-primary', onClick: () => pcSaveManual(draftKey) },
    ],
    onOpen: () => {
      if (window.AutoSave) {
        window.AutoSave.restore({
          scope: 'product_card',
          key: draftKey,
          fillElements: (payload) => {
            if (payload && payload.values) {
              const ids = ['nTitleRu','nTitleZh','nDescRu','nDescZh','nKeywords','nMainTags','nLongTail'];
              ids.forEach((id, i) => {
                const el = document.getElementById(id);
                if (el && payload.values[i] !== undefined) el.value = payload.values[i];
              });
              const hint = document.getElementById('pcManualDraftHint');
              if (hint) hint.textContent = '已恢复上次未提交的手动卡草稿，继续编辑即可。';
            }
          }
        }).then(() => {
          // 拉回后再绑定
          _pcManualCleanups.forEach(fn => fn());
          _pcManualCleanups = [];
          const els = ['nTitleRu','nTitleZh','nDescRu','nDescZh','nKeywords','nMainTags','nLongTail']
            .map(id => document.getElementById(id)).filter(Boolean);
          const cleanup = window.AutoSave.bind(els, {
            scope: 'product_card',
            key: draftKey,
            debounceMs: 600,
            meta: { page: 'product-cards', kind: 'manual', productId: pcState.productId },
            computePayload: (es) => ({ values: es.map(el => el.value) }),
            onSaved: () => { if (window.Toast) window.Toast.show('手动卡草稿已自动保存', 'info', 800); },
          });
          _pcManualCleanups.push(cleanup);
        });
      }
    },
  });
}

function _splitTags(val) {
  return (val || '').split(/[，,]/).map(s => s.trim()).filter(Boolean);
}

async function pcSaveEdit(id, draftKey) {
  const data = {
    title_ru: document.getElementById('mTitleRu').value,
    title_zh: document.getElementById('mTitleZh').value,
    description_ru: document.getElementById('mDescRu').value,
    description_zh: document.getElementById('mDescZh').value,
    keywords_ru: _splitTags(document.getElementById('mKeywords').value),
    main_tags_ru: _splitTags(document.getElementById('mMainTags').value),
    long_tail_tags_ru: _splitTags(document.getElementById('mLongTail').value),
  };
  try {
    const resp = await API.put('/api/product-cards/' + id, data);
    if (resp && resp.code === 200) {
      window.Toast && Toast.show('已保存（人工编辑优先生效）', 'success');
      // cleanup + 清草稿
      _pcEditCleanups.forEach(fn => fn());
      _pcEditCleanups = [];
      if (draftKey && window.AutoSave) {
        window.AutoSave.clear({ scope: 'product_card', key: draftKey });
        // 落一条 AI/编辑结果归档（user 自己的人工编辑也算一次产出）
        const card = pcState.versions.find(v => v.id === id) || {};
        window.AutoSave.saveAiResult({
          type: 'product_card',
          productId: pcState.productId,
          result: Object.assign({ version: card.version, source: 'manual_edit' }, data),
          meta: { model: 'human', provider: 'human', cardId: id, version: card.version },
        });
      }
      Modal.close();
      await pcLoadVersions();
    } else {
      window.Toast && Toast.show(resp.msg || '保存失败', 'error');
    }
  } catch (e) {
    window.Toast && Toast.show('保存失败: ' + (e.message || e), 'error');
  }
}

async function pcSaveManual(draftKey) {
  const data = {
    product_id: pcState.productId,
    title_ru: document.getElementById('nTitleRu').value,
    title_zh: document.getElementById('nTitleZh').value,
    description_ru: document.getElementById('nDescRu').value,
    description_zh: document.getElementById('nDescZh').value,
    keywords_ru: _splitTags(document.getElementById('nKeywords').value),
    main_tags_ru: _splitTags(document.getElementById('nMainTags').value),
    long_tail_tags_ru: _splitTags(document.getElementById('nLongTail').value),
  };
  try {
    const resp = await API.post('/api/product-cards/manual', data);
    if (resp && resp.code === 200) {
      window.Toast && Toast.show('手动卡已保存', 'success');
      // cleanup + 清草稿
      _pcManualCleanups.forEach(fn => fn());
      _pcManualCleanups = [];
      if (draftKey && window.AutoSave) {
        window.AutoSave.clear({ scope: 'product_card', key: draftKey });
        // 落一条结果归档
        const card = resp.data || {};
        window.AutoSave.saveAiResult({
          type: 'product_card',
          productId: pcState.productId,
          result: Object.assign({ version: card.version, source: 'manual' }, data),
          meta: { model: 'human', provider: 'human', cardId: card.id, version: card.version },
        });
      }
      Modal.close();
      await pcLoadVersions();
    } else {
      window.Toast && Toast.show(resp.msg || '保存失败', 'error');
    }
  } catch (e) {
    window.Toast && Toast.show('保存失败: ' + (e.message || e), 'error');
  }
}

async function pcGenerateCard() {
  if (!pcState.productId) {
    window.Toast && Toast.show('请选择商品', 'error');
    return;
  }
  const points = (document.getElementById('pcSellingPoints').value || '').split('\n')
    .map(s => s.replace(/[\r]/g, '').trim()).filter(Boolean);
  if (!points.length) {
    window.Toast && Toast.show('请填写核心卖点（每行一条）', 'error');
    return;
  }
  const btn = document.getElementById('pcGenerateBtn');
  btn && (btn.disabled = true);
  btn && (btn.innerHTML = '<i data-lucide="loader-2" style="width:16px;height:16px;animation:spin 1s linear infinite;"></i> 生成中…');
  if (window.lucide) lucide.createIcons();
  try {
    const resp = await Api.request('/api/product-cards/generate', {
      method: 'POST',
      body: JSON.stringify({ product_id: pcState.productId, selling_points_zh: points }),
      timeout: 300000, // AI 三步生成最长 5 分钟
    });
    if (resp && resp.code === 200) {
      window.Toast && Toast.show('商品卡生成成功（v' + (resp.data && resp.data.version) + '）', 'success');
      // ===== 自动保存：AI 结果归档 =====
      if (window.AutoSave) {
        const card = resp.data || {};
        // 1) 落 ai_results（永久归档）
        window.AutoSave.saveAiResult({
          type: 'product_card',
          productId: pcState.productId,
          result: card,
          meta: {
            model: card.model || card.provider || 'unknown',
            provider: card.provider || '',
            version: card.version,
            source: 'ai_generate',
            cardId: card.id,
            sellingPointsInput: points,
          },
        });
        // 2) 生成成功 → 核心卖点草稿已经体现在新版本里，可清
        window.AutoSave.clear({ scope: 'product_card', key: 'pc_points_' + pcState.productId });
      }
      await pcLoadVersions();
    } else {
      window.Toast && Toast.show(resp.msg || '生成失败', 'error');
    }
  } catch (e) {
    window.Toast && Toast.show('生成失败: ' + (e.message || e), 'error');
  } finally {
    btn && (btn.disabled = false);
    btn && (btn.innerHTML = '<i data-lucide="sparkles" style="width:16px;height:16px;"></i> 生成商品卡');
    if (window.lucide) lucide.createIcons();
  }
}

async function pcRegenerateCard(id) {
  const ok = await Modal.confirm('重新生成会新增一个新版本，当前生效的编辑版本不会被覆盖，确定吗？');
  if (!ok) return;
  try {
    const resp = await Api.request('/api/product-cards/' + id + '/regenerate', { method: 'POST' });
    if (resp && resp.code === 200) {
      window.Toast && Toast.show('已重新生成新版本', 'success');
      // ===== 自动保存：AI 结果归档 =====
      if (window.AutoSave) {
        const card = resp.data || {};
        window.AutoSave.saveAiResult({
          type: 'product_card',
          productId: pcState.productId,
          result: card,
          meta: {
            model: card.model || card.provider || 'unknown',
            provider: card.provider || '',
            version: card.version,
            source: 'ai_regenerate',
            baseCardId: id,
            cardId: card.id,
          },
        });
      }
      await pcLoadVersions();
    } else {
      window.Toast && Toast.show(resp.msg || '操作失败', 'error');
    }
  } catch (e) {
    window.Toast && Toast.show('操作失败: ' + (e.message || e), 'error');
  }
}

async function pcActivateCard(id) {
  try {
    const resp = await Api.request('/api/product-cards/' + id + '/activate', { method: 'POST' });
    if (resp && resp.code === 200) {
      window.Toast && Toast.show('已采纳该版本为生效', 'success');
      await pcLoadVersions();
    } else {
      window.Toast && Toast.show(resp.msg || '操作失败', 'error');
    }
  } catch (e) {
    window.Toast && Toast.show('操作失败: ' + (e.message || e), 'error');
  }
}

async function pcDeleteCard(id) {
  const ok = await Modal.confirm('删除改动不可恢复，确定删除该版本吗？');
  if (!ok) return;
  try {
    const resp = await API.del('/api/product-cards/' + id);
    if (resp && resp.code === 200) {
      window.Toast && Toast.show('已删除', 'success');
      pcState.selectedCard = null;
      await pcLoadVersions();
    } else {
      window.Toast && Toast.show(resp.msg || '删除失败', 'error');
    }
  } catch (e) {
    window.Toast && Toast.show('删除失败: ' + (e.message || e), 'error');
  }
}

function escapeHtml(str) {
  return String(str || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeHtmlAttr(str) {
  return escapeHtml(str);
}

Router.register('/product-cards', (route) => {
  const html = renderProductCardsPage();
  setTimeout(() => initProductCardsPage(), 50);
  return html;
});