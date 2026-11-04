/**
 * 04_dump_session.js — fallback "remember login" approach (pure JS version)
 *
 * ⚠️ Important: arena.ai's login cookie is httpOnly, so document.cookie cannot read it.
 *    So for "remembering cookies", prefer the Python script (Playwright storage_state can capture httpOnly cookies):
 *        python scripts/arena_agent.py login       # saves ~/.arena-cli/state.json
 *        python scripts/arena_agent.py export-cookies --out cookies.json
 *    This script only exports what JS can see (localStorage + non-httpOnly cookies),
 *    suitable for restricted environments where browser_exec can only run JS and cannot access context.cookies().
 */
(() => {
  const cookies = document.cookie
    ? document.cookie.split(';').map(p => {
        const i = p.indexOf('=');
        return { name: p.slice(0, i).trim(), value: p.slice(i + 1).trim(), domain: location.hostname, path: '/' };
      })
    : [];

  const localStorageDump = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    localStorageDump[k] = localStorage.getItem(k);
  }

  return JSON.stringify({
    ok: true,
    origin: location.origin,
    note: 'httpOnly cookies are not included here; for the full session use the Python export-cookies command',
    cookies,
    localStorage: localStorageDump,
  }, null, 2);
})()
