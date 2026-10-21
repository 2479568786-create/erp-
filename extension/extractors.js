(function(root) {
  function parseSource(url) {
    const parsed = new URL(url);
    if (parsed.hostname.endsWith('1688.com')) {
      const match = parsed.pathname.match(/offer\/(\d+)/);
      return { source_platform: '1688', source_product_id: match?.[1] || parsed.searchParams.get('id') || '' };
    }
    if (parsed.hostname.endsWith('taobao.com') || parsed.hostname.endsWith('tmall.com')) {
      return { source_platform: 'TAOBAO', source_product_id: parsed.searchParams.get('id') || '' };
    }
    if (parsed.hostname === 'ozon.ru' || parsed.hostname.endsWith('.ozon.ru')) {
      const path = parsed.pathname.replace(/\/+$/, '');
      const match = path.match(/(?:^|[-/])(\d+)$/);
      return { source_platform: 'OZON', source_product_id: match?.[1] || parsed.searchParams.get('product_id') || '' };
    }
    return { source_platform: '', source_product_id: '' };
  }

  function normalizeImageUrl(value) {
    if (!value) return '';
    try {
      const parsed = new URL(String(value));
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
      ['width', 'height', 'size'].forEach((key) => parsed.searchParams.delete(key));
      return parsed.toString();
    } catch (e) {
      return '';
    }
  }

  function toFiniteNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function rankImageCandidates(candidates, options = {}) {
    const minWidth = options.minWidth ?? 600;
    const minHeight = options.minHeight ?? 600;
    const sourceOrder = { main_gallery: 0, sku: 1, detail: 2 };

    const ranked = (Array.isArray(candidates) ? candidates : [])
      .map((candidate, index) => {
        const url = normalizeImageUrl(candidate?.url);
        if (!url) return null;

        const source = candidate?.source || 'detail';
        if (!(source in sourceOrder)) return null;

        const naturalWidth = toFiniteNumber(candidate?.natural_width ?? candidate?.naturalWidth);
        const naturalHeight = toFiniteNumber(candidate?.natural_height ?? candidate?.naturalHeight);
        const width = toFiniteNumber(candidate?.width ?? naturalWidth);
        const height = toFiniteNumber(candidate?.height ?? naturalHeight);
        const effectiveWidth = naturalWidth ?? width ?? 0;
        const effectiveHeight = naturalHeight ?? height ?? 0;
        const hasKnownDimensions = [naturalWidth, naturalHeight, width, height].some((value) => value !== null && value > 0);

        if (!hasKnownDimensions) {
          if (source !== 'sku') return null;
        } else if (effectiveWidth < minWidth || effectiveHeight < minHeight) {
          return null;
        }

        const selectionReason = Array.isArray(candidate?.selection_reason) ? candidate.selection_reason.slice() : [];
        if (!selectionReason.includes(source)) selectionReason.unshift(source);
        if (!hasKnownDimensions && source === 'sku') {
          if (!selectionReason.includes('unknown_dimensions')) selectionReason.push('unknown_dimensions');
        } else if (!selectionReason.includes('large_enough')) {
          selectionReason.push('large_enough');
        }

        return {
          ...candidate,
          url,
          source,
          position: toFiniteNumber(candidate?.position) ?? index + 1,
          width: width ?? effectiveWidth,
          height: height ?? effectiveHeight,
          natural_width: naturalWidth ?? effectiveWidth,
          natural_height: naturalHeight ?? effectiveHeight,
          selection_reason: selectionReason,
          _rank_source_order: sourceOrder[source],
          _rank_input_order: index,
        };
      })
      .filter(Boolean)
      .sort((a, b) => {
        if (a._rank_source_order !== b._rank_source_order) {
          return a._rank_source_order - b._rank_source_order;
        }
        if (a.position !== b.position) {
          return a.position - b.position;
        }
        return a._rank_input_order - b._rank_input_order;
      });

    const seen = new Set();
    return ranked
      .filter((candidate) => {
        if (seen.has(candidate.url)) return false;
        seen.add(candidate.url);
        return true;
      })
      .map(({ _rank_source_order, _rank_input_order, ...candidate }) => candidate);
  }

  function limitImageCandidates(candidates, limit = 5) {
    return (Array.isArray(candidates) ? candidates : []).slice(0, limit);
  }

  const extractors = { parseSource, normalizeImageUrl, rankImageCandidates, limitImageCandidates };
  root.OZON_EXTRACTORS = extractors;
  if (typeof module !== 'undefined' && module.exports) module.exports = extractors;
})(typeof globalThis !== 'undefined' ? globalThis : this);
