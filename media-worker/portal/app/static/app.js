// Small helpers: live countdowns, "copy URL" buttons, auto-refresh every 2 minutes while the tab is visible.
(() => {
  const fmt = s => {
    const a = Math.abs(s);
    const t = a < 3600 ? `${Math.floor(a / 60)} min` : a < 86400 ? `${Math.floor(a / 3600)}h ${String(Math.floor(a % 3600 / 60)).padStart(2, '0')}m`
      : `${Math.floor(a / 86400)} g ${Math.floor(a % 86400 / 3600)}h`;
    return s >= 0 ? `tra ${t}` : `${t} fa`;
  };
  const tick = () => document.querySelectorAll('[data-countdown]').forEach(el => {
    el.textContent = fmt((Date.parse(el.dataset.countdown) - Date.now()) / 1000);
  });
  const loaded = Date.now();
  const upd = () => document.querySelectorAll('[data-updated]').forEach(el => {
    const m = Math.floor((Date.now() - loaded) / 60000);
    el.textContent = m < 1 ? 'aggiornato ora' : `aggiornato ${m} min fa`;
  });
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-copy]');
    if (!b) return;
    navigator.clipboard?.writeText(b.dataset.copy).then(() => { b.textContent = 'copiato ✓'; setTimeout(() => b.textContent = 'copia URL video', 1500); });
  });
  document.addEventListener('submit', e => {
    const f = e.target.closest('form[data-confirm]');
    if (f && !confirm(f.dataset.confirm)) e.preventDefault();
  });
  let dirty = false;  // no auto-refresh while a settings form has unsaved edits
  document.addEventListener('input', e => { if (e.target.closest('.settings')) dirty = true; });
  setInterval(() => { tick(); upd(); }, 15000);
  setInterval(() => { if (document.visibilityState === 'visible' && !dirty && !['/login', '/impostazioni'].includes(location.pathname)) location.reload(); }, 120000);
  tick();
})();
