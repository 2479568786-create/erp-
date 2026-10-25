(function(root) {
  const config = {
    // 不写死任何线上地址：由 popup 保存到 chrome.storage.local['desktop_api_url']
    LOCAL_API_URL: '',
    IMAGE_FILTER_DEFAULTS: {
      minWidth: 600,
      minHeight: 600,
      maxCandidates: 5,
    },
    COLLECTION_CONTEXT_KEY: 'collection_context',
    COLLECTION_CONTEXT_STATE_KEY: 'collection_context_state',
  };
  root.OZON_CONFIG = config;
  if (typeof module !== 'undefined' && module.exports) module.exports = config;
})(typeof globalThis !== 'undefined' ? globalThis : this);
