/** Ozon link-to-listing workspace, isolated from the existing publish flow. */
let sdqProduct = null;
let sdqCard = null;

function renderShangDeKuaiPage() {
  setTimeout(() => document.getElementById('sdqSourceUrl')?.focus(), 0);
  return `
    <div class="sdq-page">
      <div class="media-page-head">
        <div><h2>上得快</h2><p>Ozon 商品链接 → 商品资料 → AI 商品卡 → 人工确认</p></div>
      </div>
      <div class="sdq-step-card">
        <div class="sdq-step-heading"><span>1</span><div><strong>采集 Ozon 商品</strong><small>打开链接后，用 ERP 采集扩展读取商品资料和图片</small></div></div>
        <div class="sdq-source-row">
          <input id="sdqSourceUrl" class="form-input" type="url" placeholder="粘贴 Ozon 商品链接，例如 https://www.ozon.ru/product/...">
          <button class="btn btn-secondary" type="button" onclick="sdqOpenOzon()">打开并采集</button>
          <button class="btn btn-ghost" type="button" onclick="sdqLoadCaptured()">查找已采集商品</button>
        </div>
        <div id="sdqSourceStatus" class="sdq-status">先粘贴 Ozon 商品链接。采集完成后回到这里查找商品。</div>
      </div>
      <div class="sdq-step-card" id="sdqProductCard" hidden></div>
      <div class="sdq-step-card" id="sdqAiCard" hidden></div>
    </div>`;
}

function sdqNormalizeUrl(value) {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== 'https:' || !(url.hostname === 'ozon.ru' || url.hostname.endsWith('.ozon.ru')) || !url.pathname.includes('/product/')) return '';
    url.search = '';
    url.hash = '';
    url.pathname = url.pathname.replace(/\/+$/, '');
    return url.href;
  } catch (_) { return ''; }
}

function sdqProductId(url) {
  const path = new URL(url).pathname.replace(/\/+$/, '');
  const match = path.match(/(?:^|[-/])(\d+)$/);
  return match?.[1] || '';
}

function sdqOpenOzon() {
  const input = document.getElementById('sdqSourceUrl');
  const url = sdqNormalizeUrl(input?.value || '');
  if (!url) return Toast.show('请输入有效的 HTTPS Ozon 商品链接', 'warning');
  input.value = url;
  window.open(url, '_blank', 'noopener');
  const status = document.getElementById('sdqSourceStatus');
  if (status) status.textContent = '在新标签页等待采集面板加载，然后点击「一键采集到OZON ERP」。完成后回到本页点击「查找已采集商品」。';
}

async function sdqLoadCaptured() {
  const input = document.getElementById('sdqSourceUrl');
  const url = sdqNormalizeUrl(input?.value || '');
  if (!url) return Toast.show('请输入有效的 HTTPS Ozon 商品链接', 'warning');
  const productId = sdqProductId(url);
  if (!productId) return Toast.show('链接中没有识别到 Ozon 商品编号', 'warning');
  const status = document.getElementById('sdqSourceStatus');
  if (status) status.textContent = '正在查找 ERP 采集箱…';
  const response = await Api.getProducts({ page: 1, pageSize: 100, keyword: productId });
  const products = response?.code === 200 ? (response.data?.list || []) : [];
  const product = products.find(item =>
    String(item.sourceId || item.sku || '') === productId
    && sdqNormalizeUrl(item.originalUrl || item.sourceLink || '') === url
  );
  if (!product) {
    if (status) status.textContent = response?.code === 200
      ? '还没找到该商品。请确认 Ozon 标签页中的采集扩展已完成采集，再重试。'
      : (response?.msg || '读取采集箱失败');
    return;
  }
  sdqProduct = product;
  sdqCard = null;
  if (status) status.textContent = '已找到采集商品。源信息保留在下方，可继续生成并审核商品卡。';
  sdqRenderProduct();
}

function sdqRenderProduct() {
  const product = sdqProduct;
  const panel = document.getElementById('sdqProductCard');
  if (!product || !panel) return;
  const attrs = Array.isArray(product.attributes) ? product.attributes : [];
  const attrText = attrs.map(item => {
    const name = item?.name || item?.id || '';
    const value = item?.value ?? item?.sourceValue ?? '';
    return name && value ? `<div class="sdq-attribute"><span>${escapeHtml(name)}</span><strong>${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : value)}</strong></div>` : '';
  }).filter(Boolean).join('') || '<div class="sdq-muted">未采集到商品特征，请打开 Ozon 页面检查采集结果。</div>';
  const images = (product.images || []).slice(0, 8).map(url => `<img src="${escapeAttr(url)}" alt="采集商品图" loading="lazy" referrerpolicy="no-referrer">`).join('');
  panel.hidden = false;
  panel.innerHTML = `
    <div class="sdq-step-heading"><span>2</span><div><strong>核对采集内容</strong><small>原始采集数据不被 AI 覆盖</small></div></div>
    <div class="sdq-source-data">
      <div><label>来源标题</label><p>${escapeHtml(product.title || '未采集')}</p></div>
      <div><label>来源描述</label><p class="sdq-long-text">${escapeHtml(product.description || '未采集')}</p></div>
      <div><label>商品特征</label><div class="sdq-attributes">${attrText}</div></div>
      <div><label>采集图片（最多展示 8 张）</label><div class="sdq-images">${images || '<span class="sdq-muted">未采集到图片</span>'}</div></div>
    </div>
    <div class="sdq-actions">
      <button class="btn btn-secondary" type="button" onclick="sdqPrepareSellingPoints()">AI 整理中文商品事实</button>
      <button class="btn btn-primary" type="button" onclick="sdqGenerateCard()">生成 Ozon 商品卡</button>
      <button class="btn btn-ghost" type="button" onclick="sdqOpenImageWorkspace()">打开 AI 套图工作区</button>
    </div>
    <label class="form-label" for="sdqSellingPoints">供商品卡生成使用的商品事实（请审核，每行一条）</label>
    <textarea id="sdqSellingPoints" class="form-textarea" rows="5" placeholder="采集商品后，可先让 AI 整理，再人工核对。"></textarea>`;
  sdqRenderSourceFacts();
}

function sdqRenderSourceFacts() {
  if (!sdqProduct) return;
  const facts = [sdqProduct.title, sdqProduct.description, ...(sdqProduct.attributes || []).map(item => {
    const value = item?.value ?? item?.sourceValue ?? '';
    return value ? `${item?.name || item?.id || '商品特征'}：${typeof value === 'object' ? JSON.stringify(value) : value}` : '';
  })].filter(Boolean);
  const field = document.getElementById('sdqSellingPoints');
  if (field && !field.value) field.value = facts.join('\n');
}

async function sdqPrepareSellingPoints() {
  if (!sdqProduct) return;
  const button = document.querySelector('#sdqProductCard button[onclick="sdqPrepareSellingPoints()"]');
  if (button) { button.disabled = true; button.textContent = 'AI 正在整理…'; }
  const facts = document.getElementById('sdqSellingPoints')?.value.trim() || '';
  const response = await Api.aiGenerate({
    type: 'description',
    content: `仅依据以下 Ozon 商品原始信息，用中文逐行提炼已明确给出的商品事实和属性。不要补充推测、认证或未出现的功能。\n${facts}`,
    params: { platform: 'ozon', language: 'zh' },
  });
  if (response?.code === 200 && response.data?.result) {
    const field = document.getElementById('sdqSellingPoints');
    if (field) field.value = response.data.result;
    Toast.show('已整理，请核对事实后再生成商品卡', 'success');
  } else Toast.show(response?.msg || 'AI 整理失败', 'error');
  if (button) { button.disabled = false; button.textContent = 'AI 整理中文商品事实'; }
}

async function sdqGenerateCard() {
  if (!sdqProduct) return;
  const selling = document.getElementById('sdqSellingPoints')?.value.trim();
  if (!selling) return Toast.show('请先填写商品事实', 'warning');
  const response = await Api.request('/api/product-cards/generate', {
    method: 'POST', body: JSON.stringify({ product_id: sdqProduct.id, selling_points_zh: selling }), timeout: 300000,
  });
  if (response?.code !== 200 || !response.data) return Toast.show(response?.msg || '商品卡生成失败', 'error');
  sdqCard = response.data;
  sdqRenderGeneratedCard();
  Toast.show('商品卡已生成，可编辑并单独采纳', 'success');
}

function sdqRenderGeneratedCard() {
  const card = sdqCard;
  const panel = document.getElementById('sdqAiCard');
  if (!card || !panel) return;
  panel.hidden = false;
  panel.innerHTML = `
    <div class="sdq-step-heading"><span>3</span><div><strong>审核并保存 AI 商品卡</strong><small>保存为单独商品卡版本；采纳后才成为上架优先内容</small></div></div>
    <label class="form-label">俄语标题</label><textarea id="sdqTitleRu" class="form-textarea" rows="2" maxlength="130">${escapeHtml(card.title_ru || '')}</textarea>
    <label class="form-label">中文标题</label><textarea id="sdqTitleZh" class="form-textarea" rows="2">${escapeHtml(card.title_zh || '')}</textarea>
    <label class="form-label">俄语描述</label><textarea id="sdqDescriptionRu" class="form-textarea" rows="8">${escapeHtml(card.description_ru || '')}</textarea>
    <label class="form-label">中文描述</label><textarea id="sdqDescriptionZh" class="form-textarea" rows="6">${escapeHtml(card.description_zh || '')}</textarea>
    <div class="sdq-actions">
      <button class="btn btn-secondary" type="button" onclick="sdqSaveCard(false)">保存人工修改</button>
      <button class="btn btn-primary" type="button" onclick="sdqSaveCard(true)">保存并采纳</button>
      <button class="btn btn-ghost" type="button" onclick="sdqOpenProductEditor()">进入商品编辑与上架</button>
    </div>
    <div id="sdqCardStatus" class="sdq-status">卡片版本 v${escapeHtml(card.version || '?')} · ${escapeHtml(card.status || 'GENERATED')}</div>`;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function sdqSaveCard(activate) {
  if (!sdqCard?.id) return;
  const data = {
    title_ru: document.getElementById('sdqTitleRu')?.value || '',
    title_zh: document.getElementById('sdqTitleZh')?.value || '',
    description_ru: document.getElementById('sdqDescriptionRu')?.value || '',
    description_zh: document.getElementById('sdqDescriptionZh')?.value || '',
  };
  const save = await Api.request(`/api/product-cards/${sdqCard.id}`, { method: 'PUT', body: JSON.stringify(data) });
  if (save?.code !== 200) return Toast.show(save?.msg || '商品卡保存失败', 'error');
  sdqCard = save.data;
  if (activate) {
    const applied = await Api.request(`/api/product-cards/${sdqCard.id}/activate`, { method: 'POST' });
    if (applied?.code !== 200) return Toast.show(applied?.msg || '采纳失败', 'error');
    sdqCard = applied.data;
  }
  const status = document.getElementById('sdqCardStatus');
  if (status) status.textContent = activate ? '已保存并采纳；原上架流程未改变。' : '人工修改已保存；当前尚未采纳。';
  Toast.show(activate ? '商品卡已采纳' : '商品卡修改已保存', 'success');
}

function sdqOpenProductEditor() {
  if (!sdqProduct) return;
  if (typeof editProduct !== 'function') return Toast.show('当前页面无法打开商品编辑器，请从采集箱打开此商品', 'error');
  editProduct(sdqProduct.id, { product: sdqProduct });
}

async function sdqOpenImageWorkspace() {
  if (!sdqProduct) return;
  const button = document.querySelector('#sdqProductCard button[onclick="sdqOpenImageWorkspace()"]');
  if (button) { button.disabled = true; button.textContent = '正在准备参考图…'; }
  const imageUrls = (sdqProduct.images || []).slice(0, 6);
  let referenceImages = [];
  if (imageUrls.length) {
    const imported = await Api.request('/api/collect/images', {
      method: 'POST',
      body: JSON.stringify({ product_title: sdqProduct.title || 'Ozon 商品', source_url: sdqProduct.originalUrl || '', images: imageUrls.map(url => ({ url, source: 'main_gallery' })) }),
    });
    if (imported?.assets?.length) {
      referenceImages = await Promise.all(imported.assets.slice(0, 6).map(async (asset, index) => {
        const response = await fetch(asset.url);
        const blob = await response.blob();
        return await new Promise(resolve => {
          const reader = new FileReader();
          reader.onload = () => resolve({ name: asset.title || `Ozon参考图${index + 1}`, type: blob.type || 'image/jpeg', dataUrl: reader.result });
          reader.onerror = () => resolve(null);
          reader.readAsDataURL(blob);
        });
      }));
      referenceImages = referenceImages.filter(Boolean);
    }
  }
  sessionStorage.setItem('shangdekuai_image_context', JSON.stringify({
    product_id: sdqProduct.id,
    title: sdqProduct.title || '',
    description: sdqProduct.description || '',
    attributes: sdqProduct.attributes || [],
    images: sdqProduct.images || [],
    referenceImages,
  }));
  if (button) { button.disabled = false; button.textContent = '打开 AI 套图工作区'; }
  if (imageUrls.length && !referenceImages.length) Toast.show('商品图未能导入图库；可在套图页手动上传参考图', 'warning');
  Router.navigate('/ai-image');
}

Router.register('/shangdekuai', renderShangDeKuaiPage);
