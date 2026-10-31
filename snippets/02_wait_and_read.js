/**
 * 02_wait_and_read.js —— 读取 Agent 的回答（轮询用）
 * 每次调用返回 JSON 字符串：
 *   {"streaming":false,"answer":"...","stableFor":3.2,"done":true}
 * 判定"完成"的逻辑（在 Hermes 侧循环调用本脚本）：
 *   连续 2~3 次调用都满足 streaming=false 且 answer 无变化 → 视为执行完成。
 */
(() => {
  const bodyText = document.body.innerText || '';

  // 1) 还在流式输出？
  const streaming =
    !!document.querySelector('button[aria-label*="Stop" i]') ||
    !!document.querySelector('[data-streaming="true"], [data-state="streaming"]');

  // 2) 结构化抽取：最后一条 assistant 消息
  const pick = [
    '[data-message-author-role="assistant"]',
    '[data-role="assistant"]',
    '[data-testid*="assistant"]',
    '[class*="assistant"] .prose',
    '.message-assistant',
    '[class*="message"] .prose',
    'article .prose',
  ];
  let answer = '';
  for (const sel of pick) {
    const nodes = [...document.querySelectorAll(sel)].filter(n => n.offsetParent !== null);
    if (nodes.length) { answer = nodes[nodes.length - 1].innerText; break; }
  }

  // 3) 兜底：从最后一条 user 消息之后截取（差分法）
  const PROMPT_KEY = `__PROMPT_HEAD__`;   // 用户消息的开头（前 40 字左右），用于定位
  if (!answer && PROMPT_KEY) {
    const i = bodyText.lastIndexOf(PROMPT_KEY);
    if (i !== -1) answer = bodyText.slice(i + PROMPT_KEY.length);
  }

  // 4) 去掉常见的按钮/UI 噪音行
  const noise = /^(Copy|Copied|Retry|Regenerate|Edit|Share|Like|Dislike|Sources|Show more|Show less|Stop)$/i;
  answer = answer.split('\n').map(s => s.trim()).filter(s => s && !noise.test(s)).join('\n').trim();

  const errs = ['Something went wrong', 'An error occurred', 'rate limit', 'Too many requests'];
  const error = errs.find(e => bodyText.toLowerCase().includes(e.toLowerCase())) || null;

  // 5) 用文本指纹做"是否稳定"标记，供调用方判断
  const fp = answer.length + ':' + answer.slice(-24);
  const prev = window.__arenaLastFp;
  const stable = prev === fp;
  window.__arenaLastFp = fp;

  return JSON.stringify({
    streaming,
    answer,
    error,
    stable,            // true = 和上一次调用相比没有变化
    done: !!answer && !streaming && stable,
    url: location.href,
  });
})()
