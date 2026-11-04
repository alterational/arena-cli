/**
 * 01_fill_and_send.js — writes content into the arena.ai/agent input box and submits it
 * Usage (Hermes browser_exec runs JS): replace PROMPT with the user's content, then run the whole script;
 * Returns a JSON string such as {"ok":true,"editor":"...","sent":true}
 *
 * Why not input.value: arena's input box is Tiptap/ProseMirror (contenteditable);
 * setting innerText directly is not picked up by React state, so the send button stays disabled. You must dispatch paste/input events.
 */
(() => {
  const PROMPT = `__PROMPT__`;           // ← replace with the user's input (watch out for escaping backticks and ${})

  const editor =
    document.querySelector('div.tiptap.ProseMirror[contenteditable="true"]') ||
    document.querySelector('.ProseMirror[contenteditable="true"]') ||
    document.querySelector('div[contenteditable="true"][role="textbox"]');
  if (!editor) return JSON.stringify({ ok: false, error: 'EDITOR_NOT_FOUND' });

  editor.focus();
  // Clear
  const sel = window.getSelection();
  sel.selectAllChildren(editor);
  sel.deleteFromDocument();

  // Write via a paste event: ProseMirror parses it into paragraphs, so multi-line text is safe too
  const dt = new DataTransfer();
  dt.setData('text/plain', PROMPT);
  editor.dispatchEvent(new ClipboardEvent('paste', {
    clipboardData: dt, bubbles: true, cancelable: true
  }));

  // Fallback: if paste did not take effect, use insertText
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
