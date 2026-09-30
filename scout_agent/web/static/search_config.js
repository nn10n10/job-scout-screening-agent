/* Persist only validated search parameters, never credentials or CSRF. */
const SearchConfig = (() => {
  const key = 'scout.search.config.v1';
  function validate(data, sources, limits) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const fields = ['sources', ...Object.keys(limits)];
    if (Object.keys(data).length !== fields.length ||
        Object.keys(data).some(name => !fields.includes(name))) return null;
    if (!Array.isArray(data.sources) || !data.sources.length ||
        new Set(data.sources).size !== data.sources.length ||
        data.sources.some(source => !sources.includes(source))) return null;
    const config = {sources: [...data.sources]};
    for (const [name, [, low, high]] of Object.entries(limits)) {
      const value = data[name];
      if (!Number.isInteger(value) || value < low || value > high) return null;
      config[name] = value;
    }
    return config;
  }
  function sources(form) {
    return [...form.querySelectorAll('[name=sources]')].map(input => input.value);
  }
  function restore(form, limits) {
    try {
      const config = validate(JSON.parse(sessionStorage.getItem(key)), sources(form), limits);
      if (!config) return;
      for (const input of form.querySelectorAll('[name=sources]')) {
        input.checked = config.sources.includes(input.value);
      }
      for (const input of form.querySelectorAll('[type=number]')) input.value = config[input.name];
    } catch { /* Unavailable storage or malformed JSON: retain server defaults. */ }
  }
  function save(form, limits) {
    const data = {sources: [...form.querySelectorAll('[name=sources]:checked')].map(input => input.value)};
    for (const input of form.querySelectorAll('[type=number]')) data[input.name] = Number(input.value);
    const config = validate(data, sources(form), limits);
    if (!config) return false;
    try { sessionStorage.setItem(key, JSON.stringify(config)); }
    catch { /* Search remains usable when browser storage is unavailable. */ }
    return true;
  }
  return {validate, restore, save};
})();
