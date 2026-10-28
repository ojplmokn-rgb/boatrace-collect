# 南関東 締切前オッズの自動記録（GitHub Actions用）
# 各レースの発走30分前〜1分前まで、2分おきに単勝オッズを keiba_odds/日付.csv に保存します。
import os, re, time, json, subprocess
import datetime as dt
import requests
import pandas as pd
from bs4 import BeautifulSoup

H = {'User-Agent': 'Mozilla/5.0 (personal research, low rate)'}
JST = dt.timezone(dt.timedelta(hours=9))
NAMES = {'42': '浦和', '43': '船橋', '44': '大井', '45': '川崎'}
ODDS_URL = 'https://nar.netkeiba.com/api/api_get_nar_odds.html?race_id={}&type=1&action=update'
ENTRY_URL = 'https://nar.netkeiba.com/race/shutuba.html?race_id={}'
START_MIN, END_MIN, EVERY_MIN = 30, 1, 2


def now():
    return dt.datetime.now(JST)


DAY = now().date()
STOP = now().replace(hour=15, minute=10, second=0) if now().hour < 15 else now().replace(hour=21, minute=40, second=0)
OUT_DIR = 'keiba_odds'
OUT = f'{OUT_DIR}/{DAY}.csv'
os.makedirs(OUT_DIR, exist_ok=True)


def get(url):
    time.sleep(3)
    try:
        r = requests.get(url, headers=H, timeout=20)
    except Exception:
        return None
    if r.status_code in (400, 403, 429):
        print(f'アクセス制限の可能性：{r.status_code}')
        return None
    if r.status_code != 200:
        return None
    r.encoding = r.apparent_encoding
    return r.text


def post_time(rid):
    page = get(ENTRY_URL.format(rid))
    if not page or 'HorseList' not in page:
        return None
    soup = BeautifulSoup(page, 'html.parser')
    t = soup.find(class_='RaceData01')
    m = re.search(r'(\d{1,2}):(\d{2})\s*発走', t.get_text(' ', strip=True) if t else page)
    if not m:
        return None
    return dt.datetime(DAY.year, DAY.month, DAY.day, int(m.group(1)), int(m.group(2)), tzinfo=JST)


def fetch_odds(rid):
    t = get(ODDS_URL.format(rid))
    try:
        od = json.loads(t)['ary_odds'] if t else {}
    except Exception:
        return {}
    out = {}
    for k, v in od.items():
        try:
            o = float(v['Odds'])
            if o > 0:
                out[int(k)] = o
        except Exception:
            pass
    return out


def commit(msg):
    subprocess.run(['git', 'add', OUT_DIR])
    r = subprocess.run(['git', '-c', 'user.name=github-actions', '-c', 'user.email=actions@github.com',
                        'commit', '-m', msg], capture_output=True)
    if r.returncode != 0:
        return
    for _ in range(3):
        subprocess.run(['git', 'pull', '--rebase', '--autostash'])
        if subprocess.run(['git', 'push']).returncode == 0:
            return
        time.sleep(10)


# ---------- 今日のレースと発走時刻 ----------
races = {}
for code, name in NAMES.items():
    first = f'{DAY.year}{code}{DAY:%m%d}01'
    pt = post_time(first)
    if pt is None:
        continue
    races[first] = pt
    for r in range(2, 13):
        rid = f'{DAY.year}{code}{DAY:%m%d}{r:02d}'
        pt = post_time(rid)
        if pt is not None:
            races[rid] = pt
races = {k: v for k, v in races.items() if v <= STOP + dt.timedelta(minutes=1)}
print(f'{DAY}：対象 {len(races)}レース（{STOP:%H:%M}まで）')
for rid, pt in sorted(races.items(), key=lambda x: x[1]):
    print(f'  {NAMES[rid[4:6]]}{int(rid[-2:])}R {pt:%H:%M}発走')

# ---------- 記録 ----------
last, n, last_commit = {}, 0, now()
while races and now() < STOP:
    pending = [r for r, pt in races.items() if (pt - now()).total_seconds() / 60 > END_MIN - 0.5]
    if not pending:
        break
    for rid in sorted(pending, key=lambda x: races[x]):
        mins = (races[rid] - now()).total_seconds() / 60
        if mins > START_MIN or mins < END_MIN - 0.5:
            continue
        if rid in last and (now() - last[rid]).total_seconds() < EVERY_MIN * 60 - 20:
            continue
        od = fetch_odds(rid)
        if not od:
            continue
        ts = now()
        mins = (races[rid] - ts).total_seconds() / 60
        pd.DataFrame([{'race_id': rid, 'umaban': u, 'odds': o,
                       'fetched_at': ts.strftime('%Y-%m-%d %H:%M:%S'),
                       'post_at': races[rid].strftime('%Y-%m-%d %H:%M'),
                       'min_to_post': round(mins, 1)} for u, o in od.items()]).to_csv(
            OUT, mode='a', index=False, header=not os.path.exists(OUT), encoding='utf-8-sig')
        last[rid] = ts
        n += 1
        print(f'{ts:%H:%M} {NAMES[rid[4:6]]}{int(rid[-2:])}R 発走{mins:.0f}分前（{len(od)}頭）')
    if (now() - last_commit).total_seconds() > 20 * 60 and os.path.exists(OUT):
        commit(f'keiba odds {DAY} {now():%H:%M}')
        last_commit = now()
    time.sleep(30)

if os.path.exists(OUT):
    commit(f'keiba odds {DAY} final {now():%H:%M}')
print(f'終了：{n}回分を記録')
