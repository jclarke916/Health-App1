"""Exercise the Cloudflare worker running locally (wrangler dev on 8799) the way the phones do."""
import json, threading, urllib.error, urllib.request

BASE = 'http://127.0.0.1:8799'
KEY = 'test-key-for-local-runs-only-0000'
ok = fail = 0


def call(path, body=None, key=KEY, method=None):
    req = urllib.request.Request(BASE + path, method=method or ('POST' if body is not None else 'GET'),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + key} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or '{}')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}')


def check(name, cond, detail=''):
    global ok, fail
    if cond:
        ok += 1
        print('  PASS', name)
    else:
        fail += 1
        print('  FAIL', name, detail)


def sync(client, changes=(), since=0, server=None, first=False, owner=''):
    return call('/sync', {'client': client, 'since': since, 'serverId': server, 'first': first,
                          'owner': owner, 'changes': list(changes)})


print('auth')
check('no key refused', call('/sync', {'client': 'x'}, key=None)[0] == 401)
check('wrong key refused', call('/sync', {'client': 'x'}, key='nope')[0] == 401)
check('bare health needs no key', call('/health', key=None) == (200, {'ok': True}))

print('\nfirst sync + two devices')
st, a1 = sync('phoneA', first=True, owner='jermaine')
sid, rev0 = a1['serverId'], a1['rev']
check('first sync returns a server id', st == 200 and len(sid) == 32, a1)

st, a2 = sync('phoneA', [{'k': 'c_meals_jermaine', 's': '2026-09-22', 'v': {'lunch': 3}, 'd': False}], since=rev0, server=sid)
check('phone A write accepted', st == 200 and a2['rev'] == rev0 + 1, a2)

st, b1 = sync('phoneB', since=rev0, server=sid)
check('phone B receives it', any(e['k'] == 'c_meals_jermaine' and e['v'] == {'lunch': 3} for e in b1['entries']), b1)

print('\nechoes and no-ops')
st, a3 = sync('phoneA', [{'k': 'c_meals_jermaine', 's': '2026-09-22', 'v': {'lunch': 3}, 'd': False}], since=a2['rev'], server=sid)
check('identical write does not bump rev', a3['rev'] == a2['rev'], a3)
st, a4 = sync('phoneA', [{'k': 'c_meals_jermaine', 's': '2026-09-22', 'v': {'lunch': 4}, 'd': False}], since=a2['rev'], server=sid)
check('own write is not echoed back', not any(e['s'] == '2026-09-22' and e['k'] == 'c_meals_jermaine' for e in a4['entries']), a4)

print('\ndeletes')
st, d1 = sync('phoneA', [{'k': 'c_meals_jermaine', 's': '2026-09-21', 'v': {'lunch': 9}, 'd': False}], since=a4['rev'], server=sid)
st, d2 = sync('phoneA', [{'k': 'c_meals_jermaine', 's': '2026-09-21', 'd': True}], since=d1['rev'], server=sid)
st, d3 = sync('phoneB', since=d1['rev'], server=sid)
check('delete reaches the other phone', any(e['s'] == '2026-09-21' and e['d'] for e in d3['entries']), d3)

print('\nfirst sync protects what is already there')
st, f1 = sync('freshPhone', [
    {'k': 'c_meals_jermaine', 's': '2026-09-22', 'v': {'lunch': 'WIPED'}, 'd': False},   # not this device's user
    {'k': 'c_meals_sophia', 's': '2026-09-22', 'v': {'lunch': 'sophia-own'}, 'd': False},  # its own user: allowed
    {'k': 'c_points_jermaine', 's': '2026-09-22', 'v': {'total': 0}, 'd': False},          # derived: never
    {'k': 'c_meals_jermaine', 's': '2026-09-21', 'd': True},                               # no history to delete
], first=True, owner='sophia')
st, after = sync('phoneB', since=0, server=sid)
vals = {(e['k'], e['s']): e for e in after['entries']}
check("other user's data survived", vals[('c_meals_jermaine', '2026-09-22')]['v'] == {'lunch': 4}, vals.get(('c_meals_jermaine', '2026-09-22')))
check("fresh phone's own user accepted", vals[('c_meals_sophia', '2026-09-22')]['v'] == {'lunch': 'sophia-own'})
check('derived points not overwritten', ('c_points_jermaine', '2026-09-22') not in vals)
check('tombstone from a fresh phone ignored', vals[('c_meals_jermaine', '2026-09-21')]['d'] is True)

print('\nbad input')
st, junk = sync('phoneA', [{'k': 'not_ours', 's': 'x', 'v': 1}, {'k': 'c_ok', 's': 'y', 'v': 2}], since=after['rev'], server=sid)
st, seen = sync('phoneB', since=after['rev'], server=sid)
check('non-c_ keys ignored', not any(e['k'] == 'not_ours' for e in seen['entries']), seen)

print('\nwatch push')
st, w1 = call('/apple?user=sophia', {'date': '2026-09-22', 'move': '540.2 kcal', 'steps': '8,412', 'rhr': '52', 'sleepHours': '7.5'})
check('flat Shortcuts payload accepted', st == 200 and w1['changed'] == 1, w1)
st, w2 = call('/apple?user=sophia', {'date': '2026-09-22', 'exercise': '42'})
st, w3 = sync('phoneB', since=0, server=sid)
apple = next((e['v'] for e in w3['entries'] if e['k'] == 'c_apple_sophia' and e['s'] == '2026-09-22'), None)
check('numbers parsed out of text', apple and apple['move'] == 540.2 and apple['steps'] == 8412, apple)
check('second push merges, keeps the first', apple and apple['exercise'] == 42 and apple['rhr'] == 52, apple)
check('sleep hours stored as seconds', apple and apple['sleep']['totalSec'] == 27000, apple)
st, w4 = call('/apple?user=sophia', {'date': '2026-09-22', 'exercise': '42'})
check('identical push changes nothing', w4['changed'] == 0, w4)

print('\noura')
st, cfg = call('/oura/config')
check('config reports configured', st == 200 and cfg['configured'] and cfg['connected'] == {'jermaine': False, 'sophia': False}, cfg)
st, bad = call('/oura/data?user=jermaine&endpoint=../secrets&start=2026-09-01&end=2026-09-02')
check('endpoint allowlist holds', st == 400 and 'not allowed' in bad.get('error', ''), bad)
st, un = call('/oura/data?user=jermaine&endpoint=sleep&start=2026-09-01&end=2026-09-02')
check('unlinked user says so', st == 500 and 'not connected' in un.get('error', ''), un)
st, who = call('/oura/exchange', {'user': 'nobody', 'code': 'x', 'redirect_uri': 'y'})
check('unknown user refused', st == 400, who)

print('\ntwo phones writing at the same moment')
results = {}


def writer(name, n):
    changes = [{'k': f'c_race_{name}', 's': str(i), 'v': {'n': i}, 'd': False} for i in range(n)]
    results[name] = sync(name, changes, since=0, server=sid)


ts = [threading.Thread(target=writer, args=(n, 12)) for n in ('racerA', 'racerB')]
[t.start() for t in ts]
[t.join() for t in ts]
check('both concurrent syncs succeeded', all(r[0] == 200 for r in results.values()), results)
st, final = sync('auditor', since=0, server=sid)
for who_ in ('racerA', 'racerB'):
    got = sorted(int(e['s']) for e in final['entries'] if e['k'] == f'c_race_{who_}')
    check(f'{who_} kept all 12 writes', got == list(range(12)), got)
revs = [e for e in final['entries']]
check('no entry lost to the race', len([e for e in final['entries'] if e['k'].startswith('c_race_')]) == 24)

print(f'\n{ok} passed, {fail} failed')
