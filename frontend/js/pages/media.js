/** 我的图库与 AI 图片生成。 */
let galleryAssets = [];
let galleryFilter = { keyword: '', source: '', favorite: '' };

function renderGalleryPage() {
  setTimeout(loadGalleryAssets, 0);
  return `
    <div class="media-page">
      <div class="media-page-head">
        <div><h2>我的图库</h2><p>集中管理商品图片与 AI 创作素材</p></div>
        <div class="media-head-actions">
          <input id="galleryFileInput" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onchange="uploadGalleryFiles(this.files)">
          <button class="btn btn-sm btn-ghost" onclick="document.getElementById('galleryFileInput').click()"><i data-lucide="upload"></i> 上传图片</button>
          <button class="btn btn-sm btn-primary" onclick="Router.navigate('/ai-image')"><i data-lucide="sparkles"></i> AI 生成</button>
        </div>
      </div>
      <div class="media-toolbar">
        <div class="media-search"><i data-lucide="search"></i><input id="gallerySearch" placeholder="搜索名称、标签或提示词" oninput="debounceGallerySearch(this.value)"></div>
        <div class="media-segments">
          <button class="active" data-source="" onclick="setGallerySource(this,'')">全部</button>
          <button data-source="upload" onclick="setGallerySource(this,'upload')">上传</button>
          <button data-source="ai" onclick="setGallerySource(this,'ai')">AI 生成</button>
          <button data-source="favorite" onclick="setGallerySource(this,'favorite')"><i data-lucide="star"></i> 收藏</button>
        </div>
        <span class="media-count" id="galleryCount">0 张</span>
      </div>
      <div class="gallery-grid" id="galleryGrid"><div class="media-loading">正在读取图库...</div></div>
    </div>`;
}

function renderAIImagePage() {
  setTimeout(() => { loadAIGenerationHistory(); Api.getAIModelsStatus().then(showImageModelStatus); loadPixPixStatus(); loadAISuiteModels(); toggleAIImageProvider(); sdqApplyImageContext(); }, 0);
  return `
    <div class="media-page ai-image-page">
      <div class="media-page-head">
        <div><h2>AI 图片生成</h2><p>单张创作或一次制作完整 Ozon 商品套图</p></div>
        <button class="btn btn-sm btn-ghost" onclick="Router.navigate('/gallery')"><i data-lucide="images"></i> 我的图库</button>
      </div>
      <div class="ai-image-mode-switch" role="tablist">
        <button id="aiModeSingle" class="active" onclick="toggleAIImageMode('single')"><i data-lucide="image"></i> 单张生成</button>
        <button id="aiModeSuite" onclick="toggleAIImageMode('suite')"><i data-lucide="layout-grid"></i> Ozon 套图</button>
      </div>
      <div class="ai-image-workspace">
        <aside class="ai-image-controls" id="aiSinglePanel">
          <div class="ai-model-state" id="aiImageModelState"><span class="state-dot"></span><span>正在读取模型配置...</span></div>
          <label class="media-label">图片提供商</label>
          <select id="aiImageProvider" class="form-select" onchange="toggleAIImageProvider()"><option value="apiyi_banana" selected>APIYI · Nano Banana Pro</option><option value="apiyi">APIYI · GPT-Image</option><option value="yyds">YYDS · GPT-Image</option><option value="lingkeai">凌科 AI · OpenAI 兼容</option><option value="default">现有图片 API</option><option value="pixpix">PixPix</option></select>
          <div id="pixpixPanel" hidden></div>
          <label class="media-label">参考图片 <span style="font-weight:400;color:var(--text-muted);">（可选）</span></label>
          <input id="aiImageReferenceInput" type="file" accept="image/png,image/jpeg,image/webp" hidden onchange="selectAIImageReference(this.files[0])">
          <div class="ai-reference-box" id="aiImageReferenceBox">
            <button type="button" class="btn btn-sm btn-ghost" onclick="document.getElementById('aiImageReferenceInput').click()"><i data-lucide="upload"></i> 上传商品参考图</button>
            <span>用于保留商品外观并重新设计背景与构图</span>
          </div>
          <label class="media-label">图片描述</label>
          <textarea id="aiImagePrompt" class="form-textarea" rows="8" maxlength="2000" placeholder="例如：白色背景中的黑色全自动防风雨伞，商品摄影，完整展示伞面与手柄，柔和棚拍光线，无文字"></textarea>
          <div class="media-form-row">
            <div><label class="media-label">画面比例</label><select id="aiImageSize" class="form-select"><option value="1024x1024">1:1 方图</option><option value="1024x1360">3:4 竖图</option><option value="1024x1536">2:3 竖图</option><option value="1536x1024">3:2 横图</option></select></div>
            <div><label class="media-label">生成数量</label><select id="aiImageCount" class="form-select"><option value="1">1 张</option><option value="2">2 张</option><option value="4">4 张</option></select></div>
          </div>
          <label class="media-label">图片模型</label>
          <input id="aiImageModel" class="form-input" value="gemini-3-pro-image-preview" placeholder="如 gemini-3-pro-image-preview">
          <button id="generateImageBtn" class="btn btn-primary ai-generate-btn" onclick="generateAIImage()"><i data-lucide="wand-sparkles"></i> 开始生成</button>
        </aside>
        <section class="ai-image-results">
          <div class="media-section-title"><div><h3>生成结果</h3><span>点击图片查看详情</span></div></div>
          <div class="gallery-grid ai-results-grid" id="aiImageResults"><div class="media-empty"><i data-lucide="image-plus"></i><strong>等待创作</strong><span>输入商品与场景描述开始生成</span></div></div>
        </section>
      </div>
      <div class="ai-suite-workspace" id="aiSuitePanel" hidden>
        <aside class="ai-image-controls ai-suite-controls">
          <div class="suite-step"><span>1</span><div><b>上传商品参考图</b><small>最多 6 张，同一商品不同角度</small></div></div>
          <input id="aiSuiteReferenceInput" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onchange="selectAISuiteReferences(this.files)">
          <div class="ai-suite-reference-box" id="aiSuiteReferenceBox"><button type="button" class="btn btn-sm btn-ghost" onclick="document.getElementById('aiSuiteReferenceInput').click()"><i data-lucide="upload"></i> 上传参考图</button><span>建议上传正面、侧面和细节图</span></div>
          <div class="suite-step"><span>2</span><div><b>填写商品要求</b><small>只使用真实信息，不自动虚构卖点</small></div></div>
          <textarea id="aiSuitePrompt" class="form-textarea" rows="7" maxlength="3000" placeholder="例如：黑色无线耳机，保留原图外观和俄语文字；突出降噪、续航和通勤使用场景，不添加未提供的参数"></textarea>
          <button id="aiSuitePromptGenerate" class="btn btn-sm btn-ghost" type="button" onclick="sdqGenerateSuitePrompt()" hidden>AI 生成套图提示词</button>
          <div class="media-form-row"><div><label class="media-label">图片比例</label><select id="aiSuiteSize" class="form-select"><option value="1024x1360">3:4 竖图</option><option value="1024x1024">1:1 方图</option><option value="1024x1536">2:3 竖图</option></select></div><div><label class="media-label">图片模型</label><select id="aiSuiteProvider" class="form-select" onchange="toggleAISuiteProvider()"><option value="apiyi_banana">Nano Banana Pro</option><option value="apiyi">APIYI GPT-Image</option><option value="yyds">YYDS GPT-Image</option><option value="lingkeai">凌科 AI</option></select></div></div>
          <select id="aiSuiteModel" class="form-select" hidden><option value="">正在读取凌科模型...</option></select>
          <div class="suite-step suite-step-modules"><span>3</span><div><b>选择套图模块</b><small>每个模块生成 1 张图片</small></div></div>
          <div class="ai-suite-modules">${AI_SUITE_MODULES.map(module => `<label class="ai-suite-module is-selected"><input type="checkbox" value="${module.key}" checked onchange="updateAISuiteModuleState(this)"><span><b>${module.name}</b><small>${module.description}</small></span></label>`).join('')}</div>
          <button id="generateSuiteBtn" class="btn btn-primary ai-generate-btn" onclick="generateAISuite()"><i data-lucide="layers"></i> 生成 Ozon 套图</button>
        </aside>
        <section class="ai-image-results ai-suite-results"><div class="media-section-title"><div><h3>套图结果</h3><span id="aiSuiteProgress">选择模块后开始生成</span></div><button class="btn btn-sm btn-ghost" onclick="clearAISuiteResults()">清空本轮</button></div><div class="ai-suite-result-grid" id="aiSuiteResults"><div class="media-empty"><i data-lucide="layout-grid"></i><strong>还没有套图任务</strong><span>上传参考图并选择模块后开始</span></div></div></section>
      </div>
    </div>`;
}

async function loadGalleryAssets() {
  const response = await Api.getGallery(galleryFilter);
  galleryAssets = response?.code === 200 ? (response.data?.list || []) : [];
  renderGalleryGrid('galleryGrid', galleryAssets);
  const count = document.getElementById('galleryCount');
  if (count) count.textContent = `${galleryAssets.length} 张`;
}

function galleryCard(asset) {
  const tags = (asset.tags || []).slice(0, 2).map(tag => `<span>${escapeHtml(tag)}</span>`).join('');
  return `<article class="gallery-item" data-id="${asset.id}">
    <button class="gallery-preview" onclick="showGalleryAsset('${asset.id}')"><img src="${escapeAttr(asset.url)}" alt="${escapeAttr(asset.title)}" loading="lazy"></button>
    <div class="gallery-meta"><div><strong title="${escapeAttr(asset.title)}">${escapeHtml(asset.title)}</strong><small>${asset.source === 'ai' ? 'AI 生成' : '本地上传'} · ${formatFileSize(asset.file_size)}</small></div>
      <button class="gallery-star ${asset.favorite ? 'active' : ''}" title="收藏" onclick="toggleGalleryFavorite('${asset.id}')"><i data-lucide="star"></i></button></div>
    ${tags ? `<div class="gallery-tags">${tags}</div>` : ''}
    <div class="gallery-actions"><button onclick="copyGalleryUrl('${asset.id}')" title="复制链接"><i data-lucide="link"></i></button><a href="${escapeAttr(asset.url)}" download title="下载"><i data-lucide="download"></i></a><button class="danger" onclick="deleteGalleryItem('${asset.id}')" title="删除"><i data-lucide="trash-2"></i></button></div>
  </article>`;
}

function renderGalleryGrid(targetId, assets) {
  const target = document.getElementById(targetId); if (!target) return;
  target.innerHTML = assets.length ? assets.map(galleryCard).join('') : `<div class="media-empty"><i data-lucide="images"></i><strong>暂无图片</strong><span>上传图片或使用 AI 生成后会显示在这里</span></div>`;
  if (window.lucide) lucide.createIcons();
}

async function uploadGalleryFiles(files) {
  if (!files?.length) return;
  Toast.show(`正在上传 ${files.length} 张图片...`, 'info');
  const response = await Api.uploadGallery(files);
  if (response?.code !== 200) return Toast.show(response?.msg || '上传失败', 'error');
  Toast.show(response.msg || '上传成功', 'success'); await loadGalleryAssets();
}

function setGallerySource(button, source) {
  document.querySelectorAll('.media-segments button').forEach(item => item.classList.remove('active')); button.classList.add('active');
  galleryFilter.source = source === 'favorite' ? '' : source; galleryFilter.favorite = source === 'favorite' ? '1' : ''; loadGalleryAssets();
}
let gallerySearchTimer;
function debounceGallerySearch(value) { clearTimeout(gallerySearchTimer); gallerySearchTimer = setTimeout(() => { galleryFilter.keyword = value.trim(); loadGalleryAssets(); }, 300); }
function findGalleryAsset(id) { return galleryAssets.find(item => String(item.id) === String(id)); }
async function toggleGalleryFavorite(id) { const item=findGalleryAsset(id); if(!item)return; await Api.updateGalleryAsset(id,{favorite:!item.favorite}); loadGalleryAssets(); }
async function copyGalleryUrl(id) { const item=findGalleryAsset(id); if(!item)return; await navigator.clipboard.writeText(new URL(item.url, location.origin).href); Toast.show('图片链接已复制','success'); }
async function deleteGalleryItem(id) { if(!await Modal.confirm('确定删除这张图片吗？'))return; const r=await Api.deleteGalleryAsset(id); if(r?.code===200){Toast.show('图片已删除','success');loadGalleryAssets();}else Toast.show(r?.msg||'删除失败','error'); }
function formatFileSize(bytes){const n=Number(bytes||0);return n>=1048576?(n/1048576).toFixed(1)+' MB':Math.max(1,Math.round(n/1024))+' KB';}

function showGalleryAsset(id) {
  const item=findGalleryAsset(id); if(!item)return;
  Modal.show({title:item.title,size:'lg',body:`<div class="gallery-detail"><img src="${escapeAttr(item.url)}" alt=""><div><p><b>来源</b>${item.source==='ai'?'AI 生成':'本地上传'}</p><p><b>文件大小</b>${formatFileSize(item.file_size)}</p><p><b>创建时间</b>${item.created_at}</p>${item.prompt?`<p><b>提示词</b>${escapeHtml(item.prompt)}</p>`:''}</div></div>`,footer:[{text:'关闭',class:'btn-ghost'},{text:'复制链接',class:'btn-primary',onClick:()=>copyGalleryUrl(id)}]});
}

let aiImageReference = null;
const AI_SUITE_MODULES = [
  {key:'hero', name:'首屏主视觉', ruTitle:'Главное изображение', description:'清晰展示商品与核心价值', textMode:'可无文字或仅保留短俄语标题', instruction:'Ozon 首图：浅色或白色背景，商品完整居中，四周留安全边距，不放大段文字；如需文字，只使用简短俄语标题。'},
  {key:'selling_point', name:'核心卖点图', ruTitle:'Ключевые преимущества', description:'俄语标题 + 真实卖点', textMode:'必须有俄语标题和 2-3 条短俄语卖点', instruction:'详情卖点图：商品完整清晰，结合真实使用环境与克制的功能视觉元素表现产品价值，为后期文字排版预留干净区域。'},
  {key:'usage_scene', name:'真实使用场景', ruTitle:'Сценарий использования', description:'俄语标题 + 可信使用场景', textMode:'必须有俄语标题和 1-2 条短俄语说明', instruction:'详情场景图：展示商品在真实生活环境中的使用方式，人物与环境不能遮挡商品，为后期文字排版预留干净区域。'},
  {key:'angle', name:'多角度图', ruTitle:'Внешний вид и конструкция', description:'俄语标题 + 结构展示', textMode:'必须有俄语标题和结构短标签', instruction:'详情结构图：突出参考图中商品的外观与结构，可用合理的局部特写辅助，不改变商品型号，为后期文字排版预留干净区域。'},
  {key:'atmosphere', name:'场景氛围图', ruTitle:'Сценарий применения', description:'俄语标题 + 使用环境', textMode:'必须有俄语标题', instruction:'详情氛围图：用克制、可信的环境强化商品用途，背景不能喧宾夺主，为后期文字排版预留干净区域。'},
  {key:'detail', name:'商品细节图', ruTitle:'Детали и материалы', description:'俄语标题 + 材质工艺细节', textMode:'必须有俄语标题和细节短标签', instruction:'详情细节图：放大参考图中可见的材质、接口、纹理或做工，不虚构不可见结构，为后期文字排版预留干净区域。'},
  {key:'size', name:'尺寸/参数图', ruTitle:'Размеры и характеристики', description:'俄语标题 + 已确认参数', textMode:'必须有俄语标题，仅写已确认数据', instruction:'详情参数图：清晰呈现商品主体，并为后期排入已确认参数预留整洁区域；不要自行生成尺寸线、数字或参数。'},
  {key:'accessories', name:'配件/赠品图', ruTitle:'Комплектация', description:'俄语标题 + 实际配件清单', textMode:'必须有俄语标题和实际配件名称', instruction:'详情配件图：仅展示参考图或提示词明确存在的商品与配件，为后期文字排版预留整洁区域，不添加赠品。'},
];
let aiSuiteReferences = [];
let aiSuiteResults = [];

async function sdqApplyImageContext() {
  let context;
  try { context = JSON.parse(sessionStorage.getItem('shangdekuai_image_context') || 'null'); } catch (_) { return; }
  if (!context) return;
  sessionStorage.removeItem('shangdekuai_image_context');
  toggleAIImageMode('suite');
  const promptButton = document.getElementById('aiSuitePromptGenerate');
  if (promptButton) promptButton.hidden = false;
  aiSuiteReferences = Array.isArray(context.referenceImages) ? context.referenceImages : [];
  renderAISuiteReferences();
  const attributes = (context.attributes || []).map(item => {
    const value = item?.value ?? item?.sourceValue ?? '';
    return value ? `${item?.name || '商品特征'}：${typeof value === 'object' ? JSON.stringify(value) : value}` : '';
  }).filter(Boolean);
  const prompt = [
    `商品标题：${context.title || ''}`,
    `商品描述：${context.description || ''}`,
    attributes.length ? `已知商品特征：\n${attributes.join('\n')}` : '',
    '请保持参考图片中商品的真实外观、颜色和结构。只使用已知商品事实，不要添加未经确认的参数、认证或配件。套图文字使用俄语，图片本身不要生成文字。',
  ].filter(Boolean).join('\n\n');
  const field = document.getElementById('aiSuitePrompt');
  if (field) field.value = prompt.slice(0, 3000);
}

async function sdqGenerateSuitePrompt() {
  const field = document.getElementById('aiSuitePrompt');
  const button = document.getElementById('aiSuitePromptGenerate');
  if (!field?.value.trim()) return Toast.show('请先提供商品标题或商品事实', 'warning');
  if (button) { button.disabled = true; button.textContent = 'AI 正在生成…'; }
  const response = await Api.aiGenerate({
    type: 'description',
    content: `根据以下已确认商品信息，生成适用于 Ozon 商品套图的中文图像提示词。写清商品外观必须保持一致、主图和场景图的视觉方案、各图可展示的事实；禁止虚构规格、配件、认证或功效；图片中不生成文字。仅输出可直接粘贴的提示词，不要解释。\n\n${field.value.trim()}`,
    params: { platform: 'ozon', language: 'zh' },
  });
  if (response?.code === 200 && response.data?.result) {
    field.value = response.data.result.slice(0, 3000);
    Toast.show('套图提示词已生成，请检查后再生成图片', 'success');
  } else Toast.show(response?.msg || '套图提示词生成失败', 'error');
  if (button) { button.disabled = false; button.textContent = 'AI 生成套图提示词'; }
}

function toggleAIImageMode(mode) {
  const single = document.getElementById('aiSinglePanel');
  const workspace = document.querySelector('.ai-image-workspace');
  const suite = document.getElementById('aiSuitePanel');
  const singleBtn = document.getElementById('aiModeSingle');
  const suiteBtn = document.getElementById('aiModeSuite');
  const isSuite = mode === 'suite';
  if (workspace) workspace.hidden = isSuite;
  if (suite) suite.hidden = !isSuite;
  if (singleBtn) singleBtn.classList.toggle('active', !isSuite);
  if (suiteBtn) suiteBtn.classList.toggle('active', isSuite);
  if (isSuite && window.lucide) lucide.createIcons();
}

function selectAISuiteReferences(files) {
  const selected = Array.from(files || []).slice(0, 6);
  if (!selected.length) return;
  if (files.length > 6) Toast.show('最多选择 6 张参考图，已取前 6 张', 'warning');
  aiSuiteReferences = [];
  let loaded = 0;
  selected.forEach(file => {
    if (file.size > 10 * 1024 * 1024 || !/^image\/(png|jpeg|webp)$/.test(file.type)) { loaded++; return; }
    const reader = new FileReader();
    reader.onload = () => { aiSuiteReferences.push({name:file.name, type:file.type, dataUrl:reader.result}); loaded++; if (loaded === selected.length) renderAISuiteReferences(); };
    reader.readAsDataURL(file);
  });
}

function renderAISuiteReferences() {
  const box = document.getElementById('aiSuiteReferenceBox');
  if (!box) return;
  box.innerHTML = aiSuiteReferences.length
    ? `${aiSuiteReferences.map((item, index) => `<div class="ai-suite-reference-thumb"><img src="${escapeAttr(item.dataUrl)}" alt="参考图 ${index + 1}"><button type="button" onclick="removeAISuiteReference(${index})">×</button></div>`).join('')}<button type="button" class="btn btn-sm btn-ghost" onclick="document.getElementById('aiSuiteReferenceInput').click()">继续添加</button>`
    : '<button type="button" class="btn btn-sm btn-ghost" onclick="document.getElementById(\'aiSuiteReferenceInput\').click()"><i data-lucide="upload"></i> 上传参考图</button><span>建议上传正面、侧面和细节图</span>';
  if (window.lucide) lucide.createIcons();
}
function removeAISuiteReference(index) { aiSuiteReferences.splice(index, 1); renderAISuiteReferences(); }
function updateAISuiteModuleState(input) { input.closest('.ai-suite-module')?.classList.toggle('is-selected', input.checked); }
function selectedAISuiteModules() { return Array.from(document.querySelectorAll('.ai-suite-modules input:checked')).map(input => AI_SUITE_MODULES.find(item => item.key === input.value)).filter(Boolean); }
function clearAISuiteResults() { aiSuiteResults = []; const el = document.getElementById('aiSuiteResults'); if (el) el.innerHTML = '<div class="media-empty"><i data-lucide="layout-grid"></i><strong>还没有套图任务</strong><span>上传参考图并选择模块后开始</span></div>'; if (window.lucide) lucide.createIcons(); }
function renderAISuiteResults() {
  const el = document.getElementById('aiSuiteResults'); if (!el) return;
  el.innerHTML = aiSuiteResults.map((item, index) => `<article class="ai-suite-result-card ${item.status}"><div class="ai-suite-result-image">${item.asset ? `<img src="${escapeAttr(item.asset.url)}" alt="${escapeAttr(item.module.name)}" loading="lazy">` : `<i data-lucide="${item.status === 'error' ? 'alert-circle' : 'loader-circle'}"></i>`}</div><div class="ai-suite-result-meta"><b>${escapeHtml(item.module.name)}</b><span>${item.status === 'done' ? '已保存到图库' : item.status === 'error' ? escapeHtml(item.error || '生成失败') : '生成中...'}</span>${item.status === 'error' ? `<button class="btn btn-sm btn-ghost" onclick="retryAISuiteItem(${index})">重新生成图片</button>` : ''}${item.asset ? `<button class="btn btn-sm btn-ghost" onclick="saveAISuiteForCollect()">导入采集箱</button>` : ''}</div></article>`).join('');
  if (window.lucide) lucide.createIcons();
}

function fallbackAISuitePlan(module, userPrompt) {
  return {key:module.key, goal:module.description, composition:module.instruction, copy:`${module.ruTitle}\n${module.textMode}`, facts:userPrompt};
}

function parseAISuitePlans(value, modules, userPrompt) {
  const fallback = Object.fromEntries(modules.map(module => [module.key, fallbackAISuitePlan(module, userPrompt)]));
  const raw = String(value || '').replace(/```(?:json)?/gi, '').replace(/```/g, '').trim();
  let parsed;
  try { parsed = JSON.parse(raw); } catch (_) {
    const match = raw.match(/[\[{][\s\S]*[\]}]/);
    try { parsed = match ? JSON.parse(match[0]) : []; } catch (_) { parsed = []; }
  }
  const list = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.modules) ? parsed.modules : [];
  list.forEach(item => {
    if (!item?.key || !fallback[item.key]) return;
    fallback[item.key] = {...fallback[item.key], ...item, key:item.key};
  });
  return fallback;
}

async function buildAISuitePlans(userPrompt, modules) {
  const moduleSpec = modules.map(module => `${module.key}: ${module.name}；目标：${module.description}；基础要求：${module.instruction}`).join('\n');
  const request = `你是 Ozon 商品视觉总监。请把下面的完整套图方案拆解成每个模块一份独立执行提示词。每份提示词只能服务当前模块，不能重复引入其他模块的目标。只使用用户提供的真实商品事实，不虚构参数、配件、认证或功效。\n\n必须返回纯 JSON 数组，不要 Markdown，不要解释，格式严格为：[{"key":"hero","goal":"当前图目标","composition":"当前图构图、背景、光影","copy":"仅本图需要的俄语标题和最多3条短文案","facts":"本图允许展示的真实事实"}]。copy 必须是俄语；如果没有已确认数据就写空字符串。\n\n模块清单：\n${moduleSpec}\n\n完整套图方案：\n${userPrompt}`;
  try {
    const response = await Api.aiGenerate({type:'description', content:request, params:{platform:'ozon', language:'ru'}});
    if (response?.code === 200) return parseAISuitePlans(response.data?.result, modules, userPrompt);
  } catch (_) {
    // 方案拆解失败时使用模块内置规则，不能阻断图片生成。
  }
  return Object.fromEntries(modules.map(module => [module.key, fallbackAISuitePlan(module, userPrompt)]));
}

function buildAISuitePrompt(module, userPrompt, ratio, plan = {}) {
  const templateRules = module.key === 'hero'
    ? '采用 Ozon 主图构造：商品主体优先，干净浅色背景，少量高质量俄语信息。'
    : '采用专业电商详情图构图：商品或真实局部清晰突出，主体完整且不被遮挡；根据当前模块目标设计有层次的背景、光影、信息区、图标和构图，不套用统一白卡。';
  return `当前只生成一个模块：${module.name}（${module.ruTitle}）。不要执行其他模块的任务。\n\n当前模块目标：${plan.goal || module.description}\n当前模块构图：${plan.composition || module.instruction}\n当前模块允许使用的真实事实：${plan.facts || userPrompt}\n当前模块俄语文案：${plan.copy || module.textMode}\n${templateRules}\n模型必须一次性完成商品、背景、构图、光影、俄语文字、图标和信息层级的最终成图；不要把文字、图标或版式留给 ERP 后期，不要输出无文字底图。严格使用上面的俄语文案，文字必须清晰、完整、可读，只能使用俄语西里尔字母，不得出现中文、乱码或无意义伪文字。\n画布必须使用 ${ratio} 比例。参考图是最高优先级，必须复制参考图中实际可见的商品、颜色、结构和配件；参考图中没有的物品一律禁止新增；如果参考图中的配件是入耳式或有线耳机，必须保持原样，禁止改成头戴式耳机。只使用当前模块事实；不添加未提供的参数、认证、配件或功能；不要添加水印、价格、折扣、二维码、联系方式或购买引导。`;
}

async function generateAISuite() {
  const prompt = document.getElementById('aiSuitePrompt')?.value.trim();
  const modules = selectedAISuiteModules();
  const size = document.getElementById('aiSuiteSize')?.value || '1024x1360';
  const provider = document.getElementById('aiSuiteProvider')?.value || 'apiyi_banana';
  const ratio = size === '1024x1360' || size === '1024x1365' ? '3:4' : size === '1024x1536' ? '2:3' : '1:1';
  if (!prompt) return Toast.show('请填写商品卖点与制作要求', 'warning');
  if (!aiSuiteReferences.length) return Toast.show('请至少上传 1 张商品参考图', 'warning');
  if (!modules.length) return Toast.show('请至少选择 1 个套图模块', 'warning');
  const btn = document.getElementById('generateSuiteBtn');
  const progress = document.getElementById('aiSuiteProgress');
  const model = provider === 'apiyi_banana' ? 'gemini-3-pro-image-preview' : provider === 'yyds' ? 'gpt-image-2-cf' : provider === 'lingkeai' ? document.getElementById('aiSuiteModel')?.value.trim() : 'gpt-image-2.5-all';
  if (provider === 'lingkeai' && !model) return Toast.show('请先填写凌科 AI 的 Model ID', 'warning');
  btn.disabled = true;
  aiSuiteResults = modules.map(module => ({module, status:'queued'})); renderAISuiteResults();
  const suitePlans = await buildAISuitePlans(prompt, modules);
  let completed = 0;
  for (let index = 0; index < modules.length; index++) {
    const item = aiSuiteResults[index]; item.status = 'running';
    item.modulePlan = suitePlans[item.module.key] || fallbackAISuitePlan(item.module, prompt);
    if (progress) progress.textContent = `${completed} / ${modules.length} 已完成 · 正在生成 ${item.module.name}`;
    renderAISuiteResults();
    const generationPrompt = buildAISuitePrompt(item.module, prompt, ratio, item.modulePlan); item.generationPrompt = generationPrompt;
    const response = await Api.generateAIImages({provider, model, prompt:generationPrompt, size, count:1, template_version:`suite-v4-model-layout-${item.module.key}`, reference_image:aiSuiteReferences[0]?.dataUrl, reference_images:['apiyi_banana','apiyi'].includes(provider) ? aiSuiteReferences.map(ref => ref.dataUrl) : undefined});
    if (response?.code === 200 && response.data?.assets?.[0]) {
      item.rawAsset = response.data.assets[0];
      item.asset = item.rawAsset; item.status = 'done'; completed++;
    }
    else { item.status = 'error'; item.error = response?.msg || '生成失败'; }
    if (progress) progress.textContent = `${completed} / ${modules.length} 已完成`;
    renderAISuiteResults();
  }
  btn.disabled = false; Toast.show(completed === modules.length ? 'Ozon 套图已全部生成' : `已完成 ${completed} / ${modules.length} 张，请重试失败项`, completed === modules.length ? 'success' : 'warning');
}

async function retryAISuiteItem(index) {
  const item = aiSuiteResults[index]; if (!item) return;
  const prompt = document.getElementById('aiSuitePrompt')?.value.trim(); const size = document.getElementById('aiSuiteSize')?.value || '1024x1360'; const provider = document.getElementById('aiSuiteProvider')?.value || 'apiyi_banana'; const ratio = size === '1024x1360' || size === '1024x1365' ? '3:4' : size === '1024x1536' ? '2:3' : '1:1';
  item.status = 'running'; renderAISuiteResults();
  const retryModel = provider === 'apiyi_banana' ? 'gemini-3-pro-image-preview' : provider === 'yyds' ? 'gpt-image-2-cf' : provider === 'lingkeai' ? document.getElementById('aiSuiteModel')?.value.trim() : 'gpt-image-2.5-all';
  if (provider === 'lingkeai' && !retryModel) { item.status = 'error'; item.error = '请先填写凌科 AI 的 Model ID'; renderAISuiteResults(); return; }
  const modulePlan = item.modulePlan || fallbackAISuitePlan(item.module, prompt);
  item.modulePlan = modulePlan;
  const generationPrompt = buildAISuitePrompt(item.module, prompt, ratio, modulePlan); item.generationPrompt = generationPrompt;
  const response = await Api.generateAIImages({provider, model:retryModel, prompt:generationPrompt, size, count:1, template_version:`suite-v4-model-layout-${item.module.key}`, reference_image:aiSuiteReferences[0]?.dataUrl, reference_images:['apiyi_banana','apiyi'].includes(provider) ? aiSuiteReferences.map(ref => ref.dataUrl) : undefined});
  if (response?.code === 200 && response.data?.assets?.[0]) {
    item.rawAsset = response.data.assets[0];
    item.asset = item.rawAsset; item.status='done'; item.error='';
  } else { item.status='error'; item.error=response?.msg || '生成失败'; }
  renderAISuiteResults();
}

function saveAISuiteForCollect() {
  const assets = aiSuiteResults.filter(item => item.status === 'done' && item.asset).map(item => ({url:item.asset.url, title:item.module.name, module:item.module.key}));
  if (!assets.length) return Toast.show('当前没有可导入的套图', 'warning');
  localStorage.setItem('ozon_ai_suite_assets', JSON.stringify({savedAt:Date.now(), assets}));
  Toast.show('套图已准备好，可在采集箱编辑页点击“导入 AI 套图”', 'success');
  Router.navigate('/collect');
}
function selectAIImageReference(file){
  if(!file)return;
  if(file.size>10*1024*1024)return Toast.show('参考图不能超过 10MB','warning');
  if(!/^image\/(png|jpeg|webp)$/.test(file.type))return Toast.show('仅支持 PNG、JPG、WebP 图片','warning');
  const reader=new FileReader();
  reader.onload=()=>{aiImageReference={name:file.name,type:file.type,dataUrl:reader.result};const box=document.getElementById('aiImageReferenceBox');if(box)box.innerHTML=`<img src="${escapeAttr(reader.result)}" alt="参考图"><span title="${escapeAttr(file.name)}">${escapeHtml(file.name)}</span><button type="button" class="btn btn-sm btn-ghost" onclick="clearAIImageReference()">移除</button>`;};
  reader.readAsDataURL(file);
}
function clearAIImageReference(){aiImageReference=null;const box=document.getElementById('aiImageReferenceBox');if(box)box.innerHTML='<button type="button" class="btn btn-sm btn-ghost" onclick="document.getElementById(\'aiImageReferenceInput\').click()"><i data-lucide="upload"></i> 上传商品参考图</button><span>用于保留商品外观并重新设计背景与构图</span>';if(window.lucide)lucide.createIcons();}
function showImageModelStatus(response) { const el=document.getElementById('aiImageModelState');if(!el)return;const ok=response?.code===200&&response.data?.has_key;el.classList.toggle('ready',ok);el.innerHTML=`<span class="state-dot"></span><span>${ok?`${escapeHtml(response.data.name||response.data.provider)} 已配置`:'未配置图片 API，请前往模型管理'}</span>`; }
async function loadAIGenerationHistory(){const r=await Api.getGallery({source:'ai'});const items=r?.code===200?(r.data?.list||[]):[];galleryAssets=items;renderGalleryGrid('aiImageResults',items);}
let pixpixConnected = false;
async function loadPixPixStatus(){const panel=document.getElementById('pixpixPanel');if(!panel)return;const r=await Api.getPixPixStatus();pixpixConnected=Boolean(r?.code===200&&r.data?.connected);renderPixPixPanel(r?.data||{});}
function renderPixPixPanel(status){const panel=document.getElementById('pixpixPanel');if(!panel)return;if(!pixpixConnected){panel.innerHTML=`<div class="ai-model-state"><span class="state-dot"></span><span>PixPix 尚未连接。管理员可完成授权后供全员使用。</span></div><button class="btn btn-sm btn-secondary" onclick="startPixPixAuthorization()">连接 PixPix</button>`;return;}panel.innerHTML=`<div class="ai-model-state ready"><span class="state-dot"></span><span>PixPix 已连接（${Number(status.toolCount||0)} 个图像工具）</span></div><label class="media-label">PixPix 工具</label><select id="pixpixTool" class="form-select"><option value="">正在读取工具...</option></select>`;loadPixPixTools();}
function toggleAIImageProvider(){const provider=document.getElementById('aiImageProvider')?.value||'default';const pixpix=provider==='pixpix';document.getElementById('pixpixPanel').hidden=!pixpix;const model=document.getElementById('aiImageModel');if(model&&provider==='yyds')model.value='gpt-image-2-cf';if(model&&provider==='apiyi')model.value='gpt-image-2.5-all';if(model&&provider==='apiyi_banana')model.value='gemini-3-pro-image-preview';}
async function loadAISuiteModels(){if(document.getElementById('aiSuiteProvider')?.value==='lingkeai')await loadAIModelOptions('aiSuiteModel');}
async function loadAIModelOptions(selectId){const select=document.getElementById(selectId);if(!select)return;select.hidden=false;select.disabled=true;select.innerHTML='<option value="">正在读取可用模型...</option>';try{const response=await Api.getAIAvailableModels('lingkeai');const models=response?.code===200?(response.data?.models||[]):[];if(!models.length)throw new Error(response?.msg||'未返回模型列表');select.innerHTML=models.map((model,index)=>`<option value="${escapeAttr(model)}"${index===0?' selected':''}>${escapeHtml(model)}</option>`).join('');select.disabled=false;}catch(error){select.innerHTML='<option value="">模型读取失败，请到模型管理重试</option>';select.disabled=false;Toast.show(error.message||'凌科模型读取失败','warning');}}
function toggleAISuiteProvider(){const provider=document.getElementById('aiSuiteProvider')?.value||'apiyi_banana';const model=document.getElementById('aiSuiteModel');if(!model)return;model.hidden=provider!=='lingkeai';model.title=provider==='lingkeai'?'凌科标准编辑接口当前仅使用第 1 张参考图；需要多角度参考时请选择 Nano Banana Pro 或 APIYI GPT-Image':'当前提供商支持套图参考图列表';if(provider==='lingkeai')loadAISuiteModels();}
async function startPixPixAuthorization(){const r=await Api.startPixPixOAuth();if(r?.code!==200)return Toast.show(r?.msg||'无法发起 PixPix 授权','error');window.open(r.data.authorizationUrl,'_blank','noopener');Toast.show('请在新标签页完成 PixPix 授权，完成后返回此页刷新状态','info');}
async function loadPixPixTools(){const select=document.getElementById('pixpixTool');if(!select)return;const r=await Api.getPixPixTools();const tools=r?.code===200?(r.data||[]):[];select.innerHTML=tools.length?`<option value="">请选择工具</option>${tools.map(tool=>`<option value="${escapeAttr(tool.name)}">${escapeHtml(tool.name)}${tool.description?` — ${escapeHtml(tool.description)}`:''}</option>`).join('')}`:'<option value="">未发现可用图像工具</option>';}
async function generateAIImage(){const prompt=document.getElementById('aiImagePrompt').value.trim();if(!prompt)return Toast.show('请输入图片描述','warning');const btn=document.getElementById('generateImageBtn');const provider=document.getElementById('aiImageProvider')?.value||'default';const size=document.getElementById('aiImageSize').value;const ratio=size==='1024x1360'||size==='1024x1365'?'3:4':size==='1024x1536'?'2:3':size==='1536x1024'?'3:2':'1:1';const finalPrompt=`${prompt}\n\n画布必须使用 ${ratio} 比例，完整展示商品，保持参考图中的商品外观、颜色、结构和原有文字内容，不要重新发明商品。`;const model=provider==='yyds'?'gpt-image-2-cf':provider==='apiyi_banana'?'gemini-3-pro-image-preview':document.getElementById('aiImageModel').value.trim();btn.disabled=true;btn.innerHTML='<span class="media-spinner"></span> 正在生成';const r=provider==='pixpix'?await Api.generatePixPixImages({tool:document.getElementById('pixpixTool')?.value,arguments:{prompt:finalPrompt,size,count:Number(document.getElementById('aiImageCount').value)}}):await Api.generateAIImages({provider:['apiyi','apiyi_banana','yyds'].includes(provider)?provider:undefined,prompt:finalPrompt,size,count:Number(document.getElementById('aiImageCount').value),model,template_version:'single-v3',reference_image:aiImageReference?.dataUrl||undefined});btn.disabled=false;btn.innerHTML='<i data-lucide="wand-sparkles"></i> 开始生成';if(window.lucide)lucide.createIcons();if(r?.code!==200)return Toast.show(r?.msg||'生成失败','error');Toast.show(r.msg||'生成完成','success');await loadAIGenerationHistory();}

Router.register('/gallery', renderGalleryPage);
Router.register('/ai-image', renderAIImagePage);
