# BOATRACE 締切前データの自動収集（GitHub Actions 用）
# 締切10/3/1分前の3連単オッズ（O10/O3/O1）と締切3分前の直前情報（B3）を data/日付/ に保存する
import requests, re, gzip, time, sys, json, subprocess, datetime as dt
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))
BASE = 'https://www.boatrace.jp/owpc/pc/race/'
JOBS = [('O10', 10, 'odds3t'), ('O3', 3, 'odds3t'), ('O1', 1, 'odds3t'), ('B3', 3, 'beforeinfo')]
SLOTS = [(7 * 60, 13 * 60 + 20), (13 * 60, 18 * 60 + 40), (18 * 60 + 20, 24 * 60)]
S = requests.Session()
S.headers['User-Agent'] = 'Mozilla/5.0'


def log(*a):
    print(dt.datetime.now(JST).strftime('%H:%M:%S'), *a, flush=True)


def get(url):
    for _ in range(2):
        try:
            r = S.get(url, timeout=20)
            if r.status_code == 200:
                return r
            log('HTTP', r.status_code, url)
        except Exception as e:
            log('エラー', e, url)
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
