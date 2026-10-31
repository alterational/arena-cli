/**
 * 01_fill_and_send.js —— 把内容写进 arena.ai/agent 的输入框并提交
 * 用法（Hermes browser_exec 执行 JS）：把 PROMPT 换成用户内容后整体执行；
 * 返回 JSON 字符串，形如 {"ok":true,"editor":"...","sent":true}
 *
 * 为什么不用 input.value：arena 的输入框是 Tiptap/ProseMirror（contenteditable），
 * 直接改 innerText 不会被 React 状态接管，发送按钮仍是禁用的。必须派发 paste/input 事件。
 */
(() => {
  const PROMPT = `__PROMPT__`;           // ← 替换成用户输入（注意转义反引号与 ${}）

  const editor =
    document.querySelector('div.tiptap.ProseMirror[contenteditable="true"]') ||
    document.querySelector('.ProseMirror[contenteditable="true"]') ||
    document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!editor) return JSON.stringify({ ok: false, error: 'EDITOR_NOT_FOUND' });

  editor.focus();
  // 清空
  const sel = window.getSelection();
  sel.selectAllChildren(editor);
  sel.deleteFromDocument();

  // 用 paste 事件写入：ProseMirror 会把它解析成段落，多行也安全
  const dt = new DataTransfer();
  dt.setData('text/plain', PROMPT);
  editor.dispatchEvent(new ClipboardEvent('paste', {
    clipboardData: dt, bubbles: true, cancelable: true
  }));

  // 兜底：paste 没生效就用 insertText
  if (!editor.innerText.trim()) {
    document.execCommand('insertText', false, PROMPT);
  }

  const stop = document.querySelector('button[aria-label*="Stop" i]');
  if (stop) return JSON.stringify({ ok: false, error: 'BUSY_STREAMING' });

  const send =
    document.querySelector('button[aria-label="Send message"]') ||
    document.querySelector('button[aria-label*="Send" i]');
  if (!send) return JSON.stringify({ ok: false, error: 'SEND_BUTTON_NOT_FOUND' });
  if (send.disabled || send.getAttribute('aria-disabled') === 'true')
    return JSON.stringify({ ok: false, error: 'SEND_DISABLED' });

  const loginWall = /Continue with (Google|email)/.test(document.body.innerText);
  if (loginWall) return JSON.stringify({ ok: false, error: 'LOGIN_REQUIRED' });

  send.click();
  return JSON.stringify({ ok: true, textLength: editor.innerText.length, sent: true });
})()
