/**
 * 03_login_email.js —— 处理 arena.ai 的登录弹窗（邮箱 magic-link 那条路）
 *
 * 触发顺序（实测）：打开 /agent → Terms 弹窗（点 Agree）→ 点发送 → "Log In or Create Account"
 * → 填邮箱 → Continue with email → 去邮箱点登录链接（或填验证码）。
 *
 * 三步都写在一个文件里，用 STEP 控制：
 *   STEP = 'terms'  只点 Agree
 *   STEP = 'trigger' 发一条试探消息，把登录弹窗逼出来（会先自动点 Agree）
 *   STEP = 'email'  填邮箱并提交
 */
(() => {
  const STEP = 'terms';                 // 'terms' | 'trigger' | 'email'
  const EMAIL = '__EMAIL__';            // ← 收信邮箱
  const PROBE = 'hi';                   // 试探消息内容
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

  // ---------- 触发登录弹窗 ----------
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
    return R({ ok: true, step: 'trigger', note: '待弹窗出现后再调用一次本脚本确认' });
  }

  // ---------- 提交邮箱 ----------
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
      note: '去邮箱点登录链接。若页面出现验证码输入框，用 snippets/03b_fill_code.js 填。',
      warn: /not permitted|invalid/i.test(txt) ? 'EMAIL_REJECTED' : null,
    });
  }

  return R({ ok: false, error: 'UNKNOWN_STEP' });
})()
