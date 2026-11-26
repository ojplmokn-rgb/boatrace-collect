# 中継対応版 11/26
# 前日までの公式の競走成績ファイル（着順・払戻）を保存する
import requests, subprocess, datetime as dt
from pathlib import Path
JST = dt.timezone(dt.timedelta(hours=9))
# ---------- 公式サイトの取得（11/18からGitHubは直接つながらないので、つながる経路を順に試す）
import urllib.parse as _up, base64 as _b64, time as _tm
_RS = requests.Session()
_RS.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
_RS.headers['Accept-Language'] = 'ja'


def _gas():
    for p in ('boat_code/gas_url.txt', 'gas_url.txt'):
        try:
            u = open(p, encoding='utf-8').read().strip()
        except Exception:
            continue
        if u.startswith('https://script.google.com/'):
            return u
    return None


def 経路():
    q = lambda u: _up.quote(u, safe='')
    r = [('直接', lambda u: u, False)]
    g = _gas()
    if g:
        r.append(('GAS', lambda u, g=g: g + '?u=' + q(u), True))
    return r + [('allorigins', lambda u: 'https://api.allorigins.win/raw?url=' + q(u), False),
                ('codetabs', lambda u: 'https://api.codetabs.com/v1/proxy/?quest=' + q(u), False),
                ('corsproxy', lambda u: 'https://corsproxy.io/?url=' + q(u), False),
                ('cors.eu.org', lambda u: 'https://cors.eu.org/' + u, False),
                ('thingproxy', lambda u: 'https://thingproxy.freeboard.io/fetch/' + u, False)]


def 中身OK(u, b):
    if 'mbrace' in u:
        return len(b) > 1000 and b[2:5] == b'-lh'
    if len(b) < 3000:
        return False
    if 'odds3t' in u or 'odds2tf' in u:
        return b'oddsPoint' in b
    if 'beforeinfo' in u:
        return b'is-fs12' in b or b'table1' in b
    if '/index' in u:
        return b'jcd=' in b or b'table1' in b
    return b'boatrace' in b


class _Res:
    status_code = 200

    def __init__(self, b, via):
        self.content, self.via = b, via

    @property
    def text(self):
        return self.content.decode('utf-8', 'replace')


_BEST = [0]


def 取得(u, timeout=25):
    rs = 経路()
    order = [_BEST[0]] + [i for i in range(len(rs)) if i != _BEST[0]]
    for i in order[:len(rs)]:
        nm, f, b64 = rs[i]
        uu = u if (i == 0 or 'mbrace' in u) else u + ('&' if '?' in u else '?') + '_=%d' % int(_tm.time())
        try:
            r = _RS.get(f(uu), timeout=timeout)
        except Exception:
            continue
        b = r.content
        if b64:
            code, _, body = b.partition(b'\n')
            try:
                b = _b64.b64decode(body) if code.strip() == b'200' else b''
            except Exception:
                b = b''
        if r.status_code == 200 and 中身OK(u, b):
            _BEST[0] = i
            return _Res(b, nm)
    return None

for k in range(1, 15):
    d = dt.datetime.now(JST) - dt.timedelta(days=k)
    f = Path('data') / d.strftime('%Y%m%d') / ('k%s.lzh' % d.strftime('%y%m%d'))
    if f.exists():
        continue
    r = 取得('https://www1.mbrace.or.jp/od2/K/%s/%s' % (d.strftime('%Y%m'), f.name), timeout=30)
    if r is not None:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.content)
        print('保存', f, len(r.content))
subprocess.run('git config user.name bot && git config user.email bot@example.com && git add data && '
               '(git diff --cached --quiet || (git commit -q -m results && git pull -q --rebase && git push -q))',
               shell=True)
