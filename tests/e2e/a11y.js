() => {
  const problems = [];
  const name = (el) => el.getAttribute('aria-label')
    || (el.labels && el.labels.length && [...el.labels].map((l) => l.textContent).join('').trim())
    || el.getAttribute('title') || '';
  const where = (el) => el.getAttribute('data-testid') || el.id || el.tagName.toLowerCase();
  for (const el of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    if (!name(el)) problems.push('control without a name: ' + where(el));
  }
  for (const el of document.querySelectorAll('button')) {
    if (!(el.textContent.trim() || el.getAttribute('aria-label'))) problems.push('button without a name: ' + where(el));
  }
  for (const el of document.querySelectorAll('a[href]')) {
    if (!(el.textContent.trim() || el.getAttribute('aria-label'))) problems.push('link without a name: ' + where(el));
  }
  for (const el of document.querySelectorAll('img')) {
    if (!el.hasAttribute('alt')) problems.push('image without alt: ' + (el.getAttribute('src') || ''));
  }
  for (const el of document.querySelectorAll('[role=dialog]')) {
    if (!el.getAttribute('aria-label')) problems.push('dialog without a label');
  }
  const ids = [...document.querySelectorAll('[id]')].map((e) => e.id);
  for (const id of new Set(ids.filter((v, i) => ids.indexOf(v) !== i))) problems.push('duplicate id: ' + id);
  if (!document.documentElement.lang) problems.push('missing lang');
  if (!document.title) problems.push('missing title');
  if (!document.querySelector('main')) problems.push('no main landmark');
  if (document.querySelectorAll('h1').length !== 1) problems.push('expected exactly one h1, found ' + document.querySelectorAll('h1').length);
  return problems;
}
