(function(root) {
  const config = {
    LOCAL_API_URL: 'https://185.209.28.217',
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
