"""Read-only CDE option quotes from the logged-in IBKR Paper portal tab.

The portal page already loads IBKR market data from its own data service. This
module asks that service, from inside the verified Paper tab, for contract
definitions and quote snapshots only. Every request is a GET to an allow-listed
contract-definition or market-data path; order, account, portfolio and
settings paths are never called. Nothing is clicked or typed.

Collection covers every listed expiry in the requested month range and several
strikes on each side of spot for each candidate's direction. Recommendation
gates (21-45 DTE, spread, open interest/volume, timestamps) stay in
research_pipeline.validated_contract.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re
import time

SOURCE = "IBKR CDE snapshot"
STRIKES_PER_SIDE = 4          # strikes below and above spot per month
SYMBOLS_PER_CALL = 4          # candidates resolved per page evaluation
SNAPSHOT_BATCH = 40           # contracts per snapshot wave (stays well inside market-data lines)
SNAPSHOT_ROUNDS, SNAPSHOT_WAIT_MS = 8, 1500   # up to ~12 s for bid/ask to arrive per wave
DELAY_MINUTES = 15            # IBKR delayed data lag used for quote_time
FIELDS = "31,84,85,86,88,87,7762,7638,6509,7308,7633"

# Shared request guard: only these read-only paths, only GET.
_GET = r"""
  const base = '/portal.proxy/v1/portal/iserver/';
  const allowed = /^(secdef\/(search|strikes|info)\?|marketdata\/snapshot\?)/;
  const pause = ms => new Promise(r => setTimeout(r, ms));
  const get = async (path) => {
    if (!allowed.test(path)) throw new Error('blocked_path');
    await pause(60);
    const r = await fetch(base + path, {method: 'GET', credentials: 'include'});
    let body = null; try { body = await r.json(); } catch (e) {}
    return {status: r.status, body};
  };
"""

RESOLVE_FN = r"""async () => {
  const spec = SPEC;
""" + _GET + r"""
  const resolveOne = async (item) => {
    const out = {symbol: item.symbol, contracts: []};
    try {
      const search = await get('secdef/search?symbol=' + encodeURIComponent(item.query) + '&secType=STK');
      const rows = Array.isArray(search.body) ? search.body : [];
      const listed = rows.filter(r => ['TSE', 'VENTURE'].includes(r.description) &&
          (!r.symbol || r.symbol.toUpperCase() === item.query.toUpperCase()));
      if (search.status === 429) { out.reason = 'ibkr_rate_limited'; return out; }
      if (listed.length !== 1) { out.reason = listed.length ? 'ibkr_symbol_ambiguous' : 'ibkr_symbol_not_found'; return out; }
      const stock = listed[0];
      out.conid = Number(stock.conid);
      const section = (stock.sections || []).find(s => s.secType === 'OPT' &&
          (s.exchange || '').split(';').includes('CDE'));
      if (!section) { out.reason = 'ibkr_no_cde_options'; return out; }
      const months = (section.months || '').split(';').filter(m => spec.months.includes(m));
      if (!months.length) { out.reason = 'ibkr_no_month_in_window'; return out; }
      const key = item.right === 'C' ? 'call' : 'put';
      for (const month of months) {
        const strikes = await get(`secdef/strikes?conid=${stock.conid}&sectype=OPT&month=${month}&exchange=CDE`);
        const list = ((strikes.body || {})[key] || []).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
        const below = list.filter(s => s <= item.spot).slice(-spec.perSide);
        const above = list.filter(s => s > item.spot).slice(0, spec.perSide);
        // Staggered parallel lookups: faster than one-by-one, still paced.
        const infos = await Promise.all(below.concat(above).map((strike, i) => pause(i * spec.staggerMs).then(() =>
            get(`secdef/info?conid=${stock.conid}&sectype=OPT&month=${month}` +
                `&exchange=CDE&strike=${strike}&right=${item.right}`).then(info => ({strike, info})))));
        for (const {strike, info} of infos) {
          if (info.status === 429) out.rate_limited = true;
          for (const c of (Array.isArray(info.body) ? info.body : [])) {
            if (c.maturityDate >= spec.first && c.maturityDate <= spec.last)
              out.contracts.push({conid: Number(c.conid), right: item.right, strike, maturity: c.maturityDate,
                  multiplier: c.multiplier, exchange: c.exchange, currency: c.currency});
          }
        }
      }
      if (!out.contracts.length) out.reason = out.rate_limited ? 'ibkr_rate_limited' : 'ibkr_no_contract_in_window';
    } catch (e) {
      out.reason = 'ibkr_lookup_failed';
    }
    return out;
  };
  // Names in this group run side by side, started slightly apart.
  return Promise.all(spec.items.map((item, i) => pause(i * spec.itemStaggerMs).then(() => resolveOne(item))));
}"""

SNAPSHOT_FN = r"""async () => {
  const spec = SPEC;
""" + _GET + r"""
  const merged = {};
  // Blank/null/prefixed prices are still pending; a genuine zero is settled
  // and will be rejected by the shared quote validator rather than retried.
  const numeric = value => {
    if (typeof value !== 'string' && typeof value !== 'number') return false;
    const text = String(value).trim().replace(/,/g, '');
    const match = text.match(/^(-?\d+(?:\.\d+)?)([KMB]?)$/i);
    return !!match && Number.isFinite(Number(match[1]) *
        ({'': 1, K: 1e3, M: 1e6, B: 1e9}[match[2].toUpperCase()]));
  };
  const done = row => row && ((numeric(row['84']) && numeric(row['86'])) ||
      /^[ZYN]/.test(String(row['6509'] || '')));
  for (let round = 0; round < spec.rounds; round++) {
    if (round) await pause(spec.waitMs);
    const pending = spec.conids.filter(id => !done(merged[id]));
    if (!pending.length) break;
    const snap = await get(`marketdata/snapshot?conids=${pending.join(',')}&fields=${spec.fields}`);
    for (const row of (Array.isArray(snap.body) ? snap.body : [])) {
      const id = Number(row.conid);
      if (spec.conids.includes(id)) merged[id] = Object.assign(merged[id] || {}, row);
    }
  }
  return merged;
}"""


def _script(source: str, spec: dict) -> str:
    return source.replace("SPEC", json.dumps(spec), 1)


def month_codes(first: date, last: date) -> list[str]:
    codes, current = [], first.replace(day=1)
    while current <= last:
        codes.append(current.strftime("%b%y").upper())
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return codes


def ibkr_number(value) -> float | None:
    """Parse IBKR display numbers such as '1,234', '1.2K' or '0.95'. Prefixed
    values like 'C1.37' (prior close) are not live quotes and are rejected."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)([KMB]?)", text, re.I)
    if not match:
        return None
    scale = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[match[2].upper()]
    return float(match[1]) * scale


def _spot(value) -> float | None:
    if value is None:
        return None
    return ibkr_number(re.sub(r"^[A-Z]", "", str(value).strip()))


def contract_rows(symbol: str, contracts: list[dict], quotes: dict, spot: float | None,
                  now: datetime) -> list[dict]:
    """Convert IBKR snapshots into the shared quote-row shape (not yet validated)."""
    rows = []
    for contract in contracts:
        quote = quotes.get(str(contract["conid"])) or quotes.get(contract["conid"]) or {}
        availability = str(quote.get("6509", ""))[:1]
        updated = quote.get("_updated")
        quote_time = None
        if isinstance(updated, (int, float)) and availability in ("R", "D"):
            stamp = datetime.fromtimestamp(updated / 1000, timezone.utc)
            if availability == "D":
                stamp -= timedelta(minutes=DELAY_MINUTES)
            quote_time = stamp.isoformat()
        maturity = str(contract.get("maturity", ""))
        try:
            expiry = datetime.strptime(maturity, "%Y%m%d").date().isoformat()
        except ValueError:
            expiry = ""
        volume = ibkr_number(quote.get("7762"))
        if volume is None:
            volume = ibkr_number(quote.get("87"))
        rows.append({
            "symbol": symbol, "currency": contract.get("currency"), "exchange": contract.get("exchange"),
            "right": "CALL" if contract["right"] == "C" else "PUT", "expiry": expiry,
            "strike": contract.get("strike"), "bid": ibkr_number(quote.get("84")),
            "ask": ibkr_number(quote.get("86")), "bid_size": ibkr_number(quote.get("88")),
            "ask_size": ibkr_number(quote.get("85")), "open_interest": ibkr_number(quote.get("7638")),
            "volume": volume, "delta": ibkr_number(quote.get("7308")),
            "implied_vol": (ibkr_number(str(quote.get("7633", "")).rstrip("%")) or 0) / 100 or None,
            "availability": availability or None, "ibkr_conid": contract["conid"],
            "source": SOURCE, "source_url": None, "quote_time": quote_time,
            "observed_at": now.isoformat(), "spot": spot,
            "spot_reference": "previous_completed_close",
            "data_mode": {"R": "IBKR Paper real-time", "D": "IBKR Paper delayed ~15 min"}.get(
                availability, "IBKR quote unavailable"),
            "multiplier": ibkr_number(contract.get("multiplier")),
        })
    return rows


def quote_order(resolved: dict, spots: dict) -> list[int]:
    """Most useful contracts first, rotating across names: nearest qualifying
    expiry, then strike closest to spot. A time limit then trims only the
    least useful quotes instead of whole names at the end of the list."""
    queues = []
    for symbol, result in resolved.items():
        spot = spots.get(symbol) or 0
        contracts = [c for c in result.get("contracts", []) if isinstance(c.get("conid"), int)]
        contracts.sort(key=lambda c: (str(c.get("maturity", "")), abs(float(c.get("strike") or 0) - spot)))
        queues.append([c["conid"] for c in contracts])
    ordered, depth = [], max((len(q) for q in queues), default=0)
    for index in range(depth):
        ordered.extend(q[index] for q in queues if index < len(q))
    return list(dict.fromkeys(ordered))


def collect_quotes(browser, directions: dict[str, str], spots: dict[str, float], deadline: float,
                   *, validate, now: datetime | None = None) -> dict:
    """Return {symbol: evidence item} for names IBKR could check before `deadline`.

    `browser` must already hold the research lock and have a verified Paper
    keeper tab. `validate(row, symbol, diagnostics=...)` applies the shared gates.
    """
    now = now or datetime.now(timezone.utc)
    first, last = now.date() + timedelta(days=21), now.date() + timedelta(days=45)
    spec_base = {"months": month_codes(first, last), "perSide": STRIKES_PER_SIDE, "staggerMs": 120,
                 "itemStaggerMs": 400,
                 "first": first.strftime("%Y%m%d"), "last": last.strftime("%Y%m%d")}
    items = [{"symbol": symbol, "query": symbol, "right": "C" if side == "CALL" else "P",
              "spot": float(spots[symbol])}
             for symbol, side in directions.items()
             if side in ("CALL", "PUT") and isinstance(spots.get(symbol), (int, float)) and spots[symbol] > 0]
    resolved = {}
    for start in range(0, len(items), SYMBOLS_PER_CALL):
        if deadline - time.monotonic() < 45:
            break
        chunk = items[start:start + SYMBOLS_PER_CALL]
        try:
            payload = browser._command("evaluate", "--target-id", browser.keeper, "--timeout-ms", "30000",
                                       "--fn", _script(RESOLVE_FN, {**spec_base, "items": chunk}))
        except Exception:
            continue
        for result in payload.get("result") or []:
            if isinstance(result, dict) and result.get("symbol") in directions:
                resolved[result["symbol"]] = result
    conids = quote_order(resolved, spots)
    quotes, requested = {}, set()
    for start in range(0, len(conids), SNAPSHOT_BATCH):
        if deadline - time.monotonic() < 25:
            break
        batch = conids[start:start + SNAPSHOT_BATCH]
        requested.update(batch)
        try:
            payload = browser._command("evaluate", "--target-id", browser.keeper, "--timeout-ms", "30000",
                                       "--fn", _script(SNAPSHOT_FN, {"conids": batch, "fields": FIELDS,
                                                                     "rounds": SNAPSHOT_ROUNDS,
                                                                     "waitMs": SNAPSHOT_WAIT_MS}))
            result = payload.get("result")
            if isinstance(result, dict):
                quotes.update({str(k): v for k, v in result.items() if isinstance(v, dict)})
        except Exception:
            continue
    evidence = {}
    observed_at = datetime.now(timezone.utc)
    for symbol, result in resolved.items():
        diagnostics = {}
        contracts = []
        quoted = 0
        if result.get("contracts"):
            for row in contract_rows(symbol, result["contracts"], quotes, spots.get(symbol), observed_at):
                side = diagnostics.setdefault(row["right"], {})
                quoted += row["bid"] is not None or row["ask"] is not None
                if row["bid"] is None and row["ask"] is None:
                    # Not quoted within the time limit; not the same as a zero bid.
                    rejected = side.setdefault("rejected", {})
                    reason = "ibkr_quote_not_requested" if row["ibkr_conid"] not in requested else "ibkr_quote_not_received"
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                checked = validate(row, symbol, diagnostics=side)
                if checked:
                    contracts.append(checked)
        item = {"status": "observed", "source_kind": "ibkr", "observed_at": observed_at.isoformat(),
                "contracts": contracts, "contract_validation": diagnostics, "sources": [],
                "requested_option_right": directions.get(symbol),
                "ibkr_contracts_checked": len(result.get("contracts", [])), "ibkr_contracts_quoted": quoted}
        # Any unusable sample may still have qualifying contracts on MX.
        reason = result.get("reason") or ("ibkr_quotes_unavailable" if result.get("contracts") and not quoted
                                          else "ibkr_no_qualified_contracts" if result.get("contracts") and not contracts
                                          else None)
        if reason:
            item["collection_reason"] = reason
        evidence[symbol] = item
    return evidence
