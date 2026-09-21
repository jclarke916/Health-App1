"""Keep the Centurion home services alive. Run by Task Scheduler every 5 minutes.

    pythonw tools/centurion_watchdog.py

Scheduled-task "restart on failure" only catches a process that exits. This also
catches the cases it misses: a service that is still running but not answering, one
that was stopped rather than crashed, and Ollama, which has no supervisor of its own.

A service is only restarted after TWO consecutive failed checks, so a slow moment
(Ollama busy loading a 96 GB model) never triggers a restart.

Writes <data>/watchdog.log: every action, plus one heartbeat line a day.
"""
import json
import os
import re
import subprocess
import time
import urllib.request

DATA = r'C:\Users\Scene\CenturionData'
STATE = os.path.join(DATA, 'watchdog_state.json')
LOG = os.path.join(DATA, 'watchdog.log')
OLLAMA_LOG = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Ollama', 'server.log')
NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW: pythonw must not flash a console

# order matters: the forwarder depends on Ollama, so Ollama is judged first
CHECKS = [
    ('sync',    'http://127.0.0.1:8792/health',           'Centurion_Sync'),
    ('ollama',  'http://100.68.21.110:11434/api/version', 'Centurion_Ollama'),
    ('forward', 'http://127.0.0.1:11434/api/version',     'Centurion_OllamaForward'),
]


def log(msg):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))


def healthy(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)


def restart_task(name):
    run('schtasks', '/End', '/TN', name)     # also ends a hung process the task owns
    time.sleep(2)
    return run('schtasks', '/Run', '/TN', name).returncode == 0


def restart_ollama():
    # Ollama is the tray app plus a server child; a wedged one must go before relaunch
    for image in ('ollama app.exe', 'ollama.exe'):
        run('taskkill', '/F', '/IM', image)
    time.sleep(3)
    return run('schtasks', '/Run', '/TN', 'Centurion_Ollama').returncode == 0


def gpu_state():
    """The 09-17 failure mode: an interrupted upgrade left Ollama on the CPU."""
    try:
        with open(OLLAMA_LOG, encoding='utf-8', errors='replace') as f:
            lines = [l for l in f if 'msg="inference compute"' in l]
        m = re.search(r'library=(\w+)', lines[-1]) if lines else None
        return m.group(1) if m else None
    except OSError:
        return None


def main():
    try:
        with open(STATE, encoding='utf-8') as f:
            state = json.load(f)
    except Exception:
        state = {}
    fails = state.setdefault('fails', {})

    status = {name: healthy(url) for name, url, _ in CHECKS}
    for name, url, task in CHECKS:
        if status[name]:
            if fails.get(name):
                log('%s recovered' % name)
            fails[name] = 0
            continue
        # a dead Ollama makes the forwarder fail too - fix the cause, not the symptom
        if name == 'forward' and not status['ollama']:
            continue
        fails[name] = fails.get(name, 0) + 1
        if fails[name] < 2:
            log('%s not answering (1st miss, will restart if it misses again)' % name)
            continue
        done = restart_ollama() if name == 'ollama' else restart_task(task)
        log('%s down %d checks running -> restart %s' % (name, fails[name], 'ok' if done else 'FAILED'))
        fails[name] = 0

    lib = gpu_state()
    today = time.strftime('%Y-%m-%d')
    if lib and lib.lower() == 'cpu' and state.get('cpu_warned') != today:
        log('WARNING: Ollama is running on the CPU, not the GPU - replies will be slow. '
            'Likely an interrupted Ollama update: re-run the Ollama installer.')
        state['cpu_warned'] = today
    if state.get('heartbeat') != today:
        log('heartbeat: %s | gpu=%s' % (' '.join('%s=%s' % (n, 'up' if status[n] else 'DOWN')
                                                  for n, _, _ in CHECKS), lib or '?'))
        state['heartbeat'] = today

    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:   # a watchdog that crashes silently is worse than none
        log('watchdog error: %r' % (e,))
