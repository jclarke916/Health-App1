"""One-off: pull the personal seed data out of the legacy single-file build
into private/seed-backup.json (gitignored), in the app's backup-import format.
Tokens are deliberately NOT exported - they were public and must be rotated."""
import json, re, sys, io
src = io.open(sys.argv[1], encoding='utf-8').read()
keys = {}
for u in ('jermaine', 'sophia'):
    line = re.search(r'^%s:\{name:.*$' % u, src, re.M).group(0)
    dob = re.search(r'dob:"([^"]+)"', line).group(1)
    bios = re.search(r'defaultBios:\{([^}]*)\}', line).group(1)
    keys['c_profile_' + u] = {'dob': dob}
    keys['c_bios_' + u] = dict(re.findall(r"(\w+):'([^']*)'", bios))
seeds = re.search(r'const seeds=\{jermaine:\[(.*?)\],sophia:\[(.*?)\]\};', src, re.S)
keys['c_bioHist_jermaine'] = json.loads('[' + seeds.group(1) + ']')
keys['c_bioHist_sophia'] = json.loads('[' + seeds.group(2) + ']')
ms = re.search(r"setItem\('c_manualSleep_jermaine',JSON\.stringify\((\{.*?\})\)\);", src).group(1)
keys['c_manualSleep_jermaine'] = json.loads(re.sub(r"'", '"', re.sub(r'(\w+):(?=[\d{])', r'"\1":', ms)))
json.dump({'_format': 'centurion-backup-v1', 'keys': keys}, io.open('private/seed-backup.json', 'w', encoding='utf-8'), indent=1)
print({k: (len(v) if hasattr(v, '__len__') else v) for k, v in keys.items()})
