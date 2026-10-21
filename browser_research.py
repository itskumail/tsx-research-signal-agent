"""Public research and guarded Paper-session activity in dedicated Chrome."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import time
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent


@contextmanager
def browser_lock(path: Path | None = None, *, wait_seconds: float = 0, poll_seconds: float = 5):
    """An OS lock releases even if a worker crashes. Callers may wait a bounded
    time for the other worker (research or the 40-minute check) to finish."""
    path = path or ROOT / "logs" / "browser.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        give_up = time.monotonic() + max(0, wait_seconds)
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() + poll_seconds > give_up:
                    break
                time.sleep(poll_seconds)
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def is_ibkr_tab(tab: dict) -> bool:
    host = (urlparse(tab.get("url", "")).hostname or "").lower()
    return host == "interactivebrokers.com" or host.endswith(".interactivebrokers.com")


class BrowserCommandError(RuntimeError):
    """Carry only safe diagnostic codes, never broker output or command arguments."""

    def __init__(self, reason: str, step: str):
        self.reason = reason
        self.step = step
        super().__init__(f"{step}: {reason}")


def failure_details(error: Exception, step: str) -> dict:
    if isinstance(error, BrowserCommandError):
        return {"reason": error.reason, "failed_step": error.step}
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        reason = "command_timeout"
    elif isinstance(error, FileNotFoundError):
        reason = "ssh_client_missing"
    else:
        reason = "unexpected_error"
    return {"reason": reason, "failed_step": step}


class CanadianResearchBrowser:
    PROFILE = "canadian-stocks"
    COMMAND_TIMEOUT = 35
    CLEANUP_TIMEOUT = 90
    RECONNECT_PROBE = r"""() => {
        const perform = PERFORM_CLICK;
        const visible = e => e.getClientRects().length > 0 &&
            getComputedStyle(e).visibility !== 'hidden';
        const text = document.body.innerText;
        const deny = reason => ({eligible: false, attempted: false, reason});
        if (!(location.hostname === 'interactivebrokers.com' ||
              location.hostname.endsWith('.interactivebrokers.com')) ||
            !/^\/portal(?:\/|$)/.test(location.pathname))
            return deny('reconnect_not_portal');
        if (!/paper trading account|simulated trading/i.test(text))
            return deny('reconnect_paper_not_verified');
        if (document.querySelector('input[type=password]') ||
            /verification code|two.factor authentication|verify you are human|captcha/i.test(text))
            return deny('reconnect_authentication_required');
        if (Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true]'))
            .some(e => visible(e) && e.innerText.trim().length > 0))
            return deny('reconnect_dialog_requires_attention');
        if (/existing session detected|continue login|would you like to[^\n]*disconnect/i.test(text))
            return deny('reconnect_competing_session');
        if (!/not connected to the brokerage system/i.test(text) ||
            !/without Trading\/Market Data/i.test(text))
            return deny('reconnect_warning_missing');
        const controls = Array.from(document.querySelectorAll('button,a,[role=button]'))
            .filter(e => visible(e) && /^Log In$/i.test(e.innerText.trim()));
        const candidates = controls.filter(e => {
            if (e.disabled || e.getAttribute('aria-disabled') === 'true') return false;
            let parent = e.parentElement;
            for (let i = 0; parent && parent !== document.body && i < 6;
                 i++, parent = parent.parentElement) {
                const warning = parent.innerText;
                if (warning.length < 2000 &&
                    /not connected to the brokerage system/i.test(warning) &&
                    /without Trading\/Market Data/i.test(warning)) return true;
            }
            return false;
        });
        if (controls.length !== 1 || candidates.length !== 1)
            return deny('reconnect_control_not_unique');
        if (!perform) return {eligible: true, attempted: false};
        candidates[0].click();
        return {eligible: true, attempted: true};
    }"""

    def __init__(self):
        self.target = os.getenv("OPENCLAW_SSH_TARGET", "trader@192.0.2.16")
        self.cli = os.getenv("OPENCLAW_CLI_PATH",
                             "/home/trader/.nvm/versions/node/v24.21.0/bin/openclaw")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.:-]*", self.target):
            raise ValueError("Invalid OpenClaw SSH target")
        if not self.cli.startswith("/"):
            raise ValueError("OpenClaw CLI path must be absolute")
        self.last_cleanup = None
        self.keeper = None
        self.deadline = None
        self.last_recovery = None
        self.session_extended = False
        self.returned_to_dashboard = False

    def _command(self, *args: str, timeout: float | None = None) -> dict:
        # Retry only observations. Never repeat a click/navigation after an
        # uncertain result, since the original action may already have succeeded.
        limit = time.monotonic() + (timeout or self.COMMAND_TIMEOUT)
        attempts = 2 if args[0] in ("status", "tabs", "evaluate", "snapshot") else 1
        for attempt in range(attempts):
            try:
                return self._command_once(*args, timeout=limit - time.monotonic())
            except BrowserCommandError as error:
                transient = error.reason in ("ssh_connection_failed", "command_timeout", "control_unavailable")
                if not transient or attempt + 1 == attempts or time.monotonic() >= limit:
                    raise
                time.sleep(1)

    def _command_once(self, *args: str, timeout: float) -> dict:
        step = args[0]
        remaining = self.deadline - time.monotonic() if self.deadline else self.COMMAND_TIMEOUT
        if remaining <= 0 or timeout <= 0:
            raise BrowserCommandError("command_timeout", step)
        bin_dir = str(PurePosixPath(self.cli).parent)
        remote = shlex.join([
            "env", f"PATH={bin_dir}:/usr/local/bin:/usr/bin:/bin", self.cli,
            "browser", "--browser-profile", self.PROFILE,
            "--timeout", "20000", *args, "--json",
        ])
        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                 "-o", "ConnectTimeout=3", self.target, remote],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=min(self.COMMAND_TIMEOUT, timeout, remaining),
            )
        except FileNotFoundError:
            raise BrowserCommandError("ssh_client_missing", step) from None
        except subprocess.TimeoutExpired:
            raise BrowserCommandError("command_timeout", step) from None
        # Do not log raw stdout/stderr: broker tab metadata may be private.
        if result.returncode:
            if result.returncode == 255:
                reason = "ssh_connection_failed"
            elif result.returncode == 127:
                reason = "openclaw_cli_missing"
            elif re.search(r"timeout|timed out", result.stderr or "", re.I):
                reason = "command_timeout"
            else:
                reason = "control_unavailable"
            raise BrowserCommandError(reason, step)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            raise BrowserCommandError("malformed_response", step) from None
        if not isinstance(payload, dict) or payload.get("ok") is False or payload.get("error"):
            raise BrowserCommandError("malformed_response", step)
        for field in ("profile", "resolvedProfile"):
            if payload.get(field) and payload[field] != self.PROFILE:
                raise BrowserCommandError("profile_mismatch", step)
        return payload

    def check(self) -> dict:
        try:
            status = self._command("status")
            ready = (status.get("profile") == self.PROFILE and
                     all(status.get(key) is True for key in ("running", "cdpReady", "pageReady")))
            result = {"profile": self.PROFILE, "available": ready,
                      "status": "ready" if ready else "unavailable"}
            if not ready:
                result.update(reason="cdp_not_ready" if status.get("profile") == self.PROFILE
                              else "profile_mismatch", failed_step="status")
            return result
        except Exception as error:
            return {"profile": self.PROFILE, "available": False, "status": "unavailable",
                    **failure_details(error, "status")}

    def ensure_available(self) -> dict:
        """Research/keepalive callers hold the shared lock; plain checks stay passive."""
        health = self.check()
        if health["available"] or health.get("reason") == "profile_mismatch":
            return health
        from browser_launcher import ensure_local_browser
        self.last_recovery = ensure_local_browser()
        recovery = self.last_recovery
        if recovery.get("launched") or (recovery["status"] == "ready" and health.get("reason") == "cdp_not_ready"):
            health = self.check()
            if not health["available"] and recovery["status"] in ("ready", "started") and health.get("reason") == "cdp_not_ready":
                try:
                    tabs = self._command("tabs").get("tabs")
                    if tabs == []:
                        self._command("open", "https://ndcdyn.interactivebrokers.com/portal/")
                        health = self.check()
                except Exception:
                    pass
        if not health["available"] and recovery["status"] in ("ready", "started") and health.get("reason") == "cdp_not_ready":
            health.update(reason="attach_not_ready")
        if recovery.get("launched"):
            health["browser_restarted"] = True
        if recovery.get("reason"):
            health["recovery_reason"] = recovery["reason"]
        return health

    @staticmethod
    def _tab_id(tab: dict) -> str:
        target = tab.get("targetId") or tab.get("tabId")
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", target):
            raise ValueError("Missing or invalid browser tab identifier")
        return target

    def _broker_keeper(self, brokers: list[dict]) -> dict:
        portals = [tab for tab in brokers if urlparse(tab.get('url', '')).path.lower().startswith('/portal')]
        choices = portals or brokers
        return next((tab for tab in choices if self._tab_id(tab) == self.keeper), choices[0])

    def cleanup(self) -> dict:
        """Preserve one IBKR tab; close the other dedicated-profile research tabs."""
        report = {"closed": 0, "complete": False}
        deadline = time.monotonic() + self.CLEANUP_TIMEOUT

        def command(*args):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Browser cleanup deadline reached")
            return self._command(*args, timeout=remaining)

        try:
            tabs = command("tabs").get("tabs")
            if not isinstance(tabs, list):
                raise ValueError("Missing browser tab list")
            targets = [self._tab_id(tab) for tab in tabs]
            broker_tabs = [tab for tab in tabs if is_ibkr_tab(tab)]
            keeper = self._tab_id(self._broker_keeper(broker_tabs)) if broker_tabs else None
            # Opening a blank keeper first prevents the last close from shutting Chrome.
            keeper = keeper or self._tab_id(command("open", "about:blank"))
            for target in targets:
                if target == keeper:
                    continue
                if time.monotonic() >= deadline:
                    break
                try:
                    command("close", target)
                    report["closed"] += 1
                except Exception:
                    continue
            remaining_tabs = command("tabs").get("tabs")
            if not isinstance(remaining_tabs, list):
                raise ValueError("Missing cleanup verification")
            report["complete"] = (len(remaining_tabs) == 1 and
                                  self._tab_id(remaining_tabs[0]) == keeper)
        except Exception:
            pass
        self.last_cleanup = report
        print(f"[browser] Cleanup: {report['closed']} tab(s) closed; complete={report['complete']}")
        return report

    RESEARCH_LOCK_WAIT = 180    # a 40-minute check takes about two minutes
    KEEPALIVE_LOCK_WAIT = 360   # Session 2 research can hold the browser several minutes

    @contextmanager
    def research(self):
        """Enter only when actually running browser research, not a health check."""
        with browser_lock(wait_seconds=self.RESEARCH_LOCK_WAIT) as acquired:
            if not acquired or not self.ensure_available()["available"]:
                yield None
                return
            try:
                yield self
            finally:
                try:
                    self.deadline = None
                    self.cleanup()
                except Exception:
                    print("[browser] Cleanup unavailable; continuing without browser research.")
                self._activity_after_research()

    def _activity_after_research(self):
        """Research held the browser for minutes; give verified Paper the same
        two real clicks the 40-minute check makes, so idle time doesn't build up."""
        if os.getenv("OPENCLAW_KEEPALIVE_ENABLED", "true").lower() != "true":
            return
        self.deadline = time.monotonic() + 90
        try:
            if self.paper_session().get("status") != "ready":
                return
            self._portfolio_navigation()
            record_research_activity()
            print("[browser] Paper activity after research: Positions and Dashboard clicked.")
        except Exception as error:
            print("[browser] Paper activity after research skipped: "
                  + failure_details(error, "portfolio_navigation")["reason"])
        finally:
            self.deadline = None

    def paper_session(self) -> dict:
        tabs = self._command("tabs").get("tabs", [])
        brokers = [tab for tab in tabs if is_ibkr_tab(tab)]
        if not brokers:
            return {"status": "ibkr_tab_missing"}
        tab = self._broker_keeper(brokers)
        self.keeper = self._tab_id(tab)
        if "/sso/" in urlparse(tab.get("url", "")).path.lower():
            return {"status": "login_required"}
        # Read only visible body text, never password inputs, storage, cookies or tokens.
        payload = self._command("evaluate", "--target-id", self.keeper, "--fn",
                                "() => document.body.innerText")
        visible = str(payload.get("result", ""))
        if not re.search(r"paper trading account|simulated trading", visible, re.I):
            return {"status": "paper_not_verified"}
        disconnected = bool(re.search(r"not connected to the brokerage|without Trading/Market Data", visible, re.I))
        return {"status": "paper_disconnected" if disconnected else "ready"}

    def keepalive(self) -> dict:
        """Verified Paper activity with owner-enabled reconnect/saved-form recovery."""
        self.deadline = time.monotonic() + 240
        health = self.ensure_available()
        if not health["available"]:
            reason = health.get("reason", "unexpected_error")
            state = {"status": "browser_unavailable" if reason == "cdp_not_ready" else "control_unavailable",
                     "reason": reason, "failed_step": health.get("failed_step", "status")}
            if health.get("recovery_reason"):
                state.update(recovery_reason=health["recovery_reason"])
            return state
        try:
            state = self.paper_session()
        except Exception as error:
            return {"status": "session_check_failed", **failure_details(error, "paper_session")}
        login_meta = {}
        if state['status'] == 'login_required' and os.getenv('OPENCLAW_PAPER_SAVED_LOGIN', 'false').lower() == 'true':
            from paper_login import attempt
            state = attempt(self, ROOT / 'logs' / 'browser_paper_login_state.json')
            login_meta = {key: state[key] for key in ('saved_login_attempted', 'saved_login_succeeded') if key in state}
            if state['status'] not in ('ready', 'paper_disconnected'):
                return state
        reconnected = False
        if state["status"] == "paper_disconnected" and os.getenv("OPENCLAW_PAPER_RECONNECT", "false").lower() == "true":
            state = self._reconnect_paper()
            if state["status"] != "ready":
                return {**state, **login_meta}
            reconnected = state.get("reconnect_succeeded", False)
        if state["status"] == "ready":
            self._clear_reconnect_block()
            from paper_login import clear_verified
            clear_verified(ROOT / 'logs' / 'browser_paper_login_state.json')
        if state["status"] not in ("ready", "paper_disconnected"):
            return state
        try:
            activity = self._portfolio_navigation()
        except Exception as error:
            failed = {"status": "navigation_failed", "paper_session_status": state["status"],
                      **failure_details(error, "portfolio_navigation"), **login_meta}
            if self.session_extended:
                failed["session_extended"] = True
            if self.returned_to_dashboard:
                failed["returned_to_dashboard"] = True
            return failed
        try:
            state = self.paper_session()
            if activity is True:
                state['activity_verified'] = True
            if self.session_extended:
                state['session_extended'] = True
            if self.returned_to_dashboard:
                state['returned_to_dashboard'] = True
            if reconnected:
                state.update(reconnect_attempted=True, reconnect_succeeded=True)
            return {**state, **login_meta}
        except Exception as error:
            return {"status": "session_check_failed", "paper_session_status": state["status"],
                    **failure_details(error, "paper_session"), **login_meta}

    def _write_reconnect_state(self, blocked: bool):
        path = ROOT / "logs" / "browser_reconnect_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"blocked": blocked,
                             "checked_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        temporary.replace(path)

    def _clear_reconnect_block(self):
        path = ROOT / "logs" / "browser_reconnect_state.json"
        if not path.exists():
            return
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("blocked") is not False:
                self._write_reconnect_state(False)
        except (OSError, ValueError, AttributeError):
            # A verified healthy page may clear a stale block, but state I/O
            # failure must not authorize another uncertain reconnect.
            pass

    def _reconnect_probe(self, *, click: bool = False) -> dict:
        args = ("evaluate", "--target-id", self.keeper, "--fn",
                self.RECONNECT_PROBE.replace("PERFORM_CLICK", "true" if click else "false"))
        # Evaluate normally retries observations; this evaluate clicks and must
        # never be retried after a timeout or ambiguous response.
        payload = (self._command_once(*args, timeout=self.COMMAND_TIMEOUT) if click
                   else self._command(*args))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BrowserCommandError("malformed_response", "paper_reconnect")
        return result

    def _reconnect_paper(self) -> dict:
        state = {"status": "paper_disconnected", "failed_step": "paper_reconnect"}
        path = ROOT / "logs" / "browser_reconnect_state.json"
        try:
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or type(saved.get("blocked")) is not bool:
                    return {**state, "reason": "reconnect_state_unavailable"}
                if saved["blocked"]:
                    return {**state, "reason": "reconnect_attempt_exhausted"}
            probe = self._reconnect_probe()
            if probe.get("eligible") is not True:
                return {**state, "reason": probe.get("reason", "reconnect_control_not_unique")}
            # Persist before the click. A crash/uncertain result cannot cause
            # another helper process to click again in this disconnected episode.
            self._write_reconnect_state(True)
        except Exception as error:
            return {**state, **failure_details(error, "paper_reconnect")}
        previous_deadline = self.deadline
        try:
            result = self._reconnect_probe(click=True)
            if result.get("attempted") is not True:
                return {**state, "reason": result.get("reason", "reconnect_control_not_unique")}
            self.deadline = min(previous_deadline or float("inf"), time.monotonic() + 60)
            for attempt in range(3):
                if attempt:
                    time.sleep(2)
                observed = self.paper_session()
                if observed["status"] == "ready":
                    self._write_reconnect_state(False)
                    return {**observed, "reconnect_attempted": True, "reconnect_succeeded": True}
                if observed["status"] != "paper_disconnected":
                    return {**observed, "reconnect_attempted": True}
                probe = self._reconnect_probe()
                if probe.get("eligible") is not True:
                    return {**state, "reason": probe.get("reason", "reconnect_needs_attention"),
                            "reconnect_attempted": True}
            return {**state, "reason": "reconnect_not_restored", "reconnect_attempted": True}
        except Exception as error:
            return {**state, **failure_details(error, "paper_reconnect"), "reconnect_attempted": True}
        finally:
            self.deadline = previous_deadline

    # Locates the portal's own "Dashboard"/"Positions" tab strip, re-checks the
    # Paper/authentication/dialog guards and reports which tab is active plus
    # the on-screen centre of TARGET_TAB. It never clicks; the click is a real
    # mouse event sent by OpenClaw so the portal sees genuine user activity.
    TAB_PROBE = r"""() => {
        const target = TARGET_TAB;
        const text = document.body.innerText;
        const visible = e => e.getClientRects().length > 0 &&
            getComputedStyle(e).visibility !== 'hidden';
        const deny = reason => ({ok: false, reason});
        if (!(location.hostname === 'interactivebrokers.com' ||
              location.hostname.endsWith('.interactivebrokers.com')) ||
            !/^\/portal(?:\/|$)/.test(location.pathname))
            return deny('paper_navigation_not_verified');
        if (!/paper trading account|simulated trading/i.test(text))
            return deny('paper_navigation_not_verified');
        if (document.querySelector('input[type=password]') ||
            /verification code|two.factor authentication|verify you are human|captcha/i.test(text))
            return deny('authentication_required');
        const dialogs = Array.from(document.querySelectorAll(DIALOG_SELECTOR))
            .filter(e => visible(e) && e.innerText.trim().length > 0);
        const sessionWarning = SESSION_WARNING_FN;
        if (dialogs.some(sessionWarning))
            return deny('session_expiry_warning');
        // Modal or confirmation dialogs stop the check. Non-modal side panels
        // (for example Orders & Trades) do not, because the click below only
        // proceeds when nothing covers the tab itself.
        if (dialogs.some(d => d.getAttribute('aria-modal') === 'true' ||
                d.getAttribute('role') === 'alertdialog' ||
                /existing session|continue login|disconnect|confirm|password/i.test(d.innerText)))
            return deny('dialog_requires_attention');
        const controls = Array.from(document.querySelectorAll('a,button,[role=tab]'))
            .filter(e => visible(e) && !e.closest(DIALOG_SELECTOR));
        const named = name => controls.filter(e => e.innerText.trim() === name);
        const strips = [];
        for (const positions of named('Positions')) {
            let parent = positions.parentElement;
            for (let i = 0; parent && parent !== document.body && i < 5;
                 i++, parent = parent.parentElement) {
                const dashboards = named('Dashboard').filter(e => parent.contains(e));
                if (dashboards.length) { strips.push({positions, dashboards}); break; }
            }
        }
        if (strips.length !== 1 || strips[0].dashboards.length !== 1)
            return deny('tab_strip_not_unique');
        const tabs = {Positions: strips[0].positions, Dashboard: strips[0].dashboards[0]};
        const marked = e => [e, e.parentElement].some(n => n &&
            (n.getAttribute('aria-selected') === 'true' || n.getAttribute('aria-current') === 'page' ||
             /\b(active|selected|current)\b/i.test(n.getAttribute('class') || '')));
        // Longest matching hash route wins: '#/dashboard/positions' is Positions, not Dashboard.
        let current = null, longest = -1;
        for (const [name, e] of Object.entries(tabs)) {
            const href = (e.getAttribute('href') || '').replace(/\/$/, '');
            if (href.startsWith('#/') && (location.hash.replace(/\/$/, '') === href ||
                location.hash.startsWith(href + '/') || location.hash.startsWith(href + '?')) &&
                href.length > longest) { current = name; longest = href.length; }
        }
        if (!current) current = Object.keys(tabs).find(name => marked(tabs[name])) || null;
        const element = tabs[target];
        element.scrollIntoView({block: 'nearest', inline: 'nearest'});
        const box = element.getBoundingClientRect();
        const x = Math.round(box.left + box.width / 2), y = Math.round(box.top + box.height / 2);
        const hit = document.elementFromPoint(x, y);
        if (!hit || !(hit === element || element.contains(hit)))
            return deny('tab_covered');
        return {ok: true, current, x, y};
    }"""
    TAB_DWELL_SECONDS = 3

    # IBKR's inactivity warning: expiry/inactivity wording, nothing about
    # orders, trades or transfers. Shared by both probes so they agree.
    DIALOG_SELECTOR = "'[role=dialog],[role=alertdialog],[aria-modal=true]'"
    SESSION_WARNING_FN = r"""(d => /(session|you will be|you'll be)[\s\S]{0,120}(expir|time ?out|timed out|log(ged)? ?out|sign(ed)? ?out)|inactiv|\bidle\b/i.test(d.innerText) &&
        !/order|trade|transfer|buy|sell|position|password|verification|captcha/i.test(d.innerText))"""
    # Only these exact labels, and only inside a verified session warning.
    STAY_LABELS = r"/^(stay logged in|stay signed in|keep me logged in|keep me signed in|continue session|extend session|continue|i'?m still here|stay)$/i"
    SESSION_PROBE = r"""() => {
        const visible = e => e.getClientRects().length > 0 &&
            getComputedStyle(e).visibility !== 'hidden';
        const text = document.body.innerText;
        const deny = reason => ({present: true, ok: false, reason});
        const dialogs = Array.from(document.querySelectorAll(DIALOG_SELECTOR))
            .filter(e => visible(e) && e.innerText.trim().length > 0);
        const warnings = dialogs.filter(SESSION_WARNING_FN);
        if (!warnings.length) return {present: false};
        if (!(location.hostname === 'interactivebrokers.com' ||
              location.hostname.endsWith('.interactivebrokers.com')) ||
            !/^\/portal(?:\/|$)/.test(location.pathname) ||
            !/paper trading account|simulated trading/i.test(text))
            return deny('paper_navigation_not_verified');
        if (document.querySelector('input[type=password]'))
            return deny('authentication_required');
        if (warnings.length !== 1) return deny('session_warning_not_unique');
        const buttons = Array.from(warnings[0].querySelectorAll('button,a,[role=button]'))
            .filter(e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true' &&
                    STAY_LABELS.test(e.innerText.trim()));
        if (buttons.length !== 1) return deny('session_warning_button_unknown');
        const box = buttons[0].getBoundingClientRect();
        const x = Math.round(box.left + box.width / 2), y = Math.round(box.top + box.height / 2);
        const hit = document.elementFromPoint(x, y);
        if (!hit || !(hit === buttons[0] || buttons[0].contains(hit)))
            return deny('session_button_covered');
        return {present: true, ok: true, x, y};
    }"""

    @classmethod
    def _script(cls, source: str) -> str:
        return (source.replace("SESSION_WARNING_FN", cls.SESSION_WARNING_FN)
                .replace("DIALOG_SELECTOR", cls.DIALOG_SELECTOR)
                .replace("STAY_LABELS", cls.STAY_LABELS))

    @staticmethod
    def _probe_result(payload: dict, step: str, fallback: str) -> dict:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BrowserCommandError("malformed_response", step)
        if result.get("ok") is False:
            reason = result.get("reason")
            safe = reason if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,40}", reason) else fallback
            raise BrowserCommandError(safe, step)
        return result

    @staticmethod
    def _valid_point(result: dict, step: str) -> tuple[int, int]:
        x, y = result.get("x"), result.get("y")
        if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10000 for v in (x, y)):
            raise BrowserCommandError("click_position_invalid", step)
        return x, y

    def _session_probe(self) -> dict:
        payload = self._command("evaluate", "--target-id", self.keeper, "--fn",
                                self._script(self.SESSION_PROBE))
        return self._probe_result(payload, "session_warning", "session_probe_failed")

    def _extend_session(self) -> bool:
        """Click IBKR's own stay-logged-in button once, only inside its expiry warning."""
        probe = self._session_probe()
        if probe.get("present") is not True:
            return False
        x, y = self._valid_point(probe, "session_warning")
        # Real mouse click, never retried after an uncertain result.
        self._command("click-coords", str(x), str(y), "--target-id", self.keeper)
        for _ in range(4):
            time.sleep(1.5)
            if self._session_probe().get("present") is False:
                return True
        raise BrowserCommandError("session_warning_not_cleared", "session_warning")

    def _tab_probe(self, target: str) -> dict:
        payload = self._command("evaluate", "--target-id", self.keeper, "--fn",
                                self._script(self.TAB_PROBE).replace("TARGET_TAB", json.dumps(target)))
        result = self._probe_result(payload, "portfolio_navigation", "tab_probe_failed")
        if result.get("ok") is not True:
            raise BrowserCommandError("malformed_response", "portfolio_navigation")
        return result

    def _click_tab(self, target: str, *, allow_return: bool = False):
        try:
            probe = self._tab_probe(target)
        except BrowserCommandError as error:
            # Before any click only: Paper/login/dialog guards already passed,
            # the tab just sits on another portal page.
            if not (allow_return and error.reason == "tab_strip_not_unique"):
                raise
            self._return_to_dashboard()
            probe = self._tab_probe(target)
        x, y = self._valid_point(probe, "portfolio_navigation")
        # A real mouse click (not a script click or URL change). Never retried:
        # the first click may already have switched the tab.
        self._command("click-coords", str(x), str(y), "--target-id", self.keeper)
        for _ in range(4):
            time.sleep(1.5)
            if self._tab_probe(target).get("current") == target:
                return
        raise BrowserCommandError(f"{target.lower()}_click_unconfirmed", "portfolio_navigation")

    RETURN_WAIT_SECONDS = 4

    def _return_to_dashboard(self):
        """The verified Paper tab is on another portal page (a quote or option
        chain): load the portal Dashboard so the tab strip exists."""
        url = self._command("evaluate", "--target-id", self.keeper, "--fn", "() => location.href").get("result")
        if (not isinstance(url, str) or not is_ibkr_tab({"url": url})
                or not urlparse(url).path.lower().startswith("/portal")):
            raise BrowserCommandError("paper_navigation_not_verified", "portfolio_navigation")
        self._command("navigate", url.split("#", 1)[0] + "#/dashboard/", "--target-id", self.keeper)
        time.sleep(self.RETURN_WAIT_SECONDS)
        self.returned_to_dashboard = True

    def _portfolio_navigation(self):
        # Answer IBKR's inactivity warning first (if shown), bring the tab back
        # to Dashboard if it was left on another portal page, then two genuine
        # clicks: open Positions, pause, return to Dashboard. Clicking only
        # these controls and verifying the result avoids counting a no-op.
        self.session_extended = self._extend_session()
        self._click_tab("Positions", allow_return=True)
        time.sleep(self.TAB_DWELL_SECONDS)
        self._click_tab("Dashboard")
        return True

    def visible_page(self, url: str) -> dict:
        target = self._tab_id(self._command("open", url))
        # Open waits for navigation. Read rendered DOM cells, not hidden API calls.
        payload = self._command("evaluate", "--target-id", target, "--fn", """() => ({
            title: document.title, text: document.body.innerText.slice(0, 90000),
            tables: Array.from(document.querySelectorAll('table')).filter(t => t.innerText.trim())
                .map(t => Array.from(t.rows).filter(r => r.innerText.trim())
                    .map(r => Array.from(r.cells).map(c => c.innerText.trim()))),
            option_symbols: Array.from(document.querySelectorAll('select option'))
                .map(e => e.textContent.trim()).filter(s => /^[A-Z][A-Z0-9.]{0,11}$/.test(s))
        })""")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("Visible page unavailable")
        return result

    def run_research(self, researcher: Callable[[CanadianResearchBrowser], dict] | None = None) -> dict:
        """Fail-soft entry point with a shared lock and guaranteed cleanup attempt."""
        if researcher is None:
            return {}
        try:
            with self.research() as browser:
                if browser is None:
                    return {}
                result = researcher(browser)
                return result if isinstance(result, dict) else {}
        except Exception:
            print("[browser] Research unavailable; continuing existing analysis.")
            return {}


def optional_browser_status() -> dict:
    if os.getenv("OPENCLAW_BROWSER_CHECK", "true").lower() != "true":
        return {"profile": CanadianResearchBrowser.PROFILE, "available": False, "status": "disabled"}
    try:
        return CanadianResearchBrowser().check()
    except Exception:
        return {"profile": CanadianResearchBrowser.PROFILE, "available": False, "status": "unavailable"}


def format_browser_status(status: dict) -> str:
    label = "ready" if status.get("available") else "unavailable"
    if status.get("status") == "disabled":
        label = "check disabled"
    return (f"Research Chrome: {label}. Optional read-only verification; "
            "market/news/DeepSeek analysis continues independently.")


def _append_history(row: dict, state_path: Path | None = None):
    path = (state_path or ROOT / "logs" / "browser_session_state.json").with_name("browser_monitor_history.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        pass


def record_research_activity(state_path: Path | None = None):
    """Log verified clicks made at the end of research; keep notice dedup untouched."""
    now = datetime.now(timezone.utc).isoformat()
    _append_history({"checked_at": now, "status": "ready", "activity_verified": True,
                     "source": "session_research", "notify_enabled": False}, state_path)
    state_path = state_path or ROOT / "logs" / "browser_session_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            state["last_verified_activity_at"] = now
            temporary = state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state), encoding="utf-8")
            temporary.replace(state_path)
    except (OSError, ValueError):
        pass


LOGGED_OUT_STATUSES = ("login_required", "ibkr_tab_missing")
# Calgary time window before the 11:00 Session 2 options scan. At least 50
# minutes wide so one 40-minute check always lands inside it.
REMINDER_START, REMINDER_END = (10, 5), (10, 58)
OPTIONS_REMINDER = ("IBKR Paper reminder: still logged out. Session 2 runs at 11:00 - log in "
                    "before then so the options scan can use IBKR option quotes; otherwise it "
                    "uses public MX pages only.")


def options_reminder_due(status: str, previous: dict, now: datetime | None = None) -> str | None:
    """Today's Calgary date when the once-per-trading-day login reminder is due."""
    if status not in LOGGED_OUT_STATUSES:
        return None
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Denver"))
    start = local.replace(hour=REMINDER_START[0], minute=REMINDER_START[1], second=0, microsecond=0)
    end = local.replace(hour=REMINDER_END[0], minute=REMINDER_END[1], second=0, microsecond=0)
    today = local.date().isoformat()
    if not start <= local < end or previous.get("options_reminder_date") == today:
        return None
    try:
        from trading_calendar import calendar
        if not calendar().is_session(today):
            return None
    except Exception:
        if local.weekday() >= 5:
            return None
    return today


def check_paper_session(*, notify: bool = True, state_path: Path | None = None,
                        lock_wait: float | None = None) -> dict:
    """Persist minimal health state and notify only on a change; retry failed sends."""
    state_path = state_path or ROOT / "logs" / "browser_session_state.json"
    started = time.monotonic()
    wait = CanadianResearchBrowser.KEEPALIVE_LOCK_WAIT if lock_wait is None else lock_wait
    # Wait for research instead of skipping: a skipped check once left 80
    # minutes without activity and IBKR logged out.
    with browser_lock(wait_seconds=wait) as acquired:
        if not acquired:
            # Still busy after waiting: record it so a gap is visible in history.
            _append_history({"checked_at": datetime.now(timezone.utc).isoformat(), "status": "busy",
                             "notify_enabled": notify,
                             "elapsed_seconds": round(time.monotonic() - started, 1)}, state_path)
            return {"status": "busy"}
        try:
            if os.getenv("OPENCLAW_KEEPALIVE_ENABLED", "true").lower() != "true":
                return {"status": "disabled"}
            browser = CanadianResearchBrowser()
            state = browser.keepalive()
        except Exception as error:
            state = {"status": "helper_failed", **failure_details(error, "keepalive")}
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        status = state["status"]
        notified = previous.get("notified_status")
        notified_reason = previous.get("notified_reason")
        notified_step = previous.get("notified_step")
        notified_recovery = previous.get("notified_recovery_reason")
        # Initial healthy checks stay quiet. A login failure does not stop analysis.
        actionable = status != "ready" or notified not in (None, "ready")
        changed = ((status, state.get("reason"), state.get("failed_step"), state.get("recovery_reason")) !=
                   (notified, notified_reason, notified_step, notified_recovery))
        if notify and actionable and changed:
            from telegram_notify import send
            descriptions = {
                "control_unavailable": "OpenClaw control connection unavailable. This does not establish that Chrome is closed or IBKR is logged out.",
                "browser_unavailable": "The dedicated Chrome connection is not ready.",
                "session_check_failed": "Could not verify the IBKR page; account login state is unknown.",
                "navigation_failed": "Paper session was verified, but read-only portfolio navigation failed.",
                "login_required": "Chrome is available for public research. IBKR Paper sign-in needs attention; the helper's safe reason indicates whether saved-form sign-in was unavailable, blocked or attempted.",
                "ibkr_tab_missing": "Chrome is available for public research. Please open IBKR and log into Paper when convenient.",
                "ready": "Read-only browser checks recovered.",
            }
            message = "IBKR paper browser: " + descriptions.get(status, status.replace("_", " ") + ".")
            if state.get("reason"):
                message += f" Check: {state['failed_step']}; reason: {state['reason']}."
            if state.get("recovery_reason"):
                message += f" Browser recovery: {state['recovery_reason']}."
            if status != "ready":
                message += " Please check when available. IBKR login is not required for independent analysis. Analysis-task health is checked separately."
            if send(message, parse_mode=""):
                notified = status
                notified_reason = state.get("reason")
                notified_step = state.get("failed_step")
                notified_recovery = state.get("recovery_reason")
        reminder_date = previous.get("options_reminder_date")
        due = options_reminder_due(status, previous) if notify else None
        if due:
            from telegram_notify import send
            if send(OPTIONS_REMINDER, parse_mode=""):
                reminder_date = due
                state["options_reminder_sent"] = True
        if reminder_date:
            state["options_reminder_date"] = reminder_date
        state.update(checked_at=datetime.now(timezone.utc).isoformat(), notified_status=notified)
        if state.get('status') == 'ready' and state.get('activity_verified') is True:
            state['last_verified_activity_at'] = state['checked_at']
        elif previous.get('last_verified_activity_at'):
            state['last_verified_activity_at'] = previous['last_verified_activity_at']
        if notified_reason:
            state.update(notified_reason=notified_reason, notified_step=notified_step)
        if notified_recovery:
            state.update(notified_recovery_reason=notified_recovery)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(state_path)
        history = {key: state[key] for key in ('checked_at', 'status', 'reason', 'failed_step',
                   'recovery_reason', 'activity_verified', 'session_extended', 'returned_to_dashboard',
                   'options_reminder_sent',
                   'saved_login_attempted', 'saved_login_succeeded',
                   'reconnect_attempted', 'reconnect_succeeded')
                   if key in state}
        history.update(notify_enabled=notify, elapsed_seconds=round(time.monotonic() - started, 1))
        try:
            prior_at = datetime.fromisoformat(previous['checked_at'])
            history['previous_check_gap_seconds'] = round(
                (datetime.now(timezone.utc) - prior_at).total_seconds())
        except (KeyError, TypeError, ValueError):
            pass
        try:
            with state_path.with_name('browser_monitor_history.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(history) + '\n')
        except OSError:
            state['history_unavailable'] = True
        return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    parser.add_argument("action", choices=("status", "cleanup", "keepalive", "recover"))
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    if args.action == "status":
        print(json.dumps(optional_browser_status()))
    elif args.action == "keepalive":
        from analysis_health import check_analysis_health
        try:
            analysis = check_analysis_health(notify=not args.no_notify)
        except Exception:
            analysis = {"status": "analysis_check_unavailable", "reason": "task_read_failed"}
        print(json.dumps({"status": analysis["status"], "failed_step": "analysis_scheduler",
                          **({"reason": analysis["reason"]} if analysis.get("reason") else {})}))
        print(json.dumps(check_paper_session(notify=not args.no_notify)))
    elif args.action == "recover":
        with browser_lock() as acquired:
            result = CanadianResearchBrowser().ensure_available() if acquired else {"status": "busy"}
        print(json.dumps(result))
    else:
        with browser_lock() as acquired:
            result = CanadianResearchBrowser().cleanup() if acquired else {"complete": False, "closed": 0}
        print(json.dumps(result))
        raise SystemExit(0 if result["complete"] else 1)
