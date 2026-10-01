"""Allowlisted, content-free LAPRAS detail structure diagnostics."""

LABELS = ('仕事内容', '業務内容', '職務内容', '応募資格', '必須要件',
          '必須スキル', '歓迎要件', '歓迎スキル', '概要')
TAGS = frozenset('html body main article section div span h1 h2 h3 h4 h5 h6 p dt dd dl ul ol li table tbody tr th td label button a header footer nav aside'.split())
ROLES = frozenset({'main', 'article', 'section', 'region', 'tab', 'tabpanel', 'heading'})
RELATIONS = frozenset({'same-parent-next-sibling', 'same-parent-following-sibling', 'ancestor-next-sibling',
                       'nested-next-block', 'tab-panel'})

# Called only by platform_discovery, not by the production Search parser.
STRUCTURE = r"""() => {
 const labels = ['仕事内容','業務内容','職務内容','応募資格','必須要件','必須スキル','歓迎要件','歓迎スキル','概要'];
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
 const shape = e => ({...descriptor(e), visible: visible(e),
   fixed_label: labels.includes(text(e)) ? text(e) : null, is_heading: heading(e),
   has_direct_nonempty_text: directText(e),
   has_descendant_nonempty_text: Array.from(e.children).some(c => !!text(c))});
 const summary = matches.find(e => text(e) === '概要');
 const summaryShape = {};
 if (summary && matches.some(e => ['仕事内容','業務内容','職務内容'].includes(text(e)))) {
   const sibling = summary.nextElementSibling;
   const descendants = [];
   const walk = e => {
     for (const child of e.children) {
       if (descendants.length >= 12) return;
       if (visible(child) && (text(child) || heading(child) || labels.includes(text(child)))) descendants.push(shape(child));
       walk(child);
     }
   };
   if (sibling) walk(sibling);
   const subtree = sibling ? [sibling, ...Array.from(sibling.querySelectorAll('*'))] : [];
   const count = sibling ? sibling.children.length : 0;
   summaryShape.summary_node = descriptor(summary);
   summaryShape.summary_parent = summary.parentElement ? descriptor(summary.parentElement) : null;
   summaryShape.immediate_next_sibling = sibling ? {
     ...descriptor(sibling), visible: visible(sibling),
     child_count_bucket: count === 0 ? '0' : count === 1 ? '1' : count <= 5 ? '2-5' : '6+',
     has_direct_nonempty_text: directText(sibling),
     has_descendant_nonempty_text: Array.from(sibling.children).some(c => !!text(c)),
     contains_fixed_label: subtree.some(e => labels.includes(text(e))),
     contains_any_heading: subtree.some(heading)
   } : null;
   summaryShape.immediate_next_sibling_children = sibling ? Array.from(sibling.children).slice(0, 8).map(shape) : [];
   summaryShape.first_descendant_shapes = descendants;
 }
 const exact = matches.some(e => text(e) === '仕事内容');
 return {
   ...summaryShape,
   visible_h1_count: all.filter(e => e.tagName.toLowerCase() === 'h1').length,
   has_og_title: !!document.querySelector('meta[property="og:title"]'),
   has_document_title: !!document.title,
   labels: records,
   responsibilities_prefix_elements: exact ? [] : all.filter(e =>
     text(e).startsWith('仕事内容') && text(e) !== '仕事内容').slice(0, 100)
     .map(e => ({tag: descriptor(e).tag, starts_with_responsibilities: true}))
 };
}"""


def sanitize(raw):
    """Rebuild the schema; arbitrary keys, attributes and strings cannot escape."""
    if not isinstance(raw, dict):
        raise ValueError('Invalid structure')

    def descriptor(item):
        return {key: item.get(key) if item.get(key) in values else None
                for key, values in [('tag', TAGS), ('role', ROLES), ('semantic_role', ROLES)]}

    def relations(items):
        return sorted({s for s in items if isinstance(s, str) and s in RELATIONS})

    records = []
    for item in raw.get('labels', [])[:100]:
        if not isinstance(item, dict) or item.get('label') not in LABELS:
            continue
        records.append(dict(
            label=item['label'], **descriptor(item),
            ancestors=[descriptor(a) for a in item.get('ancestors', [])[:3] if isinstance(a, dict)],
            content_relationship_candidates=relations(item.get('content_relationship_candidates', [])),
            next_fixed_label=item.get('next_fixed_label') if item.get('next_fixed_label') in LABELS else None,
            next_fixed_label_relationship_candidates=relations(item.get('next_fixed_label_relationship_candidates', []))))
    count = raw.get('visible_h1_count')
    result = {
        'visible_h1_count': count if type(count) is int and count >= 0 else 0,
        'has_og_title': raw.get('has_og_title') is True,
        'has_document_title': raw.get('has_document_title') is True,
        'labels': records,
        'responsibilities_prefix_elements': [
            {'tag': i.get('tag') if i.get('tag') in TAGS else None,
             'starts_with_responsibilities': True}
            for i in raw.get('responsibilities_prefix_elements', [])[:100]
            if isinstance(i, dict) and i.get('starts_with_responsibilities') is True
        ] if not any(i['label'] == '仕事内容' for i in records) else [],
    }

    if (any(i['label'] in LABELS[:3] for i in records)
            and any(i['label'] == '概要' for i in records)
            and isinstance(raw.get('summary_node'), dict)):
        def flags(item, names):
            return {name: item.get(name) is True for name in names}

        def shape(item):
            return dict(descriptor(item), **flags(item, (
                'visible', 'is_heading', 'has_direct_nonempty_text',
                'has_descendant_nonempty_text')),
                fixed_label=item.get('fixed_label') if item.get('fixed_label') in LABELS else None)

        result['summary_node'] = descriptor(raw['summary_node'])
        parent = raw.get('summary_parent')
        result['summary_parent'] = descriptor(parent) if isinstance(parent, dict) else None
        sibling = raw.get('immediate_next_sibling')
        result['immediate_next_sibling'] = None
        for key in ('immediate_next_sibling_children', 'first_descendant_shapes'):
            result[key] = []
        if isinstance(sibling, dict):
            bucket = sibling.get('child_count_bucket')
            result['immediate_next_sibling'] = dict(descriptor(sibling),
                **flags(sibling, ('visible', 'has_direct_nonempty_text',
                                 'has_descendant_nonempty_text', 'contains_fixed_label',
                                 'contains_any_heading')),
                child_count_bucket=bucket if bucket in ('0', '1', '2-5', '6+') else None)
            for key, limit in [('immediate_next_sibling_children', 8), ('first_descendant_shapes', 12)]:
                items = raw.get(key, [])
                if isinstance(items, list):
                    result[key] = [shape(i) for i in items[:limit] if isinstance(i, dict)]
    return result
