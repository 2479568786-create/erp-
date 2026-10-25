/**
 * OZON ERP - Ozon/1688/淘宝采集插件 Content Script
 * 功能：注入采集面板、采集商品数据、规格选择、成本信息填写、发送到桌面软件
 */

(function() {
  'use strict';

  // ============================================================
  // 配置
  // ============================================================
  const CONFIG = {
    LOCAL_API_URL: OZON_CONFIG.LOCAL_API_URL,
    STORAGE_KEY: 'ozon_collector_data',
    PANEL_POSITION_KEY: 'ozon_collector_position',
    COLLECTION_CONTEXT_KEY: OZON_CONFIG.COLLECTION_CONTEXT_KEY || 'collection_context',
    COLLECTION_CONTEXT_STATE_KEY: OZON_CONFIG.COLLECTION_CONTEXT_STATE_KEY || 'collection_context_state',
  };

  // 全局状态
  let state = {
    productData: null,
    skuList: [],
    selectedSkus: new Set(),
    expandedSkus: new Set(),
    selectedImages: new Set(),
    panelMinimized: false,
    isDragging: false,
  };

  // ============================================================
  // 工具函数
  // ============================================================
  function $(selector, root = document) {
    return root.querySelector(selector);
  }

  function $$(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  function createElement(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'class') el.className = v;
      else if (k === 'style') el.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    });
    children.forEach(c => {
      if (typeof c === 'string') el.appendChild(document.createTextNode(c));
      else if (c) el.appendChild(c);
    });
    return el;
  }

  function getProductId() {
    return OZON_EXTRACTORS.parseSource(location.href).source_product_id || Date.now().toString();
  }

  function getImageFilterDefaults() {
    return {
      minWidth: OZON_CONFIG.IMAGE_FILTER_DEFAULTS?.minWidth ?? 600,
      minHeight: OZON_CONFIG.IMAGE_FILTER_DEFAULTS?.minHeight ?? 600,
      maxCandidates: OZON_CONFIG.IMAGE_FILTER_DEFAULTS?.maxCandidates ?? 5,
    };
  }

  function resolveImageUrl(value) {
    if (!value) return '';
    try {
      const parsed = new URL(String(value), location.href);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
      return parsed.href;
    } catch (e) {
      return '';
    }
  }

  function readImageDimension(img, keys) {
    for (const key of keys) {
      const rawValue = img[key] ?? img.getAttribute?.(key);
      const value = Number(rawValue || 0);
      if (Number.isFinite(value) && value > 0) return value;
    }
    return 0;
  }

  function buildImageMetadata(img, source, position) {
    const url = resolveImageUrl(
      img.currentSrc || img.src || img.dataset.src || img.dataset.lazySrc || img.dataset.original || ''
    );
    if (!url) return null;

    const width = readImageDimension(img, ['width']) || readImageDimension(img, ['naturalWidth']);
    const height = readImageDimension(img, ['height']) || readImageDimension(img, ['naturalHeight']);
    const naturalWidth = readImageDimension(img, ['naturalWidth']) || width;
    const naturalHeight = readImageDimension(img, ['naturalHeight']) || height;
    const hasKnownDimensions = Boolean(width || height || naturalWidth || naturalHeight);

    const selectionReason = [source];
    if (!hasKnownDimensions) {
      if (source === 'sku') selectionReason.push('unknown_dimensions');
    } else if (naturalWidth >= getImageFilterDefaults().minWidth && naturalHeight >= getImageFilterDefaults().minHeight) {
      selectionReason.push('large_enough');
    } else {
      selectionReason.push('below_min_dimensions');
    }

    return {
      url,
      source,
      position,
      width,
      height,
      natural_width: naturalWidth,
      natural_height: naturalHeight,
      selection_reason: selectionReason,
    };
  }

  function buildImageCandidate(img, source, position) {
    const metadata = buildImageMetadata(img, source, position);
    if (!metadata) return null;
    if (metadata.selection_reason.includes('below_min_dimensions')) return null;
    if (metadata.selection_reason.includes('unknown_dimensions') && source !== 'sku') return null;
    return metadata;
  }

  function collectSkuImageCandidates(skuList) {
    const candidates = [];
    skuList.forEach((sku, index) => {
      const imageMeta = sku.image_meta || {
        url: sku.image,
        source: 'sku',
        position: index + 1,
      };
      const url = resolveImageUrl(imageMeta.url);
      if (!url) return;
      const hasKnownDimensions = Boolean(
        Number(imageMeta.width || 0) ||
        Number(imageMeta.height || 0) ||
        Number(imageMeta.natural_width || 0) ||
        Number(imageMeta.natural_height || 0)
      );
      candidates.push({
        ...imageMeta,
        url,
        source: 'sku',
        position: imageMeta.position || index + 1,
        selection_reason: Array.isArray(imageMeta.selection_reason) && imageMeta.selection_reason.length
          ? imageMeta.selection_reason.slice()
          : (hasKnownDimensions ? ['sku'] : ['sku', 'unknown_dimensions']),
      });
    });
    return candidates;
  }

  // ============================================================
  // 1688 页面数据采集
  // ============================================================
  function collectProductData() {
    const mainImageCandidates = extractMainImages();
    const skuList = extractSkuList();
    const detailImageCandidates = extractDetailImages();
    const skuImageCandidates = collectSkuImageCandidates(skuList);
    const filterDefaults = getImageFilterDefaults();
    const rankedCandidates = OZON_EXTRACTORS.limitImageCandidates(
      OZON_EXTRACTORS.rankImageCandidates(
        [...mainImageCandidates, ...skuImageCandidates, ...detailImageCandidates],
        filterDefaults
      ),
      filterDefaults.maxCandidates
    );

    const data = {
      product_id: getProductId(),
      ...OZON_EXTRACTORS.parseSource(location.href),
      source_url: location.href,
      title: extractTitle(),
      raw_description: extractDescription(),
      category_path: extractCategoryPath(),
      price: extractPrice(),
      attributes: extractAttributes(),
      main_images: rankedCandidates.filter(item => item.source === 'main_gallery').map(item => item.url).slice(0, 3),
      sku_list: skuList,
      detail_images: rankedCandidates.filter(item => item.source === 'detail').map(item => item.url).slice(0, 2),
      image_candidates: rankedCandidates,
      collected_at: new Date().toISOString(),
    };
    data.field_evidence = buildFieldEvidence(data);
    data.extraction_warnings = buildExtractionWarnings(data);
    return data;
  }

  function extractDescription() {
    const selectors = [
      '[data-widget*="webDescription"]',
      '[data-widget*="description"]',
      '#section-description',
      '#J_DivItemDesc', '#desc-lazyload-container', '[class*="descV8"]',
      '[class*="description"]', '[class*="detail-content"]', '[class*="content-detail"]'
    ];
    for (const sel of selectors) {
      const el = $(sel);
      const text = el ? el.innerText.trim() : '';
      if (text.length > 20) return text.replace(/\s+/g, ' ');
    }
    return '';
  }

  function extractCategoryPath() {
    const root = $('[data-widget*="breadCrumbs"]') || $('[class*="breadcrumb"]');
    return root ? Array.from(root.querySelectorAll('a, span')).map(item => item.textContent.trim()).filter(Boolean).join(' > ') : '';
  }

  function buildFieldEvidence(data) {
    return {
      title: { selector: 'platform title selectors', method: 'visible_dom' },
      raw_description: { selector: data.raw_description ? 'description selectors' : '', method: 'visible_dom' },
      attributes: { selector: Object.keys(data.attributes).length ? 'attribute selectors' : '', method: 'visible_dom' },
      main_images: { selector: data.main_images.length ? 'gallery selectors' : '', method: 'visible_dom' },
      sku: { selector: data.sku_list.length ? 'sku selectors' : '', method: 'visible_dom' },
    };
  }

  function buildExtractionWarnings(data) {
    const warnings = [];
    if (!data.title || data.title === document.title) warnings.push('标题可能来自页面标题，需人工核对');
    if (!data.raw_description) warnings.push('未检测到详情描述');
    if (!Object.keys(data.attributes).length) warnings.push('未检测到属性表');
    if (!data.main_images.length) warnings.push('未检测到主图');
    return warnings;
  }

  function extractTitle() {
    const selectors = [
      'h1[class*="title"]',
      '[data-testid="item-title"]',
      '.ItemHeader--mainTitle--',
      '.tb-main-title',
      '.title-content',
      '[class*="product-title"]',
      'h1',
    ];
    for (const sel of selectors) {
      const el = $(sel);
      if (el && el.textContent.trim().length > 5) {
        return el.textContent.trim();
      }
    }
    return document.title;
  }

  function extractPrice() {
    const selectors = [
      '[class*="price"] [class*="value"]',
      '[class*="Price--priceText"]',
      '.tb-rmb-num',
      '.price-content',
      '[class*="price-num"]',
      '[class*="price"] span',
    ];
    for (const sel of selectors) {
      const el = $(sel);
      if (el) {
        const match = el.textContent.match(/[\d.]+/);
        if (match) return match[0];
      }
    }
    return '';
  }

  function extractAttributes() {
    const attrs = {};
    const ozonRoot = $('[data-widget="webCharacteristics"]') || $('[data-widget*="webCharacteristics"]');
    if (ozonRoot) {
      ozonRoot.querySelectorAll('tr').forEach(row => {
        const cells = Array.from(row.querySelectorAll('th, td')).map(cell => cell.textContent.trim()).filter(Boolean);
        if (cells.length >= 2) attrs[cells[0]] = cells.slice(1).join(' ');
      });
      ozonRoot.querySelectorAll('dl').forEach(list => {
        const terms = Array.from(list.querySelectorAll('dt'));
        terms.forEach(term => {
          const value = term.nextElementSibling;
          if (value && value.tagName === 'DD' && term.textContent.trim() && value.textContent.trim()) {
            attrs[term.textContent.trim()] = value.textContent.trim();
          }
        });
      });
      Array.from(ozonRoot.children).forEach(row => {
        const cells = Array.from(row.children).map(cell => cell.textContent.trim()).filter(Boolean);
        if (cells.length >= 2 && cells[0].length < 100 && cells[1].length < 500) attrs[cells[0]] = cells.slice(1).join(' ');
      });
    }
    const selectors = [
      '[class*="attribute"] li',
      '[class*="Attrs--attr"]',
      '.attributes-list li',
      '[class*="param"] li',
      '[class*="props"] [class*="item"]',
      '.detail-attr-item',
    ];
    for (const sel of selectors) {
      const items = $$(sel);
      items.forEach(item => {
        const text = item.textContent.trim();
        if (text.includes('：')) {
          const [k, v] = text.split('：', 2);
          if (k && v) attrs[k.trim()] = v.trim();
        } else if (text.includes(':')) {
          const [k, v] = text.split(':', 2);
          if (k && v) attrs[k.trim()] = v.trim();
        }
      });
      if (Object.keys(attrs).length > 0) break;
    }
    return attrs;
  }

  function extractMainImages() {
    const candidates = [];
    const selectors = [
      '[data-widget*="webGallery"] img',
      '[data-widget*="gallery"] img',
      '[class*="main-image"] img',
      '[class*="PicGallery--"] img',
      '#J_UlThumb img',
      '[class*="gallery"] img',
      '.tab-trigger img',
      '[class*="carousel"] img',
      '[class*="swiper"] img',
    ];
    for (const sel of selectors) {
      const imgs = $$(sel);
      imgs.forEach((img) => {
        const candidate = buildImageCandidate(img, 'main_gallery', candidates.length + 1);
        if (candidate) candidates.push(candidate);
      });
      if (candidates.length > 0) break;
    }
    return candidates;
  }

  function extractSkuList() {
    const skus = [];
    const selectors = [
      '[class*="sku"] [class*="item"]',
      '[class*="SkuContent--"] [class*="valueItem"]',
      '.J_TSaleProp li',
      '[class*="spec"] [class*="item"]',
      '[class*="prop"] [class*="item"]',
      '.sku-item',
    ];

    for (const sel of selectors) {
      const items = $$(sel);
      items.forEach(item => {
        // 规格值
        const nameEl = item.querySelector('[class*="name"], span, em');
        const specValue = nameEl ? nameEl.textContent.trim() : '';

        // 规格图
        const imgEl = item.querySelector('img');
        const imgUrl = imgEl ? (imgEl.src || imgEl.dataset.src || '') : '';

        // 规格名称（向上找父级）
        let specName = '规格';
        let parent = item.parentElement;
        for (let i = 0; i < 5 && parent; i++) {
          const titleEl = parent.querySelector('[class*="title"], [class*="name"], label');
          if (titleEl && titleEl.textContent.trim()) {
            const t = titleEl.textContent.trim().replace(/[:：]/g, '').trim();
            if (t && t.length < 10) {
              specName = t;
              break;
            }
          }
          parent = parent.parentElement;
        }

        if (specValue && specValue.length < 30) {
          skus.push({
            spec_name: specName,
            spec_value: specValue,
            image: imgUrl,
            image_meta: imgEl ? buildImageMetadata(imgEl, 'sku', skus.length + 1) : null,
            purchase_price: '',
            shipping_price: '',
            net_weight: '',
            package_weight: '',
            package_size: { length: '', width: '', height: '' },
            product_size: { length: '', width: '', height: '' },
            auto_filled: [],
          });
        }
      });
      if (skus.length > 0) break;
    }

    // 去重
    const seen = new Set();
    const uniqueSkus = skus.filter(s => {
      const key = s.spec_name + '_' + s.spec_value;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    return uniqueSkus.length ? uniqueSkus : [{
      spec_name: '规格', spec_value: '默认规格', image: '', purchase_price: extractPrice(),
      shipping_price: '', net_weight: '', package_weight: '',
      package_size: { length: '', width: '', height: '' },
      product_size: { length: '', width: '', height: '' }, auto_filled: [],
    }];
  }

  function extractDetailImages() {
    const candidates = [];
    const selectors = [
      '[data-widget*="webDescription"] img',
      '[data-widget*="description"] img',
      '#desc-lazyload-container img',
      '#J_DivItemDesc img',
      '[class*="descV8"] img',
      '[class*="detail"] img',
      '[class*="description"] img',
      '[class*="content-detail"] img',
    ];
    for (const sel of selectors) {
      const imgs = $$(sel);
      imgs.forEach((img) => {
        const candidate = buildImageCandidate(img, 'detail', candidates.length + 1);
        if (candidate) candidates.push(candidate);
      });
      if (candidates.length > 0) break;
    }
    return candidates;
  }

  // ============================================================
  // 价格自动监听（用户切换SKU时自动抓取价格）
  // ============================================================
  function setupPriceObserver() {
    const priceSelectors = [
      '[class*="price"] [class*="value"]',
      '.price-content',
      '[class*="price-num"]',
    ];

    let targetEl = null;
    for (const sel of priceSelectors) {
      targetEl = $(sel);
      if (targetEl) break;
    }

    if (!targetEl) return;

    const observer = new MutationObserver(() => {
      const newPrice = extractPrice();
      if (newPrice) {
        // 找到当前选中的SKU，自动填充价格
        const activeSku = document.querySelector('[class*="sku"] [class*="item"][class*="active"], [class*="sku"] [class*="item"][class*="selected"], .sku-item.active');
        if (activeSku) {
          const nameEl = activeSku.querySelector('[class*="name"], span, em');
          const value = nameEl ? nameEl.textContent.trim() : '';
          const sku = state.skuList.find(s => s.spec_value === value);
          if (sku && !sku.purchase_price) {
            sku.purchase_price = newPrice;
            sku.auto_filled.push('purchase_price');
            updateSkuListUI();
          }
        }
      }
    });

    observer.observe(targetEl, { childList: true, subtree: true, characterData: true });
  }

  // ============================================================
  // 采集面板 UI
  // ============================================================
  let panelEl = null;

  function createPanel() {
    if (panelEl) return;

    panelEl = createElement('div', { id: 'ozon-collector-panel' }, [
      // 头部
      createElement('div', { class: 'panel-header' }, [
        createElement('div', { class: 'panel-title' }, [
          createElement('span', {}, ['📦']),
          createElement('span', {}, ['OZON ERP 采集器 v1.5']),
        ]),
        createElement('div', { class: 'minimized-icon' }, ['📦']),
        createElement('div', { class: 'panel-actions' }, [
          createElement('button', { class: 'panel-btn', title: '最小化', onclick: toggleMinimize }, ['—']),
          createElement('button', { class: 'panel-btn', title: '关闭', onclick: closePanel }, ['✕']),
        ]),
      ]),
      // 状态提示
      createElement('div', { class: 'panel-body' }, [
        createElement('div', { class: 'status-message', id: 'ozon-status-msg' }),
        // 商品信息
        createElement('div', { class: 'product-info', id: 'ozon-product-info' }),
        // 规格列表
        createElement('div', { class: 'sku-section-title' }, [
          createElement('span', {}, ['规格列表']),
          createElement('div', { class: 'sku-quick-actions' }, [
            createElement('button', { class: 'sku-quick-btn', onclick: selectAllSkus }, ['全选']),
            createElement('button', { class: 'sku-quick-btn', onclick: expandAllSkus }, ['全展开']),
          ]),
        ]),
        createElement('div', { id: 'ozon-sku-list' }),
        // 图片采集区
        createElement('div', { class: 'sku-section-title' }, [
          createElement('span', {}, ['商品图片']),
          createElement('span', { id: 'ozon-image-count', style: 'font-size:11px;color:#999;font-weight:400;margin-left:6px;' }, ['']),
          createElement('div', { class: 'sku-quick-actions' }, [
            createElement('button', { class: 'sku-quick-btn', onclick: selectAllImages }, ['全选']),
            createElement('button', { class: 'sku-quick-btn', onclick: selectMainImages }, ['仅主图']),
            createElement('button', { class: 'sku-quick-btn', onclick: clearImages }, ['清空']),
          ]),
        ]),
        createElement('div', { id: 'ozon-image-list' }),
      ]),
      // 底部
      createElement('div', { class: 'panel-footer' }, [
        createElement('button', { class: 'image-collect-btn', id: 'ozon-image-btn', onclick: collectImages }, ['🖼 采集图片到 ERP 图库']),
        createElement('button', { class: 'collect-btn', id: 'ozon-collect-btn', onclick: sendToDesktop }, ['一键采集到OZON ERP']),
        createElement('div', { class: 'footer-hint' }, ['采集将发送到你在插件里配置的 ERP 地址']),
      ]),
    ]);

    document.body.appendChild(panelEl);

    // 恢复位置
    restorePanelPosition();
    // 启用拖拽
    setupDrag();
  }

  function updateProductInfoUI() {
    const info = $('#ozon-product-info');
    if (!info || !state.productData) return;
    info.innerHTML = `
      <div class="product-title">${state.productData.title}</div>
      <div class="product-stats">
        <span>${state.skuList.length}</span>个规格
        <span>${state.productData.main_images.length}</span>张主图
        <span>${state.productData.detail_images.length}</span>张详情图
      </div>
    `;
  }

  // ============================================================
  // 图片采集 UI 与逻辑
  // ============================================================
  function getImageCandidates() {
    return (state.productData && state.productData.image_candidates) || [];
  }

  function updateImageListUI() {
    const container = $('#ozon-image-list');
    if (!container) return;
    const candidates = getImageCandidates();
    if (!candidates.length) {
      container.innerHTML = '<div style="text-align:center;color:#999;padding:12px;font-size:12px;">未检测到商品图片</div>';
      return;
    }
    if (state.selectedImages.size === 0) {
      // 首次打开默认选中全部主图
      candidates.filter(c => c.source === 'main_gallery').forEach(c => state.selectedImages.add(c.url));
    }
    container.innerHTML = '';
    const SOURCE_LABELS = { main_gallery: '主图', sku: '规格图', detail: '详情图' };
    candidates.forEach((cand, index) => {
      const isSelected = state.selectedImages.has(cand.url);
      const item = createElement('div', {
        class: 'image-item' + (isSelected ? ' selected' : ''),
        title: SOURCE_LABELS[cand.source] || cand.source,
      });
      const img = document.createElement('img');
      img.src = cand.url;
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      img.onerror = () => { img.style.visibility = 'hidden'; };
      item.appendChild(img);
      const badge = createElement('div', { class: 'image-badge' }, [SOURCE_LABELS[cand.source] || '图']);
      item.appendChild(badge);
      if (isSelected) {
        item.appendChild(createElement('div', { class: 'image-check' }, ['✓']));
      }
      item.addEventListener('click', () => {
        if (state.selectedImages.has(cand.url)) state.selectedImages.delete(cand.url);
        else state.selectedImages.add(cand.url);
        updateImageListUI();
      });
      container.appendChild(item);
    });
    const countEl = $('#ozon-image-count');
    if (countEl) countEl.textContent = `已选 ${state.selectedImages.size}/${candidates.length}`;
  }

  function selectAllImages() {
    getImageCandidates().forEach(c => state.selectedImages.add(c.url));
    updateImageListUI();
  }

  function selectMainImages() {
    state.selectedImages = new Set(
      getImageCandidates().filter(c => c.source === 'main_gallery').map(c => c.url)
    );
    updateImageListUI();
  }

  function clearImages() {
    state.selectedImages = new Set();
    updateImageListUI();
  }

  async function collectImages() {
    const btn = $('#ozon-image-btn');
    const picked = getImageCandidates().filter(c => state.selectedImages.has(c.url));
    if (!picked.length) {
      showStatus('请先点选要采集的图片', 'error');
      return;
    }
    if (!state.productData) {
      showStatus('商品信息尚未就绪，请稍候再试', 'error');
      return;
    }
    if (btn) { btn.disabled = true; btn.textContent = '图片采集中...'; }
    try {
      // 经 background 代发，绕过 https 页面请求 http 的混合内容限制
      const data = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          { type: 'COLLECT_IMAGES', payload: {
            product_title: state.productData.title || '',
            source_url: state.productData.source_url || location.href,
            images: picked.map(c => ({ url: c.url, source: c.source })),
          } },
          (response) => {
            if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
            else if (!response || !response.success) reject(new Error((response && response.error) || '未收到响应'));
            else resolve(response.data);
          }
        );
      });
      const failNote = (data.failed && data.failed.length) ? `，${data.failed.length} 张失败` : '';
      showStatus(`已采集 ${data.saved} 张图片到 ERP 图库${failNote}`, data.saved ? 'success' : 'error');
      chrome.notifications?.create?.({
        type: 'basic',
        iconUrl: 'icons/icon48.png',
        title: 'OZON ERP 采集',
        message: `已采集 ${data.saved} 张图片到 ERP 图库`,
      });
    } catch (e) {
      showStatus('图片采集失败: ' + (e.message || e), 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '🖼 采集图片到 ERP 图库'; }
    }
  }

  function updateSkuListUI() {
    const container = $('#ozon-sku-list');
    if (!container) return;

    if (state.skuList.length === 0) {
      container.innerHTML = '<div style="text-align:center;color:#999;padding:20px;font-size:12px;">未检测到规格，该商品可能无多规格</div>';
      return;
    }

    container.innerHTML = '';
    state.skuList.forEach((sku, index) => {
      const skuKey = sku.spec_name + '_' + sku.spec_value;
      const isSelected = state.selectedSkus.has(skuKey);
      const isExpanded = state.expandedSkus.has(skuKey);

      const item = createElement('div', {
        class: 'sku-item' + (isSelected ? ' selected' : '') + (isExpanded ? ' expanded' : ''),
        'data-index': index,
      });

      // 头部
      const header = createElement('div', { class: 'sku-header', onclick: (e) => toggleSkuExpand(index, e) });
      header.innerHTML = `
        <input type="checkbox" class="sku-checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation()">
        ${sku.image ? `<img src="${sku.image}" class="sku-image" alt="">` : '<div class="sku-image" style="background:#eee;"></div>'}
        <span class="sku-name">${sku.spec_name}：${sku.spec_value}</span>
        ${sku.purchase_price ? `<span class="sku-price-tag ${sku.auto_filled.includes('purchase_price') ? 'auto' : ''}">¥${sku.purchase_price}${sku.auto_filled.includes('purchase_price') ? '✓' : ''}</span>` : ''}
        <span class="sku-toggle">▶</span>
      `;

      // 复选框事件
      const checkbox = header.querySelector('.sku-checkbox');
      checkbox.addEventListener('change', (e) => {
        e.stopPropagation();
        if (checkbox.checked) state.selectedSkus.add(skuKey);
        else state.selectedSkus.delete(skuKey);
        item.classList.toggle('selected', checkbox.checked);
        saveData();
      });

      item.appendChild(header);

      // 详情（表单）
      const detail = createElement('div', { class: 'sku-detail' });
      detail.innerHTML = `
        <div class="form-group-title">价格信息</div>
        <div class="form-row">
          <span class="form-label">采购价</span>
          <input type="number" class="form-input ${sku.auto_filled.includes('purchase_price') ? 'auto-filled' : ''}" 
                 value="${sku.purchase_price}" placeholder="元" data-field="purchase_price" step="0.01">
          <span class="form-unit">元</span>
        </div>
        <div class="form-row">
          <span class="form-label">运费</span>
          <input type="number" class="form-input" value="${sku.shipping_price}" placeholder="元" data-field="shipping_price" step="0.01">
          <span class="form-unit">元</span>
        </div>

        <div class="form-group-title">重量信息</div>
        <div class="form-row">
          <span class="form-label">净重</span>
          <input type="number" class="form-input" value="${sku.net_weight}" placeholder="g" data-field="net_weight">
          <span class="form-unit">g</span>
          <button class="copy-btn" data-copy="weight">复制到包装</button>
        </div>
        <div class="form-row">
          <span class="form-label">包装重</span>
          <input type="number" class="form-input" value="${sku.package_weight}" placeholder="g" data-field="package_weight">
          <span class="form-unit">g</span>
        </div>

        <div class="form-group-title">包装尺寸（发货用）</div>
        <div class="form-row">
          <span class="form-label">长宽高</span>
          <div class="size-inputs">
            <input type="number" class="form-input" value="${sku.package_size.length}" placeholder="长" data-field="package_size.length">
            <span class="size-sep">×</span>
            <input type="number" class="form-input" value="${sku.package_size.width}" placeholder="宽" data-field="package_size.width">
            <span class="size-sep">×</span>
            <input type="number" class="form-input" value="${sku.package_size.height}" placeholder="高" data-field="package_size.height">
          </div>
          <span class="form-unit">cm</span>
        </div>

        <div class="form-group-title">产品外观尺寸</div>
        <div class="form-row">
          <span class="form-label">长宽高</span>
          <div class="size-inputs">
            <input type="number" class="form-input" value="${sku.product_size.length}" placeholder="长" data-field="product_size.length">
            <span class="size-sep">×</span>
            <input type="number" class="form-input" value="${sku.product_size.width}" placeholder="宽" data-field="product_size.width">
            <span class="size-sep">×</span>
            <input type="number" class="form-input" value="${sku.product_size.height}" placeholder="高" data-field="product_size.height">
          </div>
          <span class="form-unit">cm</span>
        </div>
      `;

      // 表单输入事件
      detail.querySelectorAll('.form-input').forEach(input => {
        input.addEventListener('input', () => {
          const field = input.dataset.field;
          if (field.includes('.')) {
            const [parent, child] = field.split('.');
            sku[parent][child] = input.value;
          } else {
            sku[field] = input.value;
          }
          saveData();
        });
      });

      // 复制按钮
      const copyBtn = detail.querySelector('.copy-btn');
      if (copyBtn) {
        copyBtn.addEventListener('click', () => {
          sku.package_weight = sku.net_weight;
          updateSkuListUI();
          saveData();
        });
      }

      item.appendChild(detail);
      container.appendChild(item);
    });
  }

  function toggleSkuExpand(index, e) {
    if (e && e.target.classList.contains('sku-checkbox')) return;
    const sku = state.skuList[index];
    const key = sku.spec_name + '_' + sku.spec_value;
    if (state.expandedSkus.has(key)) state.expandedSkus.delete(key);
    else state.expandedSkus.add(key);
    updateSkuListUI();
  }

  function selectAllSkus() {
    const allSelected = state.selectedSkus.size === state.skuList.length;
    if (allSelected) {
      state.selectedSkus.clear();
    } else {
      state.skuList.forEach(s => state.selectedSkus.add(s.spec_name + '_' + s.spec_value));
    }
    updateSkuListUI();
    saveData();
  }

  function expandAllSkus() {
    const allExpanded = state.expandedSkus.size === state.skuList.length;
    if (allExpanded) {
      state.expandedSkus.clear();
    } else {
      state.skuList.forEach(s => state.expandedSkus.add(s.spec_name + '_' + s.spec_value));
    }
    updateSkuListUI();
  }

  // ============================================================
  // 拖拽
  // ============================================================
  function setupDrag() {
    const header = panelEl.querySelector('.panel-header');
    let startX, startY, startLeft, startTop;

    header.addEventListener('mousedown', (e) => {
      if (e.target.closest('.panel-btn')) return;
      state.isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      const rect = panelEl.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!state.isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      panelEl.style.left = (startLeft + dx) + 'px';
      panelEl.style.top = (startTop + dy) + 'px';
      panelEl.style.right = 'auto';
    });

    document.addEventListener('mouseup', () => {
      if (state.isDragging) {
        state.isDragging = false;
        savePanelPosition();
      }
    });
  }

  function savePanelPosition() {
    const rect = panelEl.getBoundingClientRect();
    chrome.storage.local.set({
      [CONFIG.PANEL_POSITION_KEY]: { left: rect.left, top: rect.top }
    });
  }

  function restorePanelPosition() {
    chrome.storage.local.get(CONFIG.PANEL_POSITION_KEY, (result) => {
      const pos = result[CONFIG.PANEL_POSITION_KEY];
      if (pos && panelEl) {
        panelEl.style.left = pos.left + 'px';
        panelEl.style.top = pos.top + 'px';
        panelEl.style.right = 'auto';
      }
    });
  }

  // ============================================================
  // 最小化/关闭
  // ============================================================
  function toggleMinimize() {
    state.panelMinimized = !state.panelMinimized;
    panelEl.classList.toggle('minimized', state.panelMinimized);
    if (state.panelMinimized) {
      panelEl.title = '点击展开OZON ERP 采集器 v1.5';
    } else {
      panelEl.title = '';
    }
  }

  function closePanel() {
    if (panelEl) {
      panelEl.remove();
      panelEl = null;
    }
  }

  // 最小化状态下点击展开
  document.addEventListener('click', (e) => {
    if (state.panelMinimized && panelEl && panelEl.contains(e.target)) {
      toggleMinimize();
    }
  });

  // ============================================================
  // 状态提示
  // ============================================================
  function showStatus(message, type = 'info') {
    const el = $('#ozon-status-msg');
    if (!el) return;
    el.className = 'status-message show ' + type;
    el.textContent = message;
    setTimeout(() => {
      el.classList.remove('show');
    }, 4000);
  }

  // ============================================================
  // 数据暂存
  // ============================================================
  function saveData() {
    const data = {
      product_id: state.productData?.product_id,
      sku_list: state.skuList,
      selected_skus: Array.from(state.selectedSkus),
      expanded_skus: Array.from(state.expandedSkus),
    };
    chrome.storage.local.set({ [CONFIG.STORAGE_KEY + '_' + getProductId()]: data });
  }

  function loadSavedData() {
    const key = CONFIG.STORAGE_KEY + '_' + getProductId();
    chrome.storage.local.get(key, (result) => {
      const saved = result[key];
      if (saved && saved.sku_list) {
        state.skuList = saved.sku_list;
        state.selectedSkus = new Set(saved.selected_skus || []);
        state.expandedSkus = new Set(saved.expanded_skus || []);
        updateSkuListUI();
      }
    });
  }

  // ============================================================
  // 发送到桌面软件
  // ============================================================
  async function getCollectionContext() {
    const result = await chrome.storage.local.get(CONFIG.COLLECTION_CONTEXT_KEY);
    return result[CONFIG.COLLECTION_CONTEXT_KEY] || null;
  }

  async function getCollectionContextState() {
    const result = await chrome.storage.local.get(CONFIG.COLLECTION_CONTEXT_STATE_KEY);
    return result[CONFIG.COLLECTION_CONTEXT_STATE_KEY] || 'unknown';
  }

  async function sendToDesktop() {
    if (state.selectedSkus.size === 0) {
      showStatus('请至少选择一个规格', 'error');
      return;
    }

    const btn = $('#ozon-collect-btn');
    btn.disabled = true;
    btn.textContent = '采集中...';

    // 构建选中的SKU数据
    const selectedSkuData = state.skuList.filter(s =>
      state.selectedSkus.has(s.spec_name + '_' + s.spec_value)
    ).map(sku => ({
      ...sku,
      purchase_price: sku.purchase_price || '0',
      shipping_price: sku.shipping_price || '0',
    }));

    let context = await getCollectionContext();
    const contextState = await getCollectionContextState();
    if (contextState === 'unavailable') {
      await chrome.storage.local.remove(CONFIG.COLLECTION_CONTEXT_KEY);
      context = null;
    }
    if (!context?.store_id || !context?.operator_member_id || !context?.collection_batch_id) {
      // 新 ERP 为单用户系统，店铺归属非必填：未确认店铺时直接按「待归属」采集，不阻断
      showStatus('待归属采集（可在 ERP 中再指定店铺）', 'info');
    }
    const payload = {
      source_platform: state.productData.source_platform,
      source_product_id: state.productData.source_product_id,
      title: state.productData.title,
      raw_description: state.productData.raw_description,
      category_path: state.productData.category_path,
      source_url: state.productData.source_url,
      attributes: state.productData.attributes,
      field_evidence: state.productData.field_evidence,
      extraction_warnings: state.productData.extraction_warnings,
      main_images: state.productData.main_images,
      detail_images: state.productData.detail_images,
      selected_skus: selectedSkuData,
      collected_from: 'browser_extension',
      collected_at: new Date().toISOString(),
    };
    if (context?.store_id && context?.operator_member_id && context?.collection_batch_id) {
      payload.store_id = context.store_id;
      payload.operator_member_id = context.operator_member_id;
      payload.collection_batch_id = context.collection_batch_id;
    }

    try {
      // 通过 background service worker 发送，避免 https 页面请求 http 的混合内容限制
      const result = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({ type: 'SUBMIT_COLLECTION', payload }, (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else {
            resolve(response);
          }
        });
      });

      if (result && result.success) {
        showStatus('采集成功！已发送到OZON ERP', 'success');
        // 通知background打开 ERP 结果页
        chrome.runtime.sendMessage({
          type: 'COLLECT_SUCCESS',
          data: result.data,
        });
      } else if (result?.queued) {
        showStatus('已保存，ERP 恢复后自动补发', 'info');
      } else {
        showStatus('发送失败: ' + (result?.error || '未知错误'), 'error');
      }
    } catch (e) {
      showStatus('无法连接OZON ERP，请检查服务是否在线', 'error');
      console.error('发送失败:', e);
    } finally {
      btn.disabled = false;
      btn.textContent = '一键采集到OZON ERP';
    }
  }

  // ============================================================
  // 初始化
  // ============================================================
  function init() {
    // 只在商品详情页运行
    const source = OZON_EXTRACTORS.parseSource(location.href);
    if (!source.source_platform || !source.source_product_id) {
      return;
    }

    // 延迟等待页面加载
    setTimeout(() => {
      state.productData = collectProductData();
      state.skuList = state.productData.sku_list;
      state.skuList.forEach(sku => state.selectedSkus.add(sku.spec_name + '_' + sku.spec_value));

      createPanel();
      updateProductInfoUI();
      updateSkuListUI();
      updateImageListUI();
      loadSavedData();
      setupPriceObserver();

      console.log('[OZON ERP] 采集面板已加载，检测到', state.skuList.length, '个规格');
    }, 2000);
  }

  // 监听来自popup的消息
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'OPEN_PANEL') {
      if (!panelEl) {
        init();
      } else if (state.panelMinimized) {
        toggleMinimize();
      }
      sendResponse({ success: true });
    }
    if (message.type === 'GET_COLLECTED_DATA') {
      sendResponse({
        product: state.productData,
        skus: state.skuList,
        selected: Array.from(state.selectedSkus),
      });
    }
  });

  // 启动
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
