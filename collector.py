# BOATRACE 締切前データの自動収集（GitHub Actions 用）  中継対応版 11/26
# 締切10/3/1分前の3連単オッズ（O10/O3/O1）と締切3分前の直前情報（B3）を data/日付/ に保存する
import requests, re, gzip, time, sys, json, subprocess, datetime as dt
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))
BASE = 'https://www.boatrace.jp/owpc/pc/race/'
JOBS = [('O10', 10, 'odds3t'), ('O3', 3, 'odds3t'), ('O1', 1, 'odds3t'), ('B3', 3, 'beforeinfo')]
SLOTS = [(7 * 60, 13 * 60 + 20), (13 * 60, 18 * 60 + 40), (18 * 60 + 20, 24 * 60)]
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



def log(*a):
    print(dt.datetime.now(JST).strftime('%H:%M:%S'), *a, flush=True)


def get(url):
    for _ in range(2):
        r = 取得(url)
        if r is not None:
            if r.via != getattr(get, 'via', None):
                log('取得経路:', r.via)
                get.via = r.via
            return r
        log('取得できません', url)
        time.sleep(2)
    return None


def venues(html):
    return sorted(set(re.findall(r'jcd=(\d{2})', html)))


def times(html):
    k = html.find('締切予定時刻')
    return re.findall(r'\d{1,2}:\d{2}', html[k:k + 5000])[:12] if k >= 0 else []


def schedule(hd):
    r = get(BASE + 'index?hd=' + hd)
    sch = []
    for j in venues(r.text if r else ''):
        b = get(BASE + 'beforeinfo?rno=1&jcd=%s&hd=%s' % (j, hd))
        time.sleep(0.5)
        for n, t in enumerate(times(b.text if b else '')):
            h, m = map(int, t.split(':'))
            sch.append((j, n + 1, h * 60 + m))
    return sch


def push(msg):
    subprocess.run('git config user.name bot && git config user.email bot@example.com', shell=True)
    subprocess.run('git add data', shell=True)
    if subprocess.run('git diff --cached --quiet', shell=True).returncode == 0:
        return
    subprocess.run(['git', 'commit', '-q', '-m', msg])
    for _ in range(3):
        subprocess.run('git pull -q --rebase', shell=True)
        if subprocess.run('git push -q', shell=True).returncode == 0:
            log('保存しました')
            return
        time.sleep(5)
    log('保存に失敗しました')


def now_min():
    t = dt.datetime.now(JST)
    return t.hour * 60 + t.minute + t.second / 60


mode = sys.argv[1] if len(sys.argv) > 1 else 'run'
hd = dt.datetime.now(JST).strftime('%Y%m%d')

if mode == 'test':
    r = get(BASE + 'index?hd=' + hd)
    js = venues(r.text) if r else []
    log('本日の開催場', js)
    if js:
        b = get(BASE + 'beforeinfo?rno=1&jcd=%s&hd=%s' % (js[0], hd))
        log('直前情報', len(b.text) if b else None, '文字 / 締切時刻', times(b.text) if b else [])
        o = get(BASE + 'odds3t?rno=1&jcd=%s&hd=%s' % (js[0], hd))
        log('オッズ', len(o.text) if o else None, '文字')
    sys.exit(0)

m0 = now_min()
slot = next((s for s in SLOTS if m0 < s[1] - 20), None)
if slot is None:
    log('この時間帯は対象外')
    sys.exit(0)
sch = [x for x in schedule(hd) if slot[0] <= x[2] < slot[1]]
out = Path('data') / hd
out.mkdir(parents=True, exist_ok=True)
(out / ('schedule_%d.json' % slot[0])).write_text(json.dumps(sch))
log('対象レース', len(sch), '/ 時間帯', slot)

done = set()
last = time.time()
while now_min() < min(slot[1] + 2, 24 * 60 - 0.5):
    cur = now_min()
    for j, r, dl in sch:
        left = dl - cur
        rc = '%s%s%02d' % (hd, j, r)
        for tag, m, page in JOBS:
            key = rc + tag
            if key in done or not (m - 1.5 < left <= m):
                continue
            done.add(key)
            res = get(BASE + '%s?rno=%d&jcd=%s&hd=%s' % (page, r, j, hd))
            if res is not None:
                stamp = dt.datetime.now(JST).strftime('%H%M%S')
                (out / ('%s_%s_%s.html.gz' % (rc, tag, stamp))).write_bytes(gzip.compress(res.content))
            time.sleep(0.3)
    if time.time() - last > 1800:
        push('collect %s' % hd)
        last = time.time()
    time.sleep(20)
push('collect %s end' % hd)
log('終了: 取得', len(done))
