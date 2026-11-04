/**
 * 02_wait_and_read.js — reads the Agent's answer (for polling)
 * Each call returns a JSON string:
 *   {"streaming":false,"answer":"...","stableFor":3.2,"done":true}
 * Logic for deciding "complete" (the Hermes side calls this script in a loop):
 *   If 2–3 consecutive calls all have streaming=false and an unchanged answer → treat as finished.
 */
(() => {
  const bodyText = document.body.innerText || '';

  // 1) Still streaming output?
  const streaming =
    !!document.querySelector('button[aria-label*="Stop" i]') ||
    !!document.querySelector('[data-streaming="true"], [data-state="streaming"]');

  // 2) Structured extraction: the last assistant message
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

  // 3) Fallback: take the text after the last user message (diff method)
  const PROMPT_KEY = `__PROMPT_HEAD__`;   // Beginning of the user message (about the first 40 characters), used to locate it
  if (!answer && PROMPT_KEY) {
    const i = bodyText.lastIndexOf(PROMPT_KEY);
    if (i !== -1) answer = bodyText.slice(i + PROMPT_KEY.length);
  }

  // 4) Strip common button / UI noise lines
  const noise = /^(Copy|Copied|Retry|Regenerate|Edit|Share|Like|Dislike|Sources|Show more|Show less|Stop)$/i;
  answer = answer.split('\n').map(s => s.trim()).filter(s => s && !noise.test(s)).join('\n').trim();

  const errs = ['Something went wrong', 'An error occurred', 'rate limit', 'Too many requests'];
  const error = errs.find(e => bodyText.toLowerCase().includes(e.toLowerCase())) || null;

  // 5) Use a text fingerprint to mark whether the content is "stable", for the caller to decide
  const fp = answer.length + ':' + answer.slice(-24);
  const prev = window.__arenaLastFp;
  const stable = prev === fp;
  window.__arenaLastFp = fp;

  return JSON.stringify({
    streaming,
    answer,
    error,
    stable,            // true = unchanged compared with the previous call
    done: !!answer && !streaming && stable,
    url: location.href,
  });
})()
