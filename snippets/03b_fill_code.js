/**
 * 03b_fill_code.js —— 登录弹窗里出现验证码/一次性密码时填进去
 * 支持：单个 input（maxlength=6）和 6 个独立小格子的两种常见实现。
 */
(() => {
  const CODE = '__CODE__';   // ← 从邮件里取到的验证码
  const R = o => JSON.stringify(o);

  const dialog = [...document.querySelectorAll('[role="dialog"]')].filter(d => d.offsetParent !== null)[0];
  const root = dialog || document;
  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight);

  const boxes = [...root.querySelectorAll('input')].filter(
    i => visible(i) && (i.maxLength === 1 || i.getAttribute('inputmode') === 'numeric' || /code|otp/i.test(i.name + i.id + (i.placeholder || '')))
  );
  if (!boxes.length) return R({ ok: false, error: 'CODE_INPUT_NOT_FOUND' });

  const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  const fire = (el, v) => {
    nativeSet.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };

  if (boxes.length >= CODE.length) {         // 分格输入
    [...CODE].forEach((ch, i) => { if (boxes[i]) { boxes[i].focus(); fire(boxes[i], ch); } });
  } else {                                    // 单输入框
    boxes[0].focus();
    fire(boxes[0], CODE);
    boxes[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  }

  // 有些实现还要点一下确认按钮
  const submit = [...root.querySelectorAll('button')]
    .filter(visible)
    .find(b => /verify|continue|submit|sign in|confirm/i.test(b.innerText || ''));
  if (submit) submit.click();

  return R({ ok: true, filled: CODE.length });
})()
