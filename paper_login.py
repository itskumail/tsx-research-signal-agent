"""Owner-authorized submission of an already-filled IBKR Paper sign-in form.

No credential value leaves the page. No typing, password-manager operation,
challenge handling, Live submission, account setting or broker order is exposed.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


PROBE = r"""() => {
  const perform = PERFORM_ACTION;
  const deny = reason => ({eligible: false, attempted: false, reason});
  const visible = e => e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  if (location.protocol !== 'https:' ||
      !(location.hostname === 'interactivebrokers.com' || location.hostname.endsWith('.interactivebrokers.com')) ||
      !/^\/sso\/Login\/?$/i.test(location.pathname) || window.top !== window.self)
    return deny('paper_login_not_verified_page');
  const text = document.body.innerText;
  if (/verification code|two.factor authentication|verify you are human|captcha|approve.*notification|security code/i.test(text) ||
      [...document.querySelectorAll('input[autocomplete="one-time-code"],iframe[src*="captcha"]')].some(visible))
    return deny('paper_login_challenge_required');
  if (/existing session detected|continue login|would you like to[^\n]*disconnect/i.test(text))
    return deny('paper_login_competing_session');
  if ([...document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true]')]
      .some(e => visible(e) && e.innerText.trim()))
    return deny('paper_login_dialog_requires_attention');
  const forms = [...document.querySelectorAll('form.xyzform-username')].filter(visible);
  if (forms.length !== 1) return deny('paper_login_form_not_unique');
  const form = forms[0];
  const declaredAction = form.getAttribute('action');
  const actionUrl = new URL(declaredAction || location.href, location.href);
  const knownTarget = actionUrl.pathname === '/sso/Dispatcher' ||
    (!declaredAction && /^\/sso\/Login\/?$/i.test(actionUrl.pathname));
  if (actionUrl.origin !== location.origin || !knownTarget ||
      form.method.toLowerCase() !== 'post' || (form.target && form.target !== '_self'))
    return deny('paper_login_form_destination_unverified');
  if (/invalid.*(?:username|password|credentials)|incorrect.*(?:username|password)|login failed|account.*locked|too many.*attempts/i.test(form.innerText))
    return deny('paper_login_rejected');
  const unique = selector => {const es = [...document.querySelectorAll(selector)]; return es.length === 1 ? es[0] : null;};
  const user = unique('#xyz-field-username'), password = unique('#xyz-field-password');
  const toggle = unique('#toggle1'), label = unique('label[for="toggle1"]');
  if (!visible(user) || !visible(password) || user.form !== form || password.form !== form ||
      user.type !== 'text' || password.type !== 'password' || user.disabled || password.disabled ||
      !toggle || toggle.form !== form || toggle.type !== 'checkbox' || toggle.name !== 'paperSwitch' ||
      toggle.getAttribute('role') !== 'switch' || toggle.disabled ||
      toggle.getAttribute('aria-disabled') === 'true' || !form.contains(label))
    return deny('paper_login_form_unrecognized');
  // Only presence booleans are used here; no credential value is returned.
  if (!user.value.trim().length || !password.value.length)
    return deny('paper_login_saved_fields_missing');
  const buttons = [...form.querySelectorAll('button[type=submit]')].filter(visible);
  if (buttons.length !== 1 || buttons[0].disabled || buttons[0].getAttribute('aria-disabled') === 'true' ||
      buttons[0].getAttribute('formaction') ||
      buttons[0].getAttribute('formtarget')) return deny('paper_login_control_not_unique');
  const button = buttons[0];
  const announcements = [...document.querySelectorAll('[aria-live],[role=status]')].filter(visible)
    .map(e => e.textContent.trim());
  if (announcements.filter(t => /^(Paper|Live) trading mode selected$/.test(t)).length !== 1)
    return deny('paper_login_mode_unverified');
  const paper = toggle.checked === true && /^Simulated Login$/i.test(button.innerText.trim()) &&
    announcements.includes('Paper trading mode selected');
  const live = toggle.checked === false && /^Login$/i.test(button.innerText.trim()) &&
    announcements.includes('Live trading mode selected');
  if (!paper && !live) return deny('paper_login_mode_unverified');
  const action = paper ? 'submit_paper' : 'select_paper';
  if (!perform) return {eligible: true, attempted: false, action};
  if (perform !== action) return deny('paper_login_mode_changed');
  const control = paper ? button : label;
  const box = control.getBoundingClientRect();
  if (!visible(control) || box.width <= 0 || box.height <= 0)
    return deny('paper_login_control_not_visible');
  const x = box.left + box.width / 2, y = box.top + box.height / 2;
  const hit = document.elementFromPoint(x, y);
  if (!hit || !(control === hit || control.contains(hit)))
    return deny('paper_login_control_covered');
  // Guard and action are synchronous in one evaluation. A submit can only
  // reach this line with all three independently observed Paper indicators.
  control.click();
  return {eligible: true, attempted: true, action};
}"""

REASONS = {
    'paper_login_not_verified_page', 'paper_login_challenge_required', 'paper_login_competing_session',
    'paper_login_dialog_requires_attention', 'paper_login_form_not_unique',
    'paper_login_form_destination_unverified', 'paper_login_rejected', 'paper_login_form_unrecognized',
    'paper_login_saved_fields_missing', 'paper_login_control_not_unique', 'paper_login_mode_unverified',
    'paper_login_mode_changed', 'paper_login_control_not_visible', 'paper_login_control_covered',
}


def probe(browser, action: str | None = None) -> dict:
    from browser_research import BrowserCommandError
    if action not in (None, 'select_paper', 'submit_paper'):
        raise ValueError('Unsupported Paper login action')
    args = ('evaluate', '--target-id', browser.keeper, '--fn',
            PROBE.replace('PERFORM_ACTION', json.dumps(action), 1))
    # An evaluation that clicks must never use the observation retry wrapper.
    payload = (browser._command_once(*args, timeout=browser.COMMAND_TIMEOUT) if action
               else browser._command(*args))
    result = payload.get('result')
    if not isinstance(result, dict):
        raise BrowserCommandError('malformed_response', 'paper_saved_login')
    if result.get('eligible') is not True:
        return {'eligible': False, 'attempted': False,
                'reason': result.get('reason') if result.get('reason') in REASONS else 'paper_login_probe_failed'}
    expected = action if action else result.get('action')
    if expected not in ('select_paper', 'submit_paper') or result.get('action') != expected:
        raise BrowserCommandError('malformed_response', 'paper_saved_login')
    return {'eligible': True, 'attempted': result.get('attempted') is True, 'action': expected}


def _write_state(path: Path, blocked: bool):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'blocked': blocked,
                         'checked_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
    temporary.replace(path)


def clear_verified(path: Path):
    """Only call after observing the authenticated Paper banner, not a form."""
    if not path.exists():
        return
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved, dict) or saved.get('blocked') is not False:
            _write_state(path, False)
    except (OSError, ValueError):
        # A healthy observation can repair malformed state, but an I/O failure
        # must never silently authorize another submission.
        try:
            _write_state(path, False)
        except OSError:
            pass


def attempt(browser, path: Path) -> dict:
    """Caller holds the browser lock and has already classified an SSO page."""
    from browser_research import failure_details
    state = {'status': 'login_required', 'failed_step': 'paper_saved_login'}
    if os.getenv('OPENCLAW_PAPER_SAVED_LOGIN', 'false').lower() != 'true':
        return {**state, 'reason': 'paper_login_disabled'}
    old_deadline = browser.deadline
    browser.deadline = min(old_deadline or float('inf'), time.monotonic() + 100)
    attempted = False
    try:
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return {**state, 'reason': 'paper_login_state_unavailable'}
            if not isinstance(saved, dict) or type(saved.get('blocked')) is not bool:
                return {**state, 'reason': 'paper_login_state_unavailable'}
            if saved['blocked']:
                return {**state, 'reason': 'paper_login_attempt_exhausted'}
        observed = probe(browser)
        if not observed['eligible']:
            return {**state, 'reason': observed['reason']}
        # Durable block BEFORE either UI mutation. Preserve it on every
        # rejection, challenge, timeout or uncertain result until healthy Paper.
        _write_state(path, True)
        attempted = True
        if observed['action'] == 'select_paper':
            changed = probe(browser, 'select_paper')
            if not changed.get('attempted'):
                return {**state, 'reason': changed.get('reason', 'paper_login_mode_unverified'),
                        'saved_login_attempted': True}
            time.sleep(1)
        submitted = probe(browser, 'submit_paper')
        if not submitted.get('attempted'):
            return {**state, 'reason': submitted.get('reason', 'paper_login_mode_unverified'),
                    'saved_login_attempted': True}
        for _ in range(4):
            time.sleep(2)
            session = browser.paper_session()
            if session.get('status') in ('ready', 'paper_disconnected'):
                _write_state(path, False)
                return {**session, 'saved_login_attempted': True, 'saved_login_succeeded': True}
            if session.get('status') == 'login_required':
                observed = probe(browser)
                if not observed['eligible']:
                    return {**state, 'reason': observed['reason'], 'saved_login_attempted': True}
            elif session.get('status') != 'paper_not_verified':
                return {**state, 'reason': 'paper_login_not_restored', 'saved_login_attempted': True}
        return {**state, 'reason': 'paper_login_not_restored', 'saved_login_attempted': True}
    except Exception as error:
        return {**state, **failure_details(error, 'paper_saved_login'),
                **({'saved_login_attempted': True} if attempted else {})}
    finally:
        browser.deadline = old_deadline
