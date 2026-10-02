/* Persist only validated search parameters, never credentials or CSRF. */
const SearchConfig = (() => {
  const key = 'scout.search.config.v1';
  function validate(data, sources, limits) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const fields = ['sources', ...Object.keys(limits)];
    if ('platform' in data) {
      if (!['green', 'forkwell', 'lapras', 'findy', 'type'].includes(data.platform)) return null;
      fields.push('platform');
    }
    if (Object.keys(data).length !== fields.length ||
        Object.keys(data).some(name => !fields.includes(name))) return null;
    if (!Array.isArray(data.sources) || !data.sources.length ||
        new Set(data.sources).size !== data.sources.length ||
        data.sources.some(source => !sources.includes(source))) return null;
    const config = {sources: [...data.sources]};
    if ('platform' in data) config.platform = data.platform;
    for (const [name, [defaultValue, low, high]] of Object.entries(limits)) {
      const value = ['lapras', 'type'].includes(data.platform) && ['coverage_pages', 'max_depth'].includes(name)
        ? defaultValue : data[name];
      if (!Number.isInteger(value) || value < low || value > high) return null;
      config[name] = value;
    }
    return config;
  }
  function sources(form) {
    return [...form.querySelectorAll('[name=sources]')].map(input => input.value);
  }
  function restore(form, limits, renderSources) {
    try {
      const data = JSON.parse(sessionStorage.getItem(key));
      if (data && ['green', 'forkwell', 'lapras', 'findy', 'type'].includes(data.platform) && form.elements?.platform) {
        const labels = data.platform === 'type' ? ['サーバ・クラウド（設計・構築）', 'DevOps・SRE'] : data.platform === 'findy' ? ['おすすめ求人'] : data.platform === 'lapras' ? ['求人検索'] : data.platform === 'forkwell' ? ['求人一覧'] : sources(form);
        if (!validate(data, labels, limits)) return;
        form.elements.platform.value = data.platform;
        renderSources?.();
      }
      const config = validate(data, sources(form), limits);
      if (!config) return;
      for (const input of form.querySelectorAll('[name=sources]')) {
        input.checked = config.sources.includes(input.value);
      }
      for (const input of form.querySelectorAll('[type=number]')) input.value = config[input.name];
    } catch { /* Unavailable storage or malformed JSON: retain server defaults. */ }
  }
  function save(form, limits) {
    const data = {sources: [...form.querySelectorAll('[name=sources]:checked')].map(input => input.value)};
    if (form.elements?.platform) data.platform = form.elements.platform.value;
    for (const input of form.querySelectorAll('[type=number]')) data[input.name] = Number(input.value);
    const config = validate(data, sources(form), limits);
    if (!config) return false;
    try { sessionStorage.setItem(key, JSON.stringify(config)); }
    catch { /* Search remains usable when browser storage is unavailable. */ }
    return true;
  }
  return {validate, restore, save};
})();
