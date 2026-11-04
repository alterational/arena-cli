/**
 * 03b_fill_code.js — fills in the verification code / one-time password when it appears in the login dialog
 * Supports two common implementations: a single input (maxlength=6) and six separate small boxes.
 */
(() => {
  const CODE = '__CODE__';   // ← the verification code taken from the email
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

  if (boxes.length >= CODE.length) {         // Separate-box input
    [...CODE].forEach((ch, i) => { if (boxes[i]) { boxes[i].focus(); fire(boxes[i], ch); } });
  } else {                                    // Single input box
    boxes[0].focus();
    fire(boxes[0], CODE);
    boxes[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  }

  // Some implementations also require clicking a confirm button
  const submit = [...root.querySelectorAll('button')]
    .filter(visible)
    .find(b => /verify|continue|submit|sign in|confirm/i.test(b.innerText || ''));
  if (submit) submit.click();

  return R({ ok: true, filled: CODE.length });
})()
