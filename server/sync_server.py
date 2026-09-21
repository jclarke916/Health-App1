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
import hmac
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
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
                derived = k.startswith('c_points_')  # re-computed scores: a fresh device must not overwrite them
                if first and cur and (derived or not (owner and k.endswith('_' + owner))):
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
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    # Shortcuts sends text: "8,412", "540.2 kcal", "52 count/min" - or "" when there were no samples
    m = re.search(r'-?\d+(?:\.\d+)?', str(x).replace(',', ''))
    return float(m.group(0)) if m else None


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
    if 'days' not in body and 'data' not in body:
        # flat form, easiest to build in Shortcuts: one day, numbers may be text.
        # {"date": "2026-09-18" (optional, default today), "move": .., "moveGoal": .., "exercise": ..,
        #  "stand": .., "steps": .., "rhr": .., "hrv": .., "vo2max": .., "resp": .., "sleepHours": ..,
        #  "deepHours": .., "remHours": ..}
        when = _when(str(body.get('date') or '')[:10]) or datetime.datetime.now()
        day = {}
        for field in ('move', 'moveGoal', 'exercise', 'stand', 'steps', 'rhr', 'hrv', 'vo2max', 'resp'):
            val = _num(body.get(field))
            if val is not None and val > 0:
                day[field] = round(val, 1)
        hours = _num(body.get('sleepHours'))
        if hours and 0 < hours < 24:
            day['sleep'] = {'totalSec': round(hours * 3600), 'deepSec': round((_num(body.get('deepHours')) or 0) * 3600),
                            'remSec': round((_num(body.get('remHours')) or 0) * 3600)}
        if day:
            days[when.strftime('%Y-%m-%d')] = day
        return days
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


class Oura:
    """Oura OAuth2 on behalf of the household.

    Personal Access Tokens were killed by Oura in 2026 (new ones cannot be created and old
    ones are being switched off), so the only way in is OAuth2 - which needs a client secret.
    A public GitHub Pages app cannot hold one, so it lives here:

        <data>/oura_client.json   {"client_id": "...", "client_secret": "..."}
        <data>/oura_tokens.json   written by us, per user

    The phones never see an Oura credential at all. They ask this server for data and it
    attaches the bearer token itself, refreshing it when it has expired.
    """
    AUTHORIZE = 'https://cloud.ouraring.com/oauth/authorize'
    TOKEN = 'https://api.ouraring.com/oauth/token'
    API = 'https://api.ouraring.com/v2/usercollection/'
    SCOPES = 'daily workout'          # only what the app actually reads
    ENDPOINTS = {'sleep', 'workout', 'daily_activity', 'daily_readiness', 'daily_hrv', 'daily_sleep'}

    def __init__(self, data_dir):
        self.dir = data_dir
        self.tokens_path = os.path.join(data_dir, 'oura_tokens.json')
        self.lock = threading.Lock()
        self.client = self._read(os.path.join(data_dir, 'oura_client.json'), {})
        self.tokens = self._read(self.tokens_path, {})

    @staticmethod
    def _read(path, default):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return dict(default)

    def _save_tokens(self):
        tmp = self.tokens_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.tokens, f, indent=1)
        os.replace(tmp, self.tokens_path)

    def configured(self):
        return bool(self.client.get('client_id') and self.client.get('client_secret'))

    def status(self):
        return {'configured': self.configured(),
                'client_id': self.client.get('client_id', ''),
                'scopes': self.SCOPES,
                'connected': {u: bool(self.tokens.get(u, {}).get('refresh_token')) for u in USERS}}

    def _post_token(self, fields):
        body = urllib.parse.urlencode(dict(
            fields, client_id=self.client['client_id'], client_secret=self.client['client_secret']
        )).encode()
        req = urllib.request.Request(self.TOKEN, data=body, method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError('Oura refused the request: %s %s' % (e.code, e.read().decode()[:200]))

    def _store(self, user, tok):
        if not tok.get('access_token'):
            raise RuntimeError('Oura returned no access token')
        keep = self.tokens.get(user, {})
        self.tokens[user] = {
            'access_token': tok['access_token'],
            # a refresh response does not always resend the refresh token - keep the old one
            'refresh_token': tok.get('refresh_token') or keep.get('refresh_token'),
            'expires_at': time.time() + int(tok.get('expires_in') or 3600) - 120,
            'scope': tok.get('scope', keep.get('scope', '')),
            'linked_at': keep.get('linked_at') or time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        self._save_tokens()

    def exchange(self, user, code, redirect_uri):
        with self.lock:
            self._store(user, self._post_token({'grant_type': 'authorization_code', 'code': code,
                                                'redirect_uri': redirect_uri}))
            log.info('oura linked for %s (scopes %s)', user, self.tokens[user].get('scope'))

    def disconnect(self, user):
        with self.lock:
            if self.tokens.pop(user, None) is not None:
                self._save_tokens()

    def _bearer(self, user):
        with self.lock:
            t = self.tokens.get(user)
            if not t:
                raise RuntimeError('%s has not connected an Oura account yet' % user)
            if time.time() >= t.get('expires_at', 0):
                if not t.get('refresh_token'):
                    raise RuntimeError('the Oura link for %s expired - reconnect in Settings' % user)
                self._store(user, self._post_token({'grant_type': 'refresh_token',
                                                    'refresh_token': t['refresh_token']}))
                log.info('oura token refreshed for %s', user)
            return self.tokens[user]['access_token']

    def fetch(self, user, endpoint, start, end):
        if endpoint not in self.ENDPOINTS:
            raise RuntimeError('endpoint not allowed')
        token = self._bearer(user)
        url = '%s%s?%s' % (self.API, endpoint,
                           urllib.parse.urlencode({'start_date': start, 'end_date': end}))
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError('Oura API %s: %s' % (e.code, e.read().decode()[:200]))


# Ollama is only ever reached through here. It has no auth of its own, and its API can pull,
# copy and DELETE models - so only these read/chat calls are passed through, and only with the key.
AI_UPSTREAM = 'http://127.0.0.1:11434'
AI_ALLOWED = {('POST', '/api/chat'), ('POST', '/api/show'), ('GET', '/api/tags'), ('GET', '/api/version')}


def load_key(data_dir):
    """<data>/household_key.txt, one line. No file = auth off (how it ran before Funnel)."""
    try:
        with open(os.path.join(data_dir, 'household_key.txt'), encoding='utf-8') as f:
            key = f.read().strip()
        return key or None
    except OSError:
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    store = None
    oura = None
    key = None

    def _cors(self):
        origin = self.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Max-Age', '86400')
            self.send_header('Vary', 'Origin')

    def _send(self, status, obj=None):
        body = b'' if obj is None else json.dumps(obj, separators=(',', ':')).encode()
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self):
        origin = self.headers.get('Origin')
        return origin is None or origin in ALLOWED_ORIGINS  # no Origin = curl / health checks

    def _authorized(self):
        """With Funnel on, this server is on the public internet: every request must carry the key."""
        if not self.key:
            return True
        got = self.headers.get('Authorization', '')
        got = got[7:].strip() if got.lower().startswith('bearer ') else ''
        return bool(got) and hmac.compare_digest(got.encode(), self.key.encode())

    def _deny(self):
        return self._send(401, {'error': 'household key required', 'needKey': True})

    def _proxy_ai(self, method, path, body=None):
        """Pass one allowlisted Ollama call through, streaming the reply as it arrives."""
        if (method, path) not in AI_ALLOWED:
            return self._send(403, {'error': 'that AI call is not allowed through this server'})
        if path == '/api/chat':
            return self._proxy_chat(body)
        req = urllib.request.Request(AI_UPSTREAM + path, data=body, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            upstream = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            return self._send(e.code, {'error': e.read().decode(errors='replace')[:300]})
        except Exception as e:
            return self._send(502, {'error': 'the AI server is not answering: %s' % e})
        with upstream:
            self.send_response(upstream.status)
            self._cors()
            self.send_header('Content-Type', upstream.headers.get('Content-Type') or 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            read = getattr(upstream, 'read1', None) or upstream.read
            try:
                while True:
                    chunk = read(65536)
                    if not chunk:
                        break
                    self.wfile.write(b'%x\r\n%s\r\n' % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b'0\r\n\r\n')
            except (BrokenPipeError, ConnectionResetError):
                pass   # the phone went away mid-reply

    def _proxy_chat(self, body):
        """Chat, with a keep-alive while the model loads.

        Ollama sends nothing - not even headers - until the model is loaded, and swapping out
        the 96 GB GC_OS model took 98 s in testing. A phone drops a connection that is silent
        for ~60 s, so answer at once and send a blank line every 5 s until the model speaks.
        The app's stream reader already skips blank lines, and JSON.parse ignores leading
        whitespace, so both streaming and one-shot replies read exactly as before.
        """
        req = urllib.request.Request(AI_UPSTREAM + '/api/chat', data=body, method='POST',
                                     headers={'Content-Type': 'application/json'})
        result, done = {}, threading.Event()

        def call():
            try:
                result['resp'] = urllib.request.urlopen(req, timeout=900)
            except urllib.error.HTTPError as e:
                result['err'] = e.read().decode(errors='replace')[:300] or 'HTTP %d' % e.code
            except Exception as e:
                result['err'] = 'the AI server is not answering: %s' % e
            done.set()
        threading.Thread(target=call, daemon=True).start()

        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()

        def write(b):
            self.wfile.write(b'%x\r\n%s\r\n' % (len(b), b))
            self.wfile.flush()
        try:
            while not done.wait(5):
                write(b'\n')
            if 'err' in result:
                # already committed to 200: report the failure the way Ollama reports stream errors
                write((json.dumps({'error': result['err']}) + '\n').encode())
            else:
                with result['resp'] as up:
                    read = getattr(up, 'read1', None) or up.read
                    while True:
                        chunk = read(65536)
                        if not chunk:
                            break
                        write(chunk)
            self.wfile.write(b'0\r\n\r\n')
        except (BrokenPipeError, ConnectionResetError):
            pass   # the phone went away mid-reply

    def do_OPTIONS(self):
        self._send(204 if self._origin_ok() else 403)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        route = url.path.rstrip('/')
        if route in ('', '/health'):
            if not self._authorized():   # public: prove it is alive, reveal nothing
                return self._send(200, {'ok': True})
            st = self.store.state
            return self._send(200, {'ok': True, 'rev': st['rev'], 'entries': len(st['entries']),
                                    'oura': self.oura.status(), 'auth': bool(self.key)})
        if not self._origin_ok():
            return self._send(403, {'error': 'origin not allowed'})
        if not self._authorized():
            return self._deny()
        if route.startswith('/ai/'):
            return self._proxy_ai('GET', route[3:])
        if route == '/oura/config':
            return self._send(200, self.oura.status())
        if route == '/oura/data':
            q = urllib.parse.parse_qs(url.query)
            one = lambda k: (q.get(k) or [''])[0]
            user, endpoint = one('user'), one('endpoint')
            if user not in USERS:
                return self._send(400, {'error': 'user must be one of %s' % (USERS,)})
            try:
                return self._send(200, self.oura.fetch(user, endpoint, one('start'), one('end')))
            except Exception as e:
                log.info('oura fetch failed for %s/%s: %s', user, endpoint, e)
                return self._send(502, {'error': str(e)})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {'error': 'origin not allowed'})
        url = urllib.parse.urlparse(self.path)
        route = url.path.rstrip('/')
        if not self._authorized():
            return self._deny()
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            return self._send(413, {'error': 'too large'})
        if route.startswith('/ai/'):
            return self._proxy_ai('POST', route[3:], self.rfile.read(length))
        if route not in ('/sync', '/apple', '/oura/exchange', '/oura/disconnect'):
            return self._send(404, {'error': 'not found'})
        if route.startswith('/oura/'):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                user = body.get('user')
                if user not in USERS:
                    return self._send(400, {'error': 'user must be one of %s' % (USERS,)})
                if route == '/oura/disconnect':
                    self.oura.disconnect(user)
                else:
                    if not self.oura.configured():
                        raise RuntimeError('this server has no Oura client id/secret yet')
                    self.oura.exchange(user, body.get('code') or '', body.get('redirect_uri') or '')
            except Exception as e:
                log.info('oura %s failed: %s', route, e)
                return self._send(400, {'error': str(e)})
            return self._send(200, {'ok': True, 'oura': self.oura.status()})
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
    Handler.oura = Oura(args.data)
    Handler.key = load_key(args.data)
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        return  # already running
    log.info('listening on 127.0.0.1:%d, rev=%d, household key %s', args.port,
             Handler.store.state['rev'], 'REQUIRED' if Handler.key else 'off')
    httpd.serve_forever()


if __name__ == '__main__':
    main()
