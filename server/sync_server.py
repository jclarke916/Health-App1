"""Centurion sync server - keeps both phones' data in step. Stdlib only.

    pythonw server/sync_server.py --data C:/Users/<you>/CenturionData [--port 8792]

Listens on 127.0.0.1 only; phones reach it through `tailscale serve`, so it is
private to the tailnet. The health data lives in <data>/state.json on this PC
and nowhere else.

Model: the app's localStorage is a set of JSON objects (`c_meals_sophia` =
{date: {...}}, `c_bios_jermaine` = {field: value}, ...). Sync works on
"entries": one (storage key, first-level property) pair. Every accepted write
gets the next revision number; a client sends its changed entries plus the
last revision it saw and gets back everything newer. Last write wins per entry.

First sync from a device (`first: true`) is special so that two phones with
months of separate history don't trample each other: the device is trusted for
its owner's keys (`..._<owner>`), and for everyone else's keys whatever the
server already has wins.

POST /sync  {client, since, serverId, first, owner, changes:[{k, s, v, d}]}
         -> {serverId, rev, entries:[{k, s, v, d}]}
POST /apple?user=<name>   Apple Watch / Health data pushed from the phone (an
         iOS Shortcut or the Health Auto Export app - the web app cannot read
         HealthKit itself). Stored as c_apple_<user> / <date>, so it reaches
         both phones through the normal sync. See parse_apple() for formats.
GET  /health
"""
import argparse
import datetime
import json
import logging
import os
import shutil
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOWED_ORIGINS = {
    'https://jclarke916.github.io',
    'http://localhost:8765',   # local dev server
    'http://127.0.0.1:8765',   # second "device" when testing sync locally
}
MAX_BODY = 8 * 1024 * 1024
SEP = '\x00'

log = logging.getLogger('centurion-sync')


class Store:
    def __init__(self, data_dir):
        self.dir = data_dir
        self.path = os.path.join(data_dir, 'state.json')
        self.backups = os.path.join(data_dir, 'backups')
        os.makedirs(self.backups, exist_ok=True)
        self.lock = threading.Lock()
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as f:
                self.state = json.load(f)
        else:
            self.state = {'serverId': uuid.uuid4().hex, 'rev': 0, 'entries': {}}
            self._write()

    def _write(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def _daily_backup(self):
        """Copy of the state as it stood before today's first write; keep 60."""
        dest = os.path.join(self.backups, 'state-%s.json' % time.strftime('%Y-%m-%d'))
        if not os.path.exists(dest) and os.path.exists(self.path):
            shutil.copy2(self.path, dest)
            for old in sorted(os.listdir(self.backups))[:-60]:
                os.remove(os.path.join(self.backups, old))

    def apple(self, user, days):
        """Merge pushed watch data; later pushes for a day update fields, workouts dedupe by start."""
        changed = 0
        with self.lock:
            entries = self.state['entries']
            for date, vals in days.items():
                eid = 'c_apple_%s%s%s' % (user, SEP, date)
                cur = entries.get(eid)
                old = dict(cur['v']) if cur and not cur.get('d') and isinstance(cur.get('v'), dict) else {}
                new = dict(old)
                for field, val in vals.items():
                    if field == 'workouts':
                        seen = {w.get('start'): w for w in old.get('workouts', [])}
                        seen.update({w.get('start'): w for w in val if isinstance(w, dict)})
                        new['workouts'] = sorted(seen.values(), key=lambda w: str(w.get('start')))
                    elif val is not None:
                        new[field] = val
                if new == old:
                    continue
                self.state['rev'] += 1
                entries[eid] = {'v': new, 'd': False, 'rev': self.state['rev'], 'by': 'apple-push',
                                'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
                changed += 1
            if changed:
                self._daily_backup()
                self._write()
        return changed

    def sync(self, req):
        client = str(req.get('client') or '?')[:64]
        owner = str(req.get('owner') or '')
        first = bool(req.get('first')) or req.get('serverId') != self.state['serverId']
        since = 0 if first else int(req.get('since') or 0)
        with self.lock:
            entries = self.state['entries']
            written, dirty = set(), False
            for ch in req.get('changes') or []:
                k, s = ch.get('k'), ch.get('s') or ''
                if not isinstance(k, str) or not k.startswith('c_') or not isinstance(s, str):
                    continue
                eid = k + SEP + s
                cur = entries.get(eid)
                deleted = bool(ch.get('d'))
                value = None if deleted else ch.get('v')
                if cur and cur.get('d', False) == deleted and cur.get('v') == value:
                    written.add(eid)  # already identical - nothing to record or echo
                    continue
                if first and cur and not (owner and k.endswith('_' + owner)):
                    continue  # first sync: the server's copy of someone else's data wins
                if first and deleted:
                    continue  # a fresh device has no history to delete from
                self.state['rev'] += 1
                entries[eid] = {'v': value, 'd': deleted, 'rev': self.state['rev'],
                                'by': client, 'at': time.strftime('%Y-%m-%dT%H:%M:%S')}
                written.add(eid)
                dirty = True
            if dirty:
                self._daily_backup()
                self._write()
            out = []
            for eid, e in entries.items():
                if e['rev'] > since and eid not in written:
                    k, s = eid.split(SEP, 1)
                    out.append({'k': k, 's': s, 'v': e['v'], 'd': e['d']})
            return {'serverId': self.state['serverId'], 'rev': self.state['rev'], 'entries': out}


USERS = ('jermaine', 'sophia')


def _num(x):
    """HAE wraps numbers as {"qty": n, "units": ...}; Shortcuts may send strings."""
    if isinstance(x, dict):
        x = x.get('qty')
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _when(text):
    """'2026-09-18 07:02:00 -0700' (HAE) or ISO 8601 -> aware/naive datetime, else None."""
    if not isinstance(text, str):
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S.%f%z',
                '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(text.replace('Z', '+0000'), fmt)
        except ValueError:
            pass
    return None


# Health Auto Export metric name -> (our field, how to combine samples within a day)
HAE_METRICS = {
    'active_energy': ('move', 'sum'), 'apple_exercise_time': ('exercise', 'sum'),
    'apple_stand_hour': ('stand', 'sum'), 'step_count': ('steps', 'sum'),
    'resting_heart_rate': ('rhr', 'last'), 'heart_rate_variability': ('hrv', 'mean'),
    'vo2_max': ('vo2max', 'last'), 'respiratory_rate': ('resp', 'mean'),
}


def parse_apple(body):
    """-> {date: {field: value}}. Accepts either

    simple:  {"days": {"2026-09-18": {"move": 520, "moveGoal": 600, "exercise": 42, "stand": 10,
              "steps": 8412, "rhr": 52, "hrv": 61, "vo2max": 44.1, "resp": 14.2,
              "sleep": {"totalSec": 25200, "deepSec": 4200, "remSec": 5400, "bedtime": iso, "waketime": iso},
              "workouts": [{"type": "Strength", "start": iso, "duration": 48, "calories": 390, "avgHr": 128}]}}}
    or Health Auto Export's REST payload: {"data": {"metrics": [...], "workouts": [...]}}.
    """
    days = {}
    if isinstance(body.get('days'), dict):
        for date, vals in body['days'].items():
            if _when(date) and isinstance(vals, dict):
                days[date[:10]] = vals
        return days
    data = body.get('data') or {}
    for metric in data.get('metrics') or []:
        name = metric.get('name')
        for sample in metric.get('data') or []:
            when = _when(sample.get('date'))
            if not when:
                continue
            day = days.setdefault(when.strftime('%Y-%m-%d'), {})
            if name == 'sleep_analysis':
                hours = lambda key: (_num(sample.get(key)) or 0) * 3600
                total = hours('totalSleep') or hours('asleep') or (hours('core') + hours('deep') + hours('rem'))
                if total:
                    day['sleep'] = {'totalSec': round(total), 'deepSec': round(hours('deep')), 'remSec': round(hours('rem')),
                                    'bedtime': (_when(sample.get('sleepStart')) or when).isoformat(),
                                    'waketime': (_when(sample.get('sleepEnd')) or when).isoformat()}
            elif name in HAE_METRICS:
                field, how = HAE_METRICS[name]
                qty = _num(sample.get('qty'))
                if qty is None:
                    continue
                if how == 'sum':
                    day[field] = round(day.get(field, 0) + qty, 1)
                elif how == 'mean':
                    n = day.get('_n_' + field, 0)
                    day[field] = round((day.get(field, 0) * n + qty) / (n + 1), 1)
                    day['_n_' + field] = n + 1
                else:
                    day[field] = round(qty, 1)
    for w in data.get('workouts') or []:
        start, end = _when(w.get('start')), _when(w.get('end'))
        if not start:
            continue
        minutes = _num(w.get('duration'))
        minutes = round(minutes / 60) if minutes and minutes > 600 else (round(minutes) if minutes else
                  (round((end - start).total_seconds() / 60) if end else 0))
        days.setdefault(start.strftime('%Y-%m-%d'), {}).setdefault('workouts', []).append({
            'type': w.get('name') or 'Workout', 'start': start.isoformat(), 'duration': minutes,
            'calories': round(_num(w.get('activeEnergyBurned')) or _num(w.get('activeEnergy')) or 0),
            'avgHr': round(_num(w.get('avgHeartRate')) or _num(w.get('heartRateAvg')) or 0) or None})
    for day in days.values():
        for key in [k for k in day if k.startswith('_n_')]:
            del day[key]
    return days


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    store = None

    def _send(self, status, obj=None):
        body = b'' if obj is None else json.dumps(obj, separators=(',', ':')).encode()
        origin = self.headers.get('Origin')
        self.send_response(status)
        if origin in ALLOWED_ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Max-Age', '86400')
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        return origin is None or origin in ALLOWED_ORIGINS  # no Origin = curl / health checks

    def do_OPTIONS(self):
        self._send(204 if self._origin_ok() else 403)

    def do_GET(self):
        if self.path.rstrip('/') in ('', '/health'):
            st = self.store.state
            return self._send(200, {'ok': True, 'rev': st['rev'], 'entries': len(st['entries'])})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {'error': 'origin not allowed'})
        url = urllib.parse.urlparse(self.path)
        route = url.path.rstrip('/')
        if route not in ('/sync', '/apple'):
            return self._send(404, {'error': 'not found'})
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self._send(413, {'error': 'too large'})
        if route == '/apple':
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
                user = (urllib.parse.parse_qs(url.query).get('user') or [body.get('user')])[0]
                if user not in USERS:
                    return self._send(400, {'error': 'user must be one of %s' % (USERS,)})
                days = parse_apple(body)
                changed = self.store.apple(user, days)
            except Exception as e:
                log.exception('apple push failed')
                return self._send(400, {'error': str(e)})
            log.info('apple push for %s: %d day(s) in payload, %d changed', user, len(days), changed)
            return self._send(200, {'ok': True, 'days': sorted(days), 'changed': changed})
        try:
            req = json.loads(self.rfile.read(length) or b'{}')
            res = self.store.sync(req)
        except Exception as e:  # never take the server down over one bad request
            log.exception('sync failed')
            return self._send(400, {'error': str(e)})
        if req.get('changes') or res['entries']:
            log.info('%s by %s: in=%d out=%d rev=%d%s', self.headers.get('Tailscale-User-Login', 'local'),
                     str(req.get('client'))[:8], len(req.get('changes') or []), len(res['entries']), res['rev'],
                     ' FIRST' if req.get('first') else '')
        self._send(200, res)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='folder for state.json, backups/ and sync.log')
    ap.add_argument('--port', type=int, default=8792)
    args = ap.parse_args()
    os.makedirs(args.data, exist_ok=True)
    logging.basicConfig(filename=os.path.join(args.data, 'sync.log'), level=logging.INFO,
                        format='%(asctime)s %(message)s')
    Handler.store = Store(args.data)
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        return  # already running
    log.info('listening on 127.0.0.1:%d, rev=%d', args.port, Handler.store.state['rev'])
    httpd.serve_forever()


if __name__ == '__main__':
    main()
