/**
 * 03_login_email.js — handles the arena.ai login dialog (the email magic-link path)
 *
 * Trigger sequence (verified): open /agent → Terms dialog (click Agree) → click send → "Log In or Create Account"
 * → enter email → Continue with email → open the login link in the email (or enter the verification code).
 *
 * All three steps live in one file, controlled by STEP:
 *   STEP = 'terms'  only clicks Agree
 *   STEP = 'trigger' sends a probe message to force out the login dialog (clicks Agree automatically first)
 *   STEP = 'email'  enters the email and submits
 */
(() => {
  const STEP = 'terms';                 // 'terms' | 'trigger' | 'email'
  const EMAIL = '__EMAIL__';            // ← the inbox that will receive the email
  const PROBE = 'hi';                   // probe message content
  const R = (o) => JSON.stringify(o);

  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const findByText = (re, root = document) =>
    [...root.querySelectorAll('button, a, [role="button"]')].filter(visible).find(b => re.test(b.innerText || ''));

  const dialog = () => [...document.querySelectorAll('[role="dialog"]')].filter(visible)[0] || null;

  // ---------- Terms ----------
  if (STEP === 'terms' || STEP === 'trigger') {
    const d = dialog();
    if (d && /Terms of Use/i.test(d.innerText)) {
      const agree = findByText(/^(agree|i agree|同意)$/i, d);
      if (agree) { agree.click(); return R({ ok: true, step: 'terms', clicked: 'Agree' }); }
      return R({ ok: false, step: 'terms', error: 'AGREE_BUTTON_NOT_FOUND' });
    }
    if (STEP === 'terms') return R({ ok: true, step: 'terms', note: 'no terms dialog' });
  }

  // ---------- Trigger the login dialog ----------
  if (STEP === 'trigger') {
    const d = dialog();
    if (d && /Log In or Create Account/i.test(d.innerText)) return R({ ok: true, step: 'trigger', already: true });
    const editor = document.querySelector('.ProseMirror[contenteditable="true"]');
    if (!editor) return R({ ok: false, error: 'EDITOR_NOT_FOUND' });
    editor.focus();
    const dt = new DataTransfer(); dt.setData('text/plain', PROBE);
    editor.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    const send = document.querySelector('button[aria-label="Send message"]');
    if (!send) return R({ ok: false, error: 'SEND_BUTTON_NOT_FOUND' });
    send.click();
    return R({ ok: true, step: 'trigger', note: 'After the dialog appears, call this script once more to confirm' });
  }

  // ---------- Submit the email ----------
  if (STEP === 'email') {
    const d = dialog();
    if (!d) return R({ ok: false, error: 'LOGIN_DIALOG_NOT_FOUND' });
    const input = d.querySelector('input[type="email"]') || [...d.querySelectorAll('input')].find(visible);
    if (!input) return R({ ok: false, error: 'EMAIL_INPUT_NOT_FOUND' });
    const setV = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setV.call(input, EMAIL);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const btn = findByText(/continue with email|continue|next|send code/i, d);
    if (btn) btn.click();
    const txt = d.innerText;
    return R({
      ok: true, step: 'email',
      note: 'Check your inbox for the login link. If a verification-code input appears on the page, fill it with snippets/03b_fill_code.js.',
      warn: /not permitted|invalid/i.test(txt) ? 'EMAIL_REJECTED' : null,
    });
  }

  return R({ ok: false, error: 'UNKNOWN_STEP' });
})()
