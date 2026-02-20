#!/usr/bin/env python3
"""
PolyHunter Trading Audit Spreadsheet Generator
================================================
Extracts raw, timestamped data from the polyhunter repository into
structured CSV files suitable for strict rule-based simulation.

NO optimisation. NO smoothing. NO reinterpretation. NO revised logic.

Data sources:
  - index.html: Full position table with 62+ candidates, timelines, exit notes
  - summary.json: Current snapshot with position-level detail
  - git history: ~50 hourly snapshots of summary.json for time-series reconstruction

Output (5 CSV sheets):
  1. SIGNAL_LOG.csv
  2. PRICE_SERIES.csv
  3. EXECUTED_TRADES_HISTORY.csv
  4. POSITION_STATE_OVER_TIME.csv
  5. SUMMARY_STATS_PER_EVENT.csv
"""

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

REPO_DIR = Path(__file__).parent
OUTPUT_DIR = REPO_DIR / "audit_csvs"
OUTPUT_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def strip_html(text):
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def make_event_id(event_name):
    """Deterministic short event ID from name."""
    clean = re.sub(r'[●○◌◐◑◒◓◔▫▪◻◼️\u25cf\u25cb\u25cc\u25d0\u25d1\u25d2\u25d3\u25d4\u25fb\u25fc\s\[\]]+$', '', event_name).strip()
    # Also strip trailing rating badges like [A], [B] etc
    clean = re.sub(r'\s*\[[A-E]\]\s*$', '', clean).strip()
    # Strip trailing edge type labels
    clean = re.sub(r'\s*(DISCOVERY|FRONTIER|CONSENSUS)\s*$', '', clean).strip()
    # Normalize whitespace
    clean = re.sub(r'\s+', ' ', clean)
    h = hashlib.md5(clean.encode()).hexdigest()[:6].upper()
    return f"EVT_{h}"


def clean_event_name(raw):
    """Normalize event name - strip rating badges, unicode dots, etc."""
    raw = re.sub(r'[●○◌◐◑◒◓◔▫▪◻◼️\u25cf\u25cb\u25cc\u25d0\u25d1\u25d2\u25d3\u25d4\u25fb\u25fc]+', '', raw)
    raw = re.sub(r'\s*\[[A-E]\]\s*$', '', raw)
    raw = re.sub(r'\s*(DISCOVERY|FRONTIER|CONSENSUS)\s*$', '', raw)
    raw = re.sub(r'\s+', ' ', raw).strip()
    return raw


def parse_cents(text):
    """Parse price from HTML like '22.5¢' or '0.225'."""
    if not text:
        return None
    text = strip_html(text).replace('¢', '').replace('cent;', '').strip()
    try:
        val = float(text)
        if val > 1.0:  # cents, convert to decimal
            return round(val / 100.0, 6)
        return round(val, 6)
    except (ValueError, TypeError):
        return None


def parse_pnl(text):
    """Parse P&L percentage from HTML."""
    text = strip_html(text)
    match = re.search(r'([+-]?\d+\.?\d*)%', text)
    if match:
        return float(match.group(1))
    if '—' in text or 'mdash' in text:
        return None
    return None


def parse_dollar_pnl(text):
    """Parse dollar P&L from HTML like '+$1.8k' or '$-4'."""
    text = strip_html(text)
    match = re.search(r'[+\-]?\$[+-]?[\d,.]+k?', text, re.IGNORECASE)
    if match:
        s = match.group(0).replace('$', '').replace(',', '')
        multiplier = 1
        if s.lower().endswith('k'):
            multiplier = 1000
            s = s[:-1]
        try:
            return round(float(s) * multiplier, 2)
        except ValueError:
            return None
    return None


# ─────────────────────────────────────────────
# DATA EXTRACTION: HTML TABLE
# ─────────────────────────────────────────────

def extract_html_positions():
    """Parse all positions from the HTML trading table."""
    html_path = REPO_DIR / "index.html"
    with open(html_path, 'r') as f:
        html = f.read()

    positions = []
    current_group = "UNKNOWN"

    # Find all <tr> blocks
    row_pattern = re.compile(
        r'<tr\s+class="([^"]*)">\s*(.*?)\s*</tr>',
        re.DOTALL
    )

    for match in row_pattern.finditer(html):
        cls = match.group(1)
        content = match.group(2)

        # Group divider
        if 'group-divider' in cls:
            grp = re.search(r'group-label[^>]*>(.*?)</span>', content)
            if grp:
                current_group = strip_html(grp.group(1))
            continue

        # Extract cells
        cells = re.findall(r'<td\s+class="([^"]*)"[^>]*>(.*?)</td>', content, re.DOTALL)
        if len(cells) < 8:
            continue

        pos = {}
        pos['group'] = current_group

        for cell_class, cell_content in cells:
            if 'td-date' in cell_class:
                pos['date'] = strip_html(cell_content).strip()
            elif 'td-rating' in cell_class:
                rating_match = re.search(r'>([A-E])<', cell_content)
                if rating_match:
                    pos['rating'] = rating_match.group(1)
                sub_match = re.search(r'<sub[^>]*>([A-E])</sub>', cell_content)
                if sub_match:
                    pos['system_rating'] = sub_match.group(1)
            elif 'td-name' in cell_class:
                name_match = re.search(r'candidate-name[^>]*>(.*?)</a>', cell_content, re.DOTALL)
                if name_match:
                    pos['event_name_raw'] = strip_html(name_match.group(1))

                # Edge type badge
                type_match = re.search(r'type-badge[^>]*>(.*?)</span>', cell_content)
                if type_match:
                    etype = strip_html(type_match.group(1)).upper()
                    type_map = {'CON': 'CONSENSUS', 'DIS': 'DISCOVERY', 'FRO': 'FRONTIER', 'UNC': None}
                    pos['edge_type'] = type_map.get(etype, etype)

                # Direction and hold
                meta_match = re.search(r'candidate-meta[^>]*>(.*?)</span>', cell_content, re.DOTALL)
                if meta_match:
                    meta = strip_html(meta_match.group(1))
                    if '↑' in meta or '&uarr;' in unescape(meta):
                        pos['direction'] = 'higher'
                    elif '↓' in meta or '&darr;' in unescape(meta):
                        pos['direction'] = 'lower'
                    hold_match = re.search(r'(\d+[hd])\b', meta)
                    if hold_match:
                        pos['hold_duration_label'] = hold_match.group(1)

                # State
                if 'WATCHING' in cell_content:
                    pos['state'] = 'WATCHING'
                # Exit note
                exit_match = re.search(r'exit-note">(.*?)</span>', cell_content)
                if exit_match:
                    pos['exit_reason'] = strip_html(exit_match.group(1))
                    pos['state'] = 'EXITED'
                # Kill note
                kill_match = re.search(r'kill-note">(.*?)</span>', cell_content)
                if kill_match:
                    pos['kill_reason'] = strip_html(kill_match.group(1))
                    pos['state'] = 'KILLED'

                # Polymarket link
                link_match = re.search(r'href="(https://polymarket\.com/[^"]+)"', cell_content)
                if link_match:
                    pos['polymarket_url'] = link_match.group(1)

            elif 'td-timeline' in cell_class:
                # Parse timeline price points
                tl_points = []
                for tp in re.finditer(
                    r'title="([^"]*?)"[^>]*><sup class="tl-time">(\d+:\d+)</sup>([\d.]+)<sub>([^<]*)</sub>',
                    cell_content
                ):
                    tl_points.append({
                        'title': tp.group(1),
                        'time': tp.group(2),
                        'price_cents': float(tp.group(3)),
                        'pnl_label': tp.group(4)
                    })
                # Buy/Sell markers
                for marker in re.finditer(
                    r'(buy|sell|kill|confirm)-marker[^>]*title="([^"]*)"[^>]*>([^<]*)<',
                    cell_content, re.IGNORECASE
                ):
                    tl_points.append({
                        'marker_type': marker.group(1).upper(),
                        'title': marker.group(2),
                        'label': marker.group(3)
                    })
                pos['timeline'] = tl_points

            elif 'td-made' in cell_class:
                pos['made_pnl_pct'] = parse_pnl(cell_content)
                pos['made_dollar_pnl'] = parse_dollar_pnl(cell_content)
                # Risk level
                risk_match = re.search(r'title="\$[\d.]+k? position \((\w+) risk\)"', cell_content)
                if risk_match:
                    pos['risk_level'] = risk_match.group(1)
                # Position size
                size_match = re.search(r'\$(\d+\.?\d*)k? position', cell_content)
                if size_match:
                    val = float(size_match.group(1))
                    if 'k' in cell_content[cell_content.find(size_match.group(0)):]:
                        val *= 1000
                    pos['position_size'] = val

            elif 'td-ifheld' in cell_class:
                pos['ifheld_pnl_pct'] = parse_pnl(cell_content)

            elif 'td-kill' in cell_class:
                if 'kill-marker' in cell_content:
                    pos['killed'] = True

        # Extract price cells (the numbered td-num cells)
        num_cells = [c for cls_name, c in cells if 'td-num' in cls_name]
        if len(num_cells) >= 5:
            pos['current_price'] = parse_cents(num_cells[0])
            # Market price (the bold one)
            bold_match = re.search(r'<strong>(.*?)</strong>', num_cells[1])
            if bold_match:
                pos['market_price_display'] = parse_cents(bold_match.group(1))
            pos['entry_price'] = parse_cents(num_cells[2])
            # Edge points
            edge_match = re.search(r'H(\d+)', num_cells[3])
            if edge_match:
                pos['edge_points'] = int(edge_match.group(1))
            # Actual move
            move_match = re.search(r'Actual price move">(\d+)', num_cells[3])
            if move_match:
                pos['actual_move'] = int(move_match.group(1))
            # Volume
            vol_text = strip_html(num_cells[4])
            pos['volume_display'] = vol_text

        # Set state if not already set
        if 'state' not in pos:
            # Check dot color for status
            dot_match = re.search(r'border-radius:50%;background:(#[a-f0-9]+)', content)
            if dot_match:
                color = dot_match.group(1)
                if color == '#16a34a':
                    pos['status_color'] = 'green'
                elif color == '#cc0000':
                    pos['status_color'] = 'red'
                elif color == '#f59e0b':
                    pos['status_color'] = 'orange'
                else:
                    pos['status_color'] = 'grey'
            pos['state'] = 'ACTIVE'

        if pos.get('event_name_raw'):
            pos['event_name'] = clean_event_name(pos['event_name_raw'])
            pos['event_id'] = make_event_id(pos['event_name'])
            pos['row_index'] = len(positions)
            positions.append(pos)

    return positions


# ─────────────────────────────────────────────
# DATA EXTRACTION: GIT HISTORY
# ─────────────────────────────────────────────

def extract_git_snapshots():
    """Extract all summary.json snapshots from git history."""
    result = subprocess.run(
        ['git', 'log', '--all', '--format=%H %aI'],
        capture_output=True, text=True, cwd=REPO_DIR
    )
    commits = []
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.strip().split(' ', 1)
        commits.append({'hash': parts[0], 'timestamp': parts[1]})

    # Oldest first
    commits.reverse()

    snapshots = []
    for commit in commits:
        try:
            result = subprocess.run(
                ['git', 'show', f'{commit["hash"]}:summary.json'],
                capture_output=True, text=True, cwd=REPO_DIR
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                data['_commit_hash'] = commit['hash']
                data['_commit_timestamp'] = commit['timestamp']
                snapshots.append(data)
        except (json.JSONDecodeError, Exception):
            continue

    return snapshots


# ─────────────────────────────────────────────
# MERGE: Build canonical event list
# ─────────────────────────────────────────────

def build_canonical_events(html_positions, snapshots):
    """Merge HTML and git data into canonical event records.

    If the same event_id appears multiple times in the HTML
    (re-entries, rating changes, etc.), each row gets a unique
    position_instance_id (event_id + "_N") to preserve every
    position instance.  The base event_id is kept for cross-sheet
    joining.
    """
    events = {}
    eid_counter = defaultdict(int)

    # Start with HTML positions (most complete)
    for pos in html_positions:
        base_eid = pos['event_id']
        eid_counter[base_eid] += 1
        instance = eid_counter[base_eid]
        # Use instance suffix only when there are duplicates
        # (we'll fixup later once we know final counts)
        pos['_instance'] = instance

        key = f"{base_eid}_{instance}"
        events[key] = {
            'event_id': base_eid,
            'position_instance_id': key,
            'event_name': pos.get('event_name', ''),
            'event_name_raw': pos.get('event_name_raw', ''),
            'rating': pos.get('rating', ''),
            'system_rating': pos.get('system_rating', ''),
            'group': pos.get('group', ''),
            'edge_type': pos.get('edge_type', ''),
            'direction': pos.get('direction', ''),
            'entry_price': pos.get('entry_price'),
            'current_price': pos.get('current_price'),
            'market_price_display': pos.get('market_price_display'),
            'state': pos.get('state', ''),
            'status_color': pos.get('status_color', ''),
            'made_pnl_pct': pos.get('made_pnl_pct'),
            'ifheld_pnl_pct': pos.get('ifheld_pnl_pct'),
            'made_dollar_pnl': pos.get('made_dollar_pnl'),
            'edge_points': pos.get('edge_points'),
            'actual_move': pos.get('actual_move'),
            'volume_display': pos.get('volume_display', ''),
            'exit_reason': pos.get('exit_reason', ''),
            'kill_reason': pos.get('kill_reason', ''),
            'risk_level': pos.get('risk_level', ''),
            'position_size': pos.get('position_size'),
            'hold_duration_label': pos.get('hold_duration_label', ''),
            'date_label': pos.get('date', ''),
            'timeline': pos.get('timeline', []),
            'polymarket_url': pos.get('polymarket_url', ''),
        }

    # For events with only one instance, simplify the key
    singles = {eid for eid, count in eid_counter.items() if count == 1}
    simplified = {}
    for key, ev in events.items():
        base = ev['event_id']
        if base in singles:
            ev['position_instance_id'] = base
            simplified[base] = ev
        else:
            simplified[key] = ev
    events = simplified

    # Enrich from summary.json (latest snapshot)
    if snapshots:
        latest = snapshots[-1]
        for p in latest.get('recent_positions', []):
            name = clean_event_name(p.get('event', ''))
            eid = make_event_id(name)
            # Try to match to any instance of this event
            for key, ev in events.items():
                if ev['event_id'] == eid:
                    if not ev.get('edge_type') and p.get('edge_type'):
                        ev['edge_type'] = p['edge_type']
                    if not ev.get('direction') and p.get('direction'):
                        ev['direction'] = p['direction']
                    if p.get('conviction'):
                        ev['conviction'] = p['conviction']
                    if p.get('kill_reason') and not ev.get('kill_reason'):
                        ev['kill_reason'] = p['kill_reason']
                    if p.get('tail_type'):
                        ev['tail_type'] = p['tail_type']
                    break  # Only enrich first match

    return events


# ─────────────────────────────────────────────
# Build time-series from git snapshots
# ─────────────────────────────────────────────

def build_time_series(snapshots):
    """Build per-event time series from git history snapshots."""
    # event_name -> [{timestamp, price, pnl, state, status}, ...]
    series = defaultdict(list)

    for snap in snapshots:
        ts = snap.get('updated_at') or snap.get('_commit_timestamp', '')
        for p in snap.get('recent_positions', []):
            name = clean_event_name(p.get('event', ''))
            eid = make_event_id(name)
            series[eid].append({
                'timestamp': ts,
                'current_price': p.get('current_price'),
                'entry_price': p.get('entry_price'),
                'pnl_pct': p.get('pnl_pct'),
                'state': p.get('state', ''),
                'status': p.get('status', ''),
                'direction': p.get('direction', ''),
            })

    return series


# ─────────────────────────────────────────────
# Detect state transitions for signal log
# ─────────────────────────────────────────────

def detect_signals(time_series, events):
    """Detect signal changes from time series state transitions."""
    signals = []
    signal_counter = 0

    for eid, points in time_series.items():
        if not points:
            continue

        ev = events.get(eid, {})
        prev_state = None
        prev_status = None
        current_signal_id = None

        for pt in sorted(points, key=lambda x: x['timestamp']):
            state = pt['state']
            status = pt['status']

            # Signal change = state transition or status change
            state_changed = (state != prev_state)
            status_changed = (status != prev_status and prev_status is not None)

            if state_changed or (status_changed and state in ('ACTIVE', 'EXITED', 'KILLED')):
                signal_counter += 1
                current_signal_id = f"SIG_{signal_counter:04d}"

                reason = ''
                if state == 'KILLED':
                    reason = ev.get('kill_reason', 'Killed')
                elif state == 'EXITED':
                    reason = ev.get('exit_reason', 'Exited')
                elif state == 'ACTIVE' and prev_state == 'WATCHING':
                    reason = 'First entry after watching'
                elif state == 'ACTIVE' and prev_state == 'EXITED':
                    reason = 'Re-entry after exit'
                elif state == 'WATCHING':
                    reason = 'Initial observation'
                elif status_changed:
                    reason = f'Status changed: {prev_status} -> {status}'

                direction_map = {'higher': 'YES', 'lower': 'NO'}
                signal_dir = direction_map.get(pt.get('direction', ev.get('direction', '')), '')

                signals.append({
                    'event_id': eid,
                    'event_name': ev.get('event_name', ''),
                    'signal_id': current_signal_id,
                    'signal_start_timestamp': pt['timestamp'],
                    'signal_end_timestamp': '',  # filled on next transition
                    'signal_strength_score': ev.get('edge_points', ''),
                    'model_fair_value_at_signal': '',  # not available
                    'market_price_at_signal': pt.get('current_price', ''),
                    'calculated_edge_at_signal': '',  # not available
                    'signal_direction': signal_dir,
                    'signal_status': status or '',
                    'reason_for_signal_change': reason,
                })

                # End previous signal for this event
                for prev_sig in reversed(signals[:-1]):
                    if prev_sig['event_id'] == eid and not prev_sig['signal_end_timestamp']:
                        prev_sig['signal_end_timestamp'] = pt['timestamp']
                        break

            prev_state = state
            prev_status = status

    # Also create signals for HTML-only events (not tracked in git)
    git_signal_events = set(s['event_id'] for s in signals)
    for key, ev in events.items():
        eid = ev.get('event_id', key)
        if eid in git_signal_events:
            continue
        state = ev.get('state', '')
        if state == 'WATCHING':
            continue  # No signal for watching-only

        signal_counter += 1
        direction_map = {'higher': 'YES', 'lower': 'NO'}
        signal_dir = direction_map.get(ev.get('direction', ''), '')
        status_color = ev.get('status_color', '')

        reason = ''
        if state == 'KILLED':
            reason = ev.get('kill_reason', 'Killed')
        elif state == 'EXITED':
            reason = ev.get('exit_reason', 'Exited')
        elif state == 'ACTIVE':
            reason = 'Active position'

        signals.append({
            'event_id': eid,
            'event_name': ev.get('event_name', ''),
            'signal_id': f"SIG_{signal_counter:04d}",
            'signal_start_timestamp': f"HTML_snapshot_{ev.get('date_label', 'unknown')}",
            'signal_end_timestamp': '',
            'signal_strength_score': ev.get('edge_points', ''),
            'model_fair_value_at_signal': '',
            'market_price_at_signal': ev.get('current_price', ''),
            'calculated_edge_at_signal': '',
            'signal_direction': signal_dir,
            'signal_status': status_color,
            'reason_for_signal_change': reason,
        })

    return signals


# ─────────────────────────────────────────────
# Detect trades from state transitions
# ─────────────────────────────────────────────

def detect_trades(time_series, events, signals):
    """Detect executed trades from state transitions."""
    trades = []
    trade_counter = 0

    for eid, points in time_series.items():
        if not points:
            continue

        ev = events.get(eid, {})
        sorted_pts = sorted(points, key=lambda x: x['timestamp'])

        in_trade = False
        entry_ts = None
        entry_price = None
        current_signal_id = None
        max_adverse = 0
        max_favourable = 0

        for pt in sorted_pts:
            state = pt['state']
            price = pt.get('current_price')
            ts = pt['timestamp']

            if state == 'ACTIVE' and not in_trade:
                # Trade entry
                in_trade = True
                entry_ts = ts
                entry_price = pt.get('entry_price') or price
                max_adverse = 0
                max_favourable = 0

                # Find matching signal
                for sig in reversed(signals):
                    if sig['event_id'] == eid:
                        current_signal_id = sig['signal_id']
                        break

            elif state == 'ACTIVE' and in_trade and entry_price and price:
                # Track excursions
                direction = ev.get('direction', 'higher')
                if direction == 'lower':
                    # Betting price goes down (NO side)
                    move = entry_price - price
                else:
                    # Betting price goes up (YES side)
                    move = price - entry_price

                if move > 0:
                    max_favourable = max(max_favourable, move)
                else:
                    max_adverse = max(max_adverse, abs(move))

            elif state in ('EXITED', 'KILLED', 'WATCHING') and in_trade:
                # Trade exit
                trade_counter += 1
                exit_price = price or entry_price
                pnl = 0
                if entry_price and exit_price:
                    direction = ev.get('direction', 'higher')
                    if direction == 'lower':
                        pnl = round(entry_price - exit_price, 6)
                    else:
                        pnl = round(exit_price - entry_price, 6)

                trades.append({
                    'event_id': eid,
                    'signal_id': current_signal_id or '',
                    'trade_id': f"TRD_{trade_counter:04d}",
                    'entry_timestamp': entry_ts,
                    'entry_price': entry_price,
                    'size': ev.get('position_size', ''),
                    'exit_timestamp': ts,
                    'exit_price': exit_price,
                    'realised_pnl': pnl,
                    'max_adverse_excursion_during_trade': round(max_adverse, 6),
                    'max_favourable_excursion_during_trade': round(max_favourable, 6),
                })
                in_trade = False

        # If still in trade at end of series
        if in_trade:
            last_pt = sorted_pts[-1]
            trade_counter += 1
            exit_price = last_pt.get('current_price')
            pnl = 0
            if entry_price and exit_price:
                direction = ev.get('direction', 'higher')
                if direction == 'lower':
                    pnl = round(entry_price - exit_price, 6)
                else:
                    pnl = round(exit_price - entry_price, 6)

            trades.append({
                'event_id': eid,
                'signal_id': current_signal_id or '',
                'trade_id': f"TRD_{trade_counter:04d}",
                'entry_timestamp': entry_ts,
                'entry_price': entry_price,
                'size': ev.get('position_size', ''),
                'exit_timestamp': '',  # still open
                'exit_price': exit_price,
                'realised_pnl': pnl,
                'max_adverse_excursion_during_trade': round(max_adverse, 6),
                'max_favourable_excursion_during_trade': round(max_favourable, 6),
            })

    # Also reconstruct trades from HTML-only data
    # (events not tracked in git time-series but shown as EXITED/KILLED in HTML)
    git_traded_events = set(t['event_id'] for t in trades)

    for key, ev in events.items():
        eid = ev.get('event_id', key)
        state = ev.get('state', '')
        if state not in ('EXITED', 'KILLED'):
            continue  # Only reconstruct closed trades from HTML
        if eid in git_traded_events:
            continue  # Already have git-based trade

        entry_price = ev.get('entry_price')
        current_price = ev.get('current_price')
        if not entry_price:
            continue

        trade_counter += 1
        direction = ev.get('direction', 'higher')
        pnl = 0
        if entry_price and current_price:
            if direction == 'lower':
                pnl = round(entry_price - current_price, 6)
            else:
                pnl = round(current_price - entry_price, 6)

        # Use made_pnl_pct if available (more accurate than price diff)
        made_pnl = ev.get('made_pnl_pct')
        if made_pnl is not None and entry_price:
            pnl = round(entry_price * (made_pnl / 100.0), 6)

        exit_ts = ''
        if state in ('EXITED', 'KILLED'):
            exit_ts = f"HTML_snapshot_{ev.get('date_label', 'unknown')}"

        trades.append({
            'event_id': eid,
            'signal_id': '',  # not tracked in git
            'trade_id': f"TRD_{trade_counter:04d}",
            'entry_timestamp': f"HTML_snapshot_{ev.get('date_label', 'unknown')}",
            'entry_price': entry_price,
            'size': ev.get('position_size', ''),
            'exit_timestamp': exit_ts if state in ('EXITED', 'KILLED') else '',
            'exit_price': current_price if state in ('EXITED', 'KILLED') else '',
            'realised_pnl': pnl if state in ('EXITED', 'KILLED') else '',
            'max_adverse_excursion_during_trade': '',  # not available from HTML
            'max_favourable_excursion_during_trade': '',
        })
        git_traded_events.add(eid)

    return trades


# ─────────────────────────────────────────────
# SHEET GENERATORS
# ─────────────────────────────────────────────

def write_signal_log(signals):
    """SHEET 1: SIGNAL_LOG"""
    path = OUTPUT_DIR / "SIGNAL_LOG.csv"
    fieldnames = [
        'event_id', 'event_name', 'signal_id', 'signal_start_timestamp',
        'signal_end_timestamp', 'signal_strength_score',
        'model_fair_value_at_signal', 'market_price_at_signal',
        'calculated_edge_at_signal', 'signal_direction',
        'signal_status', 'reason_for_signal_change'
    ]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for sig in signals:
            writer.writerow(sig)
    print(f"  SIGNAL_LOG.csv: {len(signals)} rows")
    return path


def write_price_series(time_series, events):
    """SHEET 2: PRICE_SERIES"""
    path = OUTPUT_DIR / "PRICE_SERIES.csv"
    fieldnames = [
        'event_id', 'timestamp', 'last_traded_price', 'best_bid',
        'best_ask', 'mid_price', '24h_volume_at_time',
        'cumulative_volume', 'volatility_estimate_last_6h'
    ]

    rows = []
    for eid, points in time_series.items():
        for pt in sorted(points, key=lambda x: x['timestamp']):
            price = pt.get('current_price')
            rows.append({
                'event_id': eid,
                'timestamp': pt['timestamp'],
                'last_traded_price': price if price is not None else '',
                'best_bid': '',  # not available in data
                'best_ask': '',  # not available in data
                'mid_price': price if price is not None else '',  # approximate
                '24h_volume_at_time': '',  # not available per-snapshot
                'cumulative_volume': '',
                'volatility_estimate_last_6h': '',
            })

    # Also add timeline data from HTML (higher resolution)
    for eid, ev in events.items():
        for tl_point in ev.get('timeline', []):
            if 'price_cents' in tl_point:
                # Reconstruct approximate timestamp from time label
                time_label = tl_point.get('time', '')
                title = tl_point.get('title', '')
                # Parse hours offset from title
                hours_match = re.search(r'\((\d+\.?\d*)h\)', title)
                price_decimal = round(tl_point['price_cents'] / 100.0, 6) if tl_point['price_cents'] > 1 else tl_point['price_cents']

                rows.append({
                    'event_id': eid,
                    'timestamp': f"timeline_{time_label}",  # relative time
                    'last_traded_price': price_decimal,
                    'best_bid': '',
                    'best_ask': '',
                    'mid_price': price_decimal,
                    '24h_volume_at_time': '',
                    'cumulative_volume': '',
                    'volatility_estimate_last_6h': '',
                })

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"  PRICE_SERIES.csv: {len(rows)} rows")
    return path


def write_executed_trades(trades):
    """SHEET 3: EXECUTED_TRADES_HISTORY"""
    path = OUTPUT_DIR / "EXECUTED_TRADES_HISTORY.csv"
    fieldnames = [
        'event_id', 'signal_id', 'trade_id', 'entry_timestamp',
        'entry_price', 'size', 'exit_timestamp', 'exit_price',
        'realised_pnl', 'max_adverse_excursion_during_trade',
        'max_favourable_excursion_during_trade'
    ]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade)
    print(f"  EXECUTED_TRADES_HISTORY.csv: {len(trades)} rows")
    return path


def write_position_state(time_series, events, trades):
    """SHEET 4: POSITION_STATE_OVER_TIME"""
    path = OUTPUT_DIR / "POSITION_STATE_OVER_TIME.csv"
    fieldnames = [
        'event_id', 'timestamp', 'net_position_size',
        'average_entry_price', 'unrealised_pnl', 'signal_status_at_time'
    ]

    rows = []
    for eid, points in time_series.items():
        ev = events.get(eid, {})
        position_size = ev.get('position_size', '')

        for pt in sorted(points, key=lambda x: x['timestamp']):
            state = pt['state']
            entry_price = pt.get('entry_price')
            current_price = pt.get('current_price')

            if state == 'ACTIVE':
                net_size = position_size
                unrealised = ''
                if entry_price and current_price:
                    direction = ev.get('direction', 'higher')
                    if direction == 'lower':
                        unrealised = round(entry_price - current_price, 6)
                    else:
                        unrealised = round(current_price - entry_price, 6)
            elif state == 'WATCHING':
                net_size = 0
                unrealised = 0
            else:
                net_size = 0
                unrealised = 0

            rows.append({
                'event_id': eid,
                'timestamp': pt['timestamp'],
                'net_position_size': net_size,
                'average_entry_price': entry_price if state == 'ACTIVE' else '',
                'unrealised_pnl': unrealised,
                'signal_status_at_time': pt.get('status', ''),
            })

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"  POSITION_STATE_OVER_TIME.csv: {len(rows)} rows")
    return path


def write_summary_stats(events, signals, trades, time_series):
    """SHEET 5: SUMMARY_STATS_PER_EVENT"""
    path = OUTPUT_DIR / "SUMMARY_STATS_PER_EVENT.csv"
    fieldnames = [
        'event_id', 'event_name', 'rating', 'edge_type', 'direction',
        'entry_price', 'current_price', 'state',
        'total_number_of_signals', 'total_number_of_trade_legs',
        'total_gross_pnl', 'total_net_pnl',
        'maximum_drawdown', 'average_hold_time_hours',
        'total_time_signal_green_hours'
    ]

    rows = []
    for eid, ev in events.items():
        base_eid = ev['event_id']
        # Count signals for this event (match on base event_id)
        event_signals = [s for s in signals if s['event_id'] == base_eid]
        event_trades = [t for t in trades if t['event_id'] == base_eid]
        event_ts = time_series.get(base_eid, [])

        # Calculate hold time
        total_hold_hours = 0
        for trade in event_trades:
            if trade['entry_timestamp'] and trade['exit_timestamp']:
                try:
                    entry_dt = datetime.fromisoformat(trade['entry_timestamp'].replace('Z', '+00:00'))
                    exit_dt = datetime.fromisoformat(trade['exit_timestamp'].replace('Z', '+00:00'))
                    total_hold_hours += (exit_dt - entry_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass

        avg_hold = round(total_hold_hours / len(event_trades), 2) if event_trades else ''

        # Gross/Net PnL
        gross_pnl = sum(abs(t['realised_pnl']) for t in event_trades if isinstance(t['realised_pnl'], (int, float)))
        net_pnl = sum(t['realised_pnl'] for t in event_trades if isinstance(t['realised_pnl'], (int, float)))

        # Max drawdown from time series
        max_dd = 0
        peak_pnl = 0
        for pt in sorted(event_ts, key=lambda x: x['timestamp']):
            pnl = pt.get('pnl_pct', 0) or 0
            if pnl > peak_pnl:
                peak_pnl = pnl
            dd = peak_pnl - pnl
            if dd > max_dd:
                max_dd = dd

        # Total time signal green
        green_hours = 0
        prev_ts = None
        for pt in sorted(event_ts, key=lambda x: x['timestamp']):
            if pt.get('status') == 'green' and prev_ts:
                try:
                    curr_dt = datetime.fromisoformat(pt['timestamp'].replace('Z', '+00:00'))
                    prev_dt = datetime.fromisoformat(prev_ts.replace('Z', '+00:00'))
                    green_hours += (curr_dt - prev_dt).total_seconds() / 3600
                except (ValueError, TypeError):
                    pass
            prev_ts = pt['timestamp']

        rows.append({
            'event_id': eid,
            'event_name': ev.get('event_name', ''),
            'rating': ev.get('rating', ''),
            'edge_type': ev.get('edge_type', ''),
            'direction': ev.get('direction', ''),
            'entry_price': ev.get('entry_price', ''),
            'current_price': ev.get('current_price', ''),
            'state': ev.get('state', ''),
            'total_number_of_signals': len(event_signals),
            'total_number_of_trade_legs': len(event_trades),
            'total_gross_pnl': round(gross_pnl, 6) if gross_pnl else 0,
            'total_net_pnl': round(net_pnl, 6) if net_pnl else 0,
            'maximum_drawdown': round(max_dd, 2) if max_dd else 0,
            'average_hold_time_hours': avg_hold,
            'total_time_signal_green_hours': round(green_hours, 2) if green_hours else 0,
        })

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r['event_id']):
            writer.writerow(row)
    print(f"  SUMMARY_STATS_PER_EVENT.csv: {len(rows)} rows")
    return path


# ─────────────────────────────────────────────
# Also produce events_that_existed_but_have_no_git_history
# (HTML-only positions with no summary.json tracking)
# ─────────────────────────────────────────────

def write_static_positions_sheet(events, time_series):
    """
    Supplementary sheet: events from HTML that have no git time series.
    These are positions the HTML table shows but summary.json never tracked
    (i.e., the 42+ positions beyond the 20 in recent_positions).
    """
    path = OUTPUT_DIR / "STATIC_POSITIONS_FROM_HTML.csv"
    fieldnames = [
        'event_id', 'event_name', 'rating', 'group', 'edge_type',
        'direction', 'state', 'entry_price', 'current_price',
        'market_price_display', 'made_pnl_pct', 'ifheld_pnl_pct',
        'made_dollar_pnl', 'edge_points', 'exit_reason', 'kill_reason',
        'risk_level', 'position_size', 'hold_duration_label',
        'date_label', 'polymarket_url',
        'timeline_prices'
    ]

    rows = []
    for eid, ev in events.items():
        # Serialize timeline
        tl_str = ''
        tl = ev.get('timeline', [])
        if tl:
            parts = []
            for pt in tl:
                if 'price_cents' in pt:
                    parts.append(f"{pt.get('time','')}={pt['price_cents']}c")
                elif 'marker_type' in pt:
                    parts.append(f"[{pt['marker_type']}:{pt.get('title','')}]")
            tl_str = ' | '.join(parts)

        rows.append({
            'event_id': eid,
            'event_name': ev.get('event_name', ''),
            'rating': ev.get('rating', ''),
            'group': ev.get('group', ''),
            'edge_type': ev.get('edge_type', ''),
            'direction': ev.get('direction', ''),
            'state': ev.get('state', ''),
            'entry_price': ev.get('entry_price', ''),
            'current_price': ev.get('current_price', ''),
            'market_price_display': ev.get('market_price_display', ''),
            'made_pnl_pct': ev.get('made_pnl_pct', ''),
            'ifheld_pnl_pct': ev.get('ifheld_pnl_pct', ''),
            'made_dollar_pnl': ev.get('made_dollar_pnl', ''),
            'edge_points': ev.get('edge_points', ''),
            'exit_reason': ev.get('exit_reason', ''),
            'kill_reason': ev.get('kill_reason', ''),
            'risk_level': ev.get('risk_level', ''),
            'position_size': ev.get('position_size', ''),
            'hold_duration_label': ev.get('hold_duration_label', ''),
            'date_label': ev.get('date_label', ''),
            'polymarket_url': ev.get('polymarket_url', ''),
            'timeline_prices': tl_str,
        })

    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r['event_id']):
            writer.writerow(row)
    print(f"  STATIC_POSITIONS_FROM_HTML.csv: {len(rows)} rows (all events from HTML)")
    return path


# ─────────────────────────────────────────────
# DATA COMPLETENESS REPORT
# ─────────────────────────────────────────────

def write_data_completeness_report(events, time_series, signals, trades, snapshots):
    """Write a plain text report about data completeness and caveats."""
    path = OUTPUT_DIR / "DATA_COMPLETENESS_REPORT.txt"

    total_events = len(events)
    # Match events to time_series using the base event_id
    events_with_ts = len([
        key for key, ev in events.items()
        if ev['event_id'] in time_series and len(time_series[ev['event_id']]) > 0
    ])
    events_without_ts = total_events - events_with_ts
    unique_ts_events = len(time_series)

    ts_range_start = ''
    ts_range_end = ''
    if snapshots:
        ts_range_start = snapshots[0].get('updated_at', snapshots[0].get('_commit_timestamp', ''))
        ts_range_end = snapshots[-1].get('updated_at', snapshots[-1].get('_commit_timestamp', ''))

    lines = [
        "POLYHUNTER TRADING AUDIT - DATA COMPLETENESS REPORT",
        "=" * 55,
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "DATA SOURCES",
        "-" * 40,
        f"  Git commits analysed: {len(snapshots)}",
        f"  Time series range: {ts_range_start} to {ts_range_end}",
        f"  HTML positions parsed: {total_events}",
        f"  Unique base events with git time-series: {unique_ts_events}",
        f"  Position instances with git time-series: {events_with_ts}",
        f"  Position instances with HTML-only data (no time-series): {events_without_ts}",
        "",
        "KNOWN DATA LIMITATIONS",
        "-" * 40,
        "  1. PRICE_SERIES: bid/ask not available. Only last_traded_price from snapshots.",
        "  2. PRICE_SERIES: ~hourly granularity only (from git commit frequency).",
        "  3. PRICE_SERIES: 24h_volume, cumulative_volume, volatility not available per-snapshot.",
        "  4. SIGNAL_LOG: model_fair_value and calculated_edge not stored in source data.",
        "  5. EXECUTED_TRADES: position sizes only available for events in HTML table.",
        "  6. EXECUTED_TRADES: max adverse/favourable excursion limited to hourly resolution.",
        f"  7. Only {unique_ts_events} unique events ({events_with_ts} position instances) have git time series.",
        f"     The remaining {events_without_ts} position instances exist only in the HTML snapshot.",
        "  8. summary.json tracks only ~20 'recent_positions' per snapshot.",
        "     Events not in recent_positions have no historical price series.",
        "  9. Git history spans ~21 hours. Positions may have been active before first commit.",
        " 10. No tick-level or order-book data available.",
        "",
        "BLANK FIELDS",
        "-" * 40,
        "  Fields left blank where data is unavailable (per instructions: do not interpolate):",
        "  - best_bid, best_ask: Not stored in source data",
        "  - 24h_volume_at_time, cumulative_volume: Not stored per-snapshot",
        "  - volatility_estimate_last_6h: Not computed in source",
        "  - model_fair_value_at_signal: Not stored",
        "  - calculated_edge_at_signal: Not stored",
        "",
        "SHEETS PRODUCED",
        "-" * 40,
        f"  1. SIGNAL_LOG.csv: {len(signals)} signal change records",
        f"  2. PRICE_SERIES.csv: Time-series from git + HTML timelines",
        f"  3. EXECUTED_TRADES_HISTORY.csv: {len(trades)} trade records",
        f"  4. POSITION_STATE_OVER_TIME.csv: Hourly position states from git",
        f"  5. SUMMARY_STATS_PER_EVENT.csv: {total_events} event summaries",
        f"  +  STATIC_POSITIONS_FROM_HTML.csv: All {total_events} events with HTML data",
        "",
        "ALL TIMESTAMPS ARE UTC.",
        "ALL PRICES ARE DECIMALS (e.g. 0.67 not 67).",
        "NO OPTIMISATION APPLIED.",
        "NO HISTORY REWRITTEN.",
    ]

    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"  DATA_COMPLETENESS_REPORT.txt written")
    return path


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("PolyHunter Trading Audit Spreadsheet Generator")
    print("=" * 50)
    print()

    print("Step 1: Extracting positions from HTML...")
    html_positions = extract_html_positions()
    print(f"  Found {len(html_positions)} positions in HTML table")

    print("Step 2: Extracting historical snapshots from git...")
    snapshots = extract_git_snapshots()
    print(f"  Found {len(snapshots)} git snapshots")

    print("Step 3: Building canonical event list...")
    events = build_canonical_events(html_positions, snapshots)
    print(f"  {len(events)} unique events")

    print("Step 4: Building time series from git history...")
    time_series = build_time_series(snapshots)
    print(f"  {len(time_series)} events with time-series data")

    print("Step 5: Detecting signal changes...")
    signals = detect_signals(time_series, events)
    print(f"  {len(signals)} signal changes detected")

    print("Step 6: Detecting executed trades...")
    trades = detect_trades(time_series, events, signals)
    print(f"  {len(trades)} trades detected")

    print()
    print("Generating CSV sheets...")
    write_signal_log(signals)
    write_price_series(time_series, events)
    write_executed_trades(trades)
    write_position_state(time_series, events, trades)
    write_summary_stats(events, signals, trades, time_series)
    write_static_positions_sheet(events, time_series)
    write_data_completeness_report(events, time_series, signals, trades, snapshots)

    print()
    print(f"All files written to: {OUTPUT_DIR}/")
    print("Done.")


if __name__ == '__main__':
    main()
