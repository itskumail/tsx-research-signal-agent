import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../paper_login.py', import.meta.url), 'utf8');
const script = source.match(/PROBE = r"""([\s\S]*?)"""/)[1];

function fixture() {
  const state = { clicks: [], text: '', hit: null, mode: 'live', dialogs: [], challenges: [] };
  const el = (tag, attrs = {}) => ({
    tagName: tag, hidden: false, disabled: false, innerText: '', textContent: '', target: '',
    box: { left: 10, top: 10, width: 100, height: 30 }, ...attrs,
    getAttribute(name) { return this.attrs?.[name] ?? null; },
    getClientRects() { return this.hidden ? [] : [this.box]; },
    getBoundingClientRect() { return this.box; },
    contains(other) { return this === other || (this.children || []).includes(other); },
  });
  const form = el('FORM', { method: 'post', attrs: { action: 'Dispatcher' } });
  const user = el('INPUT', { type: 'text', form, value: 'fixture-paper-user' });
  const password = el('INPUT', { type: 'password', form, value: 'fixture-secret-never-return' });
  const toggle = el('INPUT', { type: 'checkbox', form, checked: false, name: 'paperSwitch', attrs: { role: 'switch' } });
  const label = el('LABEL');
  const button = el('BUTTON', { innerText: 'Login', box: { left: 200, top: 200, width: 100, height: 30 } });
  const announcement = el('SPAN', { textContent: 'Live trading mode selected' });
  form.children = [user, password, toggle, label, button];
  form.querySelectorAll = selector => selector === 'button[type=submit]' ? [button] : [];
  label.click = () => { state.clicks.push('select_paper'); toggle.checked = true;
    button.innerText = 'Simulated Login'; announcement.textContent = 'Paper trading mode selected'; };
  button.click = () => { assert.equal(toggle.checked, true); state.clicks.push('submit_paper'); };
  const elements = {
    '#xyz-field-username': [user], '#xyz-field-password': [password], '#toggle1': [toggle],
    'label[for="toggle1"]': [label], 'form.xyzform-username': [form],
    '[aria-live],[role=status]': [announcement],
  };
  const location = { protocol: 'https:', hostname: 'ndcdyn.interactivebrokers.com',
    origin: 'https://ndcdyn.interactivebrokers.com', pathname: '/sso/Login',
    href: 'https://ndcdyn.interactivebrokers.com/sso/Login' };
  const window = {}; window.self = window; window.top = window;
  const context = { URL, location, window,
    getComputedStyle: () => ({ visibility: 'visible', display: 'block' }),
    document: {
      body: { get innerText() { return state.text; } },
      querySelectorAll: selector => selector.includes('one-time-code') ? state.challenges
        : selector.includes('aria-modal') ? state.dialogs : elements[selector] || [],
      elementFromPoint: (x, y) => state.hit || (x > 200 ? button : label),
    },
  };
  const run = action => JSON.parse(JSON.stringify(vm.runInNewContext(
    `(${script.replace('PERFORM_ACTION', JSON.stringify(action ?? null))})()`, context, { timeout: 1000 })));
  return { run, state, elements, location, window, form, user, password, toggle, label, button, announcement };
}

test('live inspection is read-only; only select-Paper then verified simulated submit can click', () => {
  const f = fixture();
  assert.equal(f.run().action, 'select_paper');
  assert.deepEqual(f.state.clicks, []);
  assert.equal(f.run('submit_paper').reason, 'paper_login_mode_changed');
  assert.equal(f.run('select_paper').attempted, true);
  assert.equal(f.run().action, 'submit_paper');
  assert.equal(f.run('submit_paper').attempted, true);
  assert.deepEqual(f.state.clicks, ['select_paper', 'submit_paper']);
});

test('probe returns no credential or raw page data', () => {
  const f = fixture();
  const output = JSON.stringify(f.run());
  assert.ok(!output.includes(f.user.value));
  assert.ok(!output.includes(f.password.value));
  assert.deepEqual(Object.keys(f.run()).sort(), ['action', 'attempted', 'eligible']);
});

test('observed empty-action same-page POST form is allowed without new destinations', () => {
  const f = fixture();
  f.form.attrs.action = '';
  assert.equal(f.run('select_paper').attempted, true);
  assert.equal(f.run('submit_paper').attempted, true);
  assert.deepEqual(f.state.clicks, ['select_paper', 'submit_paper']);
  const denied = fixture(); denied.form.attrs.action = ''; denied.form.method = 'get';
  assert.equal(denied.run('select_paper').reason, 'paper_login_form_destination_unverified');
});

for (const [label, mutate] of [
  ['foreign domain', f => { f.location.hostname = 'interactivebrokers.com.attacker.test'; }],
  ['HTTP', f => { f.location.protocol = 'http:'; }],
  ['portal instead of SSO', f => { f.location.pathname = '/portal/'; }],
  ['iframe', f => { f.window.top = {}; }],
  ['foreign form action', f => { f.form.attrs.action = 'https://attacker.test/'; }],
  ['unexpected same-origin destination', f => { f.form.attrs.action = '/portal/'; }],
  ['ARIA-disabled submit', f => { f.button.attrs = { 'aria-disabled': 'true' }; }],
  ['new-window form', f => { f.form.target = '_blank'; }],
  ['button destination override', f => { f.button.attrs = { formaction: 'other' }; }],
  ['missing saved password', f => { f.password.value = ''; }],
  ['missing saved username', f => { f.user.value = ''; }],
  ['disabled submit', f => { f.button.disabled = true; }],
  ['duplicated form', f => { f.elements['form.xyzform-username'] = [f.form, f.form]; }],
  ['unexpected mode state', f => { f.toggle.checked = true; }],
  ['authentication challenge', f => { f.state.text = 'Enter verification code'; }],
  ['competition prompt', f => { f.state.text = 'Existing session detected. Continue login?'; }],
  ['credential rejection', f => { f.form.innerText = 'Invalid username or password'; }],
  ['overlay', f => { f.state.hit = {}; }],
]) {
  test(`${label} prevents any click`, () => {
    const f = fixture(); mutate(f);
    const output = f.run('select_paper');
    assert.equal(output.eligible, false);
    assert.equal(output.attempted, false);
    assert.deepEqual(f.state.clicks, []);
  });
}

test('Paper checkbox alone is insufficient without both Paper announcement and simulated button', () => {
  for (const missing of ['announcement', 'button']) {
    const f = fixture();
    f.run('select_paper'); f.state.clicks = [];
    if (missing === 'announcement') f.announcement.textContent = 'Live trading mode selected';
    else f.button.innerText = 'Login';
    assert.equal(f.run('submit_paper').reason, 'paper_login_mode_unverified');
    assert.deepEqual(f.state.clicks, []);
  }
});

test('a visible active dialog blocks even an otherwise recognized login form', () => {
  const f = fixture();
  f.state.dialogs = [{ getClientRects: () => [{}], innerText: 'Please confirm' }];
  assert.equal(f.run('select_paper').reason, 'paper_login_dialog_requires_attention');
  assert.deepEqual(f.state.clicks, []);
});
