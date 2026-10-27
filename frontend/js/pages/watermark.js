let watermarkManagementStores = [];
let watermarkPreviewUrl = '';
let watermarkManagementConfig = { position: 'bottom-right', widthRatio: 0.18, opacity: 0.7, marginRatio: 0.03 };

function renderWatermarkPage() {
  setTimeout(loadWatermarkManagement, 0);
  return `<div class="media-page watermark-page">
    <div class="media-page-head"><div><h2>水印管理</h2><p>每个 Ozon 店铺单独保存一套水印，仅在生成或编辑图片时自动加印。</p></div></div>
    <section class="watermark-card">
      <label class="media-label" for="watermarkManagementStore">店铺</label>
      <select id="watermarkManagementStore" class="form-select" onchange="loadShopWatermark(this.value)"><option value="">正在读取店铺...</option></select>
      <div class="watermark-editor" id="watermarkEditor" hidden>
        <div class="watermark-preview" id="watermarkPreview"><span>尚未上传水印</span></div>
        <div class="watermark-controls">
          <label class="media-label">水印图片（PNG / JPG）</label>
          <input id="shopWatermarkFile" type="file" accept="image/png,image/jpeg" onchange="uploadShopWatermarkFile(this.files[0])">
          <label class="media-label" for="watermarkPosition">位置</label>
          <select class="form-select" id="watermarkPosition"><option value="bottom-right">右下角</option><option value="bottom-left">左下角</option><option value="top-right">右上角</option><option value="top-left">左上角</option></select>
          <label class="media-label" for="watermarkWidth">相对宽度 <span id="watermarkWidthLabel">18%</span></label>
          <input id="watermarkWidth" type="range" min="5" max="50" value="18" oninput="document.getElementById('watermarkWidthLabel').textContent=this.value+'%'">
          <label class="media-label" for="watermarkOpacity">不透明度 <span id="watermarkOpacityLabel">70%</span></label>
          <input id="watermarkOpacity" type="range" min="10" max="100" value="70" oninput="document.getElementById('watermarkOpacityLabel').textContent=this.value+'%'">
          <label class="media-label" for="watermarkMargin">边距 <span id="watermarkMarginLabel">3%</span></label>
          <input id="watermarkMargin" type="range" min="0" max="10" value="3" oninput="document.getElementById('watermarkMarginLabel').textContent=this.value+'%'">
          <button class="btn btn-primary" type="button" onclick="saveShopWatermarkSettings()">保存样式</button>
        </div>
      </div>
      <p class="watermark-note">原始上传图片和参考图不会被修改；新生成结果会在保存到图库前加印。</p>
    </section>
  </div>`;
}

async function loadWatermarkManagement() {
  const select = document.getElementById('watermarkManagementStore');
  if (!select) return;
  const response = await Api.getStores();
  watermarkManagementStores = response?.code === 200 ? (response.data?.list || []) : [];
  select.innerHTML = `<option value="">请选择店铺</option>${watermarkManagementStores.map(store => `<option value="${escapeAttr(store.id)}">${escapeHtml(store.alias || store.store_id)} · ${escapeHtml(store.store_id)}</option>`).join('')}`;
}

async function loadShopWatermark(storeId) {
  const editor = document.getElementById('watermarkEditor');
  if (!storeId || !editor) { if (editor) editor.hidden = true; return; }
  const response = await Api.getShopWatermark(storeId);
  if (response?.code !== 200) return Toast.show(response?.msg || '读取店铺水印失败', 'error');
  editor.hidden = false;
  watermarkManagementConfig = response.data.config || { position: 'bottom-right', widthRatio: 0.18, opacity: 0.7, marginRatio: 0.03 };
  document.getElementById('watermarkPosition').value = watermarkManagementConfig.position;
  document.getElementById('watermarkWidth').value = Math.round(watermarkManagementConfig.widthRatio * 100);
  document.getElementById('watermarkOpacity').value = Math.round(watermarkManagementConfig.opacity * 100);
  document.getElementById('watermarkMargin').value = Math.round(watermarkManagementConfig.marginRatio * 100);
  ['watermarkWidth', 'watermarkOpacity', 'watermarkMargin'].forEach(id => document.getElementById(id).dispatchEvent(new Event('input')));
  const preview = document.getElementById('watermarkPreview');
  if (watermarkPreviewUrl) URL.revokeObjectURL(watermarkPreviewUrl);
  if (response.data.configured) {
    try {
      watermarkPreviewUrl = await Api.getShopWatermarkImage(storeId);
      preview.innerHTML = `<img src="${escapeAttr(watermarkPreviewUrl)}" alt="店铺水印预览">`;
    } catch (error) { preview.innerHTML = '<span>水印预览读取失败</span>'; }
  } else {
    watermarkPreviewUrl = '';
    preview.innerHTML = '<span>尚未上传水印</span>';
  }
}

async function uploadShopWatermarkFile(file) {
  const storeId = document.getElementById('watermarkManagementStore')?.value;
  if (!storeId || !file) return;
  if (!/^image\/(png|jpeg)$/.test(file.type) || file.size > 10 * 1024 * 1024) return Toast.show('请选择 10 MB 以内的 PNG 或 JPG 水印', 'warning');
  const response = await Api.uploadShopWatermark(storeId, file);
  if (response?.code !== 200) return Toast.show(response?.msg || '水印上传失败', 'error');
  Toast.show('水印已上传', 'success');
  await loadShopWatermark(storeId);
}

async function saveShopWatermarkSettings() {
  const storeId = document.getElementById('watermarkManagementStore')?.value;
  if (!storeId) return Toast.show('请先选择店铺', 'warning');
  const config = {
    position: document.getElementById('watermarkPosition').value,
    widthRatio: Number(document.getElementById('watermarkWidth').value) / 100,
    opacity: Number(document.getElementById('watermarkOpacity').value) / 100,
    marginRatio: Number(document.getElementById('watermarkMargin').value) / 100,
  };
  const response = await Api.updateShopWatermarkSettings(storeId, config);
  if (response?.code !== 200) return Toast.show(response?.msg || '水印样式保存失败', 'error');
  watermarkManagementConfig = response.data.config;
  Toast.show('水印样式已保存', 'success');
}

Router.register('/watermark', renderWatermarkPage);
