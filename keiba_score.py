# 南関東 ワイド3点の自動採点（GitHub Actions用）
# その日の予想（keiba_preds/日付.csv）を結果と照らし合わせて、成績をGitHubの通知で知らせます。
import os, re, glob, time, shutil, subprocess, functools
import datetime as dt
import requests
import pandas as pd
from bs4 import BeautifulSoup

print = functools.partial(print, flush=True)
OWNER = 'ojplmokn-rgb'
JST = dt.timezone(dt.timedelta(hours=9))
DAY = os.environ.get('SCORE_DATE') or str(dt.datetime.now(JST).date())
H = {'User-Agent': 'Mozilla/5.0 (personal research, low rate)'}
NAMES = {'42': '浦和', '43': '船橋', '44': '大井', '45': '川崎'}
PRED = f'keiba_preds/{DAY}.csv'
OUT_DIR = 'keiba_scores'
OUT = f'{OUT_DIR}/{DAY}.csv'


def wide_payouts(rid):
    time.sleep(3)
    try:
        r = requests.get(f'https://nar.netkeiba.com/race/result.html?race_id={rid}', headers=H, timeout=20)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, 'html.parser')
    out = {}
    for tr in soup.find_all('tr'):
        th = tr.find('th')
        if not th or 'ワイド' not in th.get_text():
            continue
        td = tr.find('td', class_=re.compile('Result'))
        if td is None:
            continue
        nums = [int(x) for x in re.findall(r'\d+', td.get_text(' '))]
        pays = [int(x.replace(',', '')) for x in re.findall(r'([\d,]+)円', tr.get_text(' '))]
        for i, p in enumerate(pays):
            if 2 * i + 1 < len(nums):
                out[tuple(sorted(nums[2 * i:2 * i + 2]))] = p
    return out or None


def summary(df):
    bet = df[df['yen'] > 0]
    if bet.empty:
        return 0, 0, 0, 0, float('nan')
    g = bet.groupby('race_id').agg(cost=('yen', 'sum'), ret=('ret', 'sum'))
    n, hit = len(g), int((g['ret'] > 0).sum())
    cost, ret = int(g['cost'].sum()), int(g['ret'].sum())
    return n, hit, cost, ret, ret / cost * 100


def line(title, df):
    n, hit, cost, ret, roi = summary(df)
    if n == 0:
        return f'{title}：対象レースなし'
    return (f'{title}：{n}レース　的中{hit}（{hit / n * 100:.0f}%）　'
            f'投資{cost:,}円　払戻{ret:,}円　回収率{roi:.0f}%')


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True)


def commit(msg):
    tmp = '/tmp/keiba_score_tmp.csv'
    shutil.copy(OUT, tmp)
    git('config', 'user.name', 'github-actions')
    git('config', 'user.email', 'actions@github.com')
    for _ in range(3):
        git('rebase', '--abort')
        git('fetch', 'origin', 'main')
        git('checkout', '-f', '-B', 'main', 'origin/main')
        os.makedirs(OUT_DIR, exist_ok=True)
        shutil.copy(tmp, OUT)
        git('add', OUT)
        r = git('commit', '-m', msg)
        if r.returncode != 0 and 'nothing to commit' in (r.stdout + r.stderr):
            return
        if git('push', 'origin', 'main').returncode == 0:
            return
        time.sleep(10)


if not os.path.exists(PRED):
    print(f'{DAY}：予想の記録がないので、採点はありません')
    raise SystemExit(0)

pred = pd.read_csv(PRED, encoding='utf-8-sig', dtype={'race_id': str, 'pair': str})
pred = pred.drop_duplicates(['race_id', 'pair'], keep='last')
rows, lines, pending = [], [], []
for rid, g in pred.groupby('race_id', sort=True):
    name = f'{NAMES.get(rid[4:6], rid[4:6])}{int(rid[-2:])}R'
    g = g[g['yen'] > 0]
    if g.empty:
        lines.append(f'{name}：見送り')
        continue
    pay = wide_payouts(rid)
    if pay is None:
        pending.append(name)
        continue
    marks, ret_sum = [], 0
    for r in g.itertuples(index=False):
        a, b = (int(x) for x in str(r.pair).split('-'))
        p = pay.get(tuple(sorted((a, b))), 0)
        ret = p * int(r.yen) // 100
        ret_sum += ret
        marks.append(f'{a}-{b}' + (f'◎{p}円' if p else '×'))
        rows.append({'race_id': rid, 'pair': f'{a}-{b}', 'yen': int(r.yen), 'payout': p, 'ret': ret,
                     'note': r.note, 'min_to_post': r.min_to_post})
    lines.append(f'{name}：' + '　'.join(marks) + f'　→ 払戻{ret_sum:,}円')

if not rows:
    print('採点できるレースがありませんでした（結果がまだ出ていないかもしれません）')
    raise SystemExit(0)

os.makedirs(OUT_DIR, exist_ok=True)
today = pd.DataFrame(rows)
today.to_csv(OUT, index=False, encoding='utf-8-sig')
commit(f'keiba scores {DAY}')

allsc = pd.concat([pd.read_csv(p, encoding='utf-8-sig', dtype={'race_id': str})
                   for p in sorted(glob.glob(f'{OUT_DIR}/*.csv'))], ignore_index=True)
is10 = lambda d: d[d['note'].astype(str).str.startswith('WD8・WD10')]
ndays = len(glob.glob(f'{OUT_DIR}/*.csv'))

body = [f'@{OWNER} {DAY} のワイド3点の採点です（発走10分前ごろのオッズで出した買い目）。', '',
        '■ 今日の成績',
        '- ' + line('WD8（8頭以上・浦和除外）', today),
        '- ' + line('WD10（10頭以上・浦和除外）', is10(today)), '',
        f'■ これまでの合計（{ndays}日分）',
        '- ' + line('WD8', allsc),
        '- ' + line('WD10', is10(allsc)), '',
        '■ レースごと'] + ['- ' + s for s in lines]
if pending:
    body += ['', '※結果がまだ出ていなかったレース：' + '、'.join(pending) + '（今日の成績には入っていません）']
body += ['', '※数十レースの成績は運で大きくぶれます。判断は数百レースたまってからにしましょう。']
text = '\n'.join(body)
print(text)
r = subprocess.run(['gh', 'issue', 'create', '--title', f'{DAY} ワイド3点 採点結果', '--assignee', OWNER,
                    '--body', text], capture_output=True, text=True)
print(r.stdout or r.stderr[:300])
