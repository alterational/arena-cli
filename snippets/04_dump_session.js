/**
 * 04_dump_session.js —— 兜底的"记住登录"方案（纯 JS 版）
 *
 * ⚠️ 重要：arena.ai 的登录 cookie 是 httpOnly 的，document.cookie 读不到。
 *    所以"记住 cookies"请优先用 Python 脚本（Playwright 的 storage_state 能拿到 httpOnly）：
 *        python scripts/arena_agent.py login       # 保存 ~/.arena-agent/state.json
 *        python scripts/arena_agent.py export-cookies --out cookies.json
 *    本脚本只导出 JS 能看见的那部分（localStorage + 非 httpOnly cookie），
 *    适合 browser_exec 只能执行 JS、无法访问 context.cookies() 的受限环境。
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
    note: 'httpOnly cookies 不在此列；如需完整会话请用 Python 版 export-cookies',
    cookies,
    localStorage: localStorageDump,
  }, null, 2);
})()
