"""Bracket-balance check for Swift written on a machine with no Swift toolchain.

    python tools/swift_balance.py ios/Centurion.swiftpm/*.swift

Strips // comments and string literals first (a naive counter false-flags every
file that uses \\( ) interpolation). Not a compiler - it only catches the
unclosed-brace class of mistake before the file goes to the iPad.
"""
import io
import re
import sys

ok = True
for path in sys.argv[1:]:
    src = io.open(path, encoding='utf-8').read()
    src = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', src)
    src = re.sub(r'//[^\n]*', '', src)
    counts = {pair: (src.count(pair[0]), src.count(pair[1])) for pair in ('{}', '()', '[]')}
    good = all(a == b for a, b in counts.values())
    ok = ok and good
    print('%-50s %s %s' % (path, 'balanced  ' if good else 'UNBALANCED', counts))
sys.exit(0 if ok else 1)
