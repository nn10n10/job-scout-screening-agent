"""Content-free Findy structure evidence, independent of production parsing."""
import re
from urllib.parse import urlsplit

LABELS = ('仕事内容', '応募資格', '必須要件', '必須スキル', '歓迎要件', '歓迎スキル', '給与', '年収', '勤務地', '開発環境', '技術', '勤務時間', '福利厚生', '雇用形態')
TAGS = frozenset('html body main article section div span h1 h2 h3 h4 h5 h6 p dt dd dl ul ol li table tbody tr th td label button a header footer nav aside'.split())
ROLES = frozenset({'main', 'article', 'section', 'region', 'tab', 'tabpanel', 'heading'})
RELATIONS = frozenset({'same-parent-next-sibling', 'same-parent-following-sibling', 'ancestor-next-sibling',
                       'nested-next-block', 'tab-panel'})

def exact_detail(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.netloc == 'findy-code.io'
                and not re.search(r'[\s\x00-\x1f\x7f]', url)
                and re.fullmatch(r'/companies/[0-9]+/jobs/[A-Za-z0-9_-]+', parts.path) is not None)
    except (ValueError, TypeError):
        return False


STRUCTURE = r"""() => {
 if (location.protocol !== "https:" || location.host !== "findy-code.io" || !/^\/companies\/[0-9]+\/jobs\/[A-Za-z0-9_-]+$/.test(location.pathname)) return {};
 const labels = ["仕事内容", "応募資格", "必須要件", "必須スキル", "歓迎要件", "歓迎スキル", "給与", "年収", "勤務地", "開発環境", "技術", "勤務時間", "福利厚生", "雇用形態"];
 const tags = new Set('html body main article section div span h1 h2 h3 h4 h5 h6 p dt dd dl ul ol li table tbody tr th td label button a header footer nav aside'.split(' '));
 const roles = new Set(['main','article','section','region','tab','tabpanel','heading']);
 const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
 const text = e => (e.textContent || '').replace(/\s+/g, ' ').trim();
 const descriptor = e => {
   const tag = (e.tagName || '').toLowerCase();
   const role = e.getAttribute('role');
   const implicit = ['main','article','section'].includes(tag) ? tag : /^h[1-6]$/.test(tag) ? 'heading' : null;
   return {tag: tags.has(tag) ? tag : null, role: roles.has(role) ? role : null,
           semantic_role: roles.has(role) ? role : implicit};
 };
 const all = Array.from(document.querySelectorAll('*')).filter(visible);
 // Prefer deepest exact matches: wrappers containing only a heading are duplicates.
 const matches = all.filter(e => labels.includes(text(e)) &&
   !Array.from(e.children).some(c => text(c) === text(e)));
 const panel = e => {
   for (let p = e; p; p = p.parentElement) if (p.getAttribute('role') === 'tabpanel') return p;
   return null;
 };
 const relations = (e, next) => {
   const out = [];
   if (next && e.nextElementSibling === next) out.push('same-parent-next-sibling');
   if (next && e.parentElement === next.parentElement && e.nextElementSibling !== next) out.push('same-parent-following-sibling');
   let p = e.parentElement;
   for (let depth = 0; p && depth < 3; depth++, p = p.parentElement) {
     if (next && p.nextElementSibling &&
         (p.nextElementSibling === next || p.nextElementSibling.contains(next))) out.push('ancestor-next-sibling');
   }
   if (next && e.nextElementSibling && e.nextElementSibling !== next &&
       e.nextElementSibling.contains(next)) out.push('nested-next-block');
   if (panel(e) || (next && panel(next))) out.push('tab-panel');
   return [...new Set(out)];
 };
 const records = matches.slice(0, 100).map(e => {
   const ancestors = [];
   for (let p = e.parentElement; p && ancestors.length < 3; p = p.parentElement) ancestors.push(descriptor(p));
   const index = all.indexOf(e);
   const next = matches.find(m => all.indexOf(m) > index && !e.contains(m));
   // Inspect following visible blocks structurally, never return their text.
   const block = all.slice(index + 1).find(m => !e.contains(m));
   return {label: text(e), ...descriptor(e), ancestors,
     content_relationship_candidates: relations(e, block),
     next_fixed_label: next ? text(next) : null,
     next_fixed_label_relationship_candidates: relations(e, next)};
 });
 const directText = e => Array.from(e.childNodes).some(n => n.nodeType === 3 && !!(n.textContent || '').trim());
 const heading = e => /^h[1-6]$/.test(e.tagName.toLowerCase()) || e.getAttribute('role') === 'heading';
 const shape = e => ({tag: descriptor(e).tag, visible: visible(e),
   fixed_label: labels.includes(text(e)) ? text(e) : null, is_heading: heading(e),
   has_direct_nonempty_text: directText(e),
   has_descendant_nonempty_text: Array.from(e.children).some(c => !!text(c))});
 const responsibilities = matches.find(e => text(e) === '仕事内容');
 const sibling = responsibilities?.nextElementSibling;
 const first = sibling?.querySelectorAll('*')[0];
 return {
   visible_h1_count: all.filter(e => e.tagName.toLowerCase() === 'h1').length,
   has_og_title: !!document.querySelector('meta[property="og:title"]'),
   has_document_title: !!document.title,
   labels: records,
   ...(responsibilities ? {immediate_next_sibling: sibling ? shape(sibling) : null,
                           first_descendant: first ? shape(first) : null} : {})
 };
}"""


def sanitize(raw):
    """Rebuild fixed fields; arbitrary strings, keys and attributes cannot escape."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid structure')

    def enum(value, values):
        return value if isinstance(value, str) and value in values else None

    def items(value, limit):
        return value[:limit] if isinstance(value, list) else []

    def descriptor(item):
        return {key: enum(item.get(key), values) for key, values in
                [('tag', TAGS), ('role', ROLES), ('semantic_role', ROLES)]}

    def relations(value):
        return sorted({v for v in items(value, 100) if isinstance(v, str) and v in RELATIONS})

    records = []
    for item in items(raw.get('labels'), 100):
        if not isinstance(item, dict) or enum(item.get('label'), LABELS) is None:
            continue
        records.append(dict(label=item['label'], **descriptor(item),
            ancestors=[descriptor(a) for a in items(item.get('ancestors'), 3) if isinstance(a, dict)],
            content_relationship_candidates=relations(item.get('content_relationship_candidates')),
            next_fixed_label=enum(item.get('next_fixed_label'), LABELS),
            next_fixed_label_relationship_candidates=relations(item.get('next_fixed_label_relationship_candidates'))))
    count = raw.get('visible_h1_count')
    result = dict(visible_h1_count=count if type(count) is int and count >= 0 else 0,
                  has_og_title=raw.get('has_og_title') is True,
                  has_document_title=raw.get('has_document_title') is True, labels=records)
    if any(i['label'] == '仕事内容' for i in records):
        for key in ('immediate_next_sibling', 'first_descendant'):
            item = raw.get(key)
            result[key] = (dict(tag=enum(item.get('tag'), TAGS),
                **{flag: item.get(flag) is True for flag in
                   ('is_heading', 'visible', 'has_direct_nonempty_text', 'has_descendant_nonempty_text')},
                fixed_label=enum(item.get('fixed_label'), LABELS)) if isinstance(item, dict) else None)
    return result
