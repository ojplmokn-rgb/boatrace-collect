# 南関東 ワイド3点の自動予想と通知（GitHub Actions用）
# 各レースの発走10分前ごろのオッズでワイド3点を計算し、GitHubの通知（Issueのコメント）で知らせます。
import os, re, time, json, ast, shutil, subprocess, functools
import datetime as dt

print = functools.partial(print, flush=True)
OWNER = 'ojplmokn-rgb'
PRED_MIN = 10          # 発走の何分前に予想するか
JST = dt.timezone(dt.timedelta(hours=9))


def now():
    return dt.datetime.now(JST)


DAY = now().date()
STOP = now().replace(hour=15, minute=10, second=0) if now().hour < 15 else now().replace(hour=21, minute=40, second=0)
LABEL = '昼' if now().hour < 15 else '夜'

# ---------- 1. ドライブからモデルとデータを取ってくる ----------
import gdown
DRIVE = {
    'keiba_nankan/results.csv': '13LBftGCcOa1Q_Ls4__W58cqnv4B0-eL_',
    'keiba_nankan/races.csv': '1aUT_5Le8Rn7QpdUq1gLL5PRWGhlhZScq',
    'keiba_nankan/payouts.csv': '1XzsOvyNn3IDAEPdjrljWHGDM1tDr-FBH',
    'keiba_nankan/dates_done.txt': '1jF4X0mViZdAcPICPKNlyT8YjMj-BkfQ5',
    'keiba_nankan/umaren_frozen/m_win.txt': '1TV6Uu_Bc1jvwE5F6kT4hcG5fYwJgqlHO',
    'keiba_nankan/umaren_frozen/m_top2.txt': '1shhW68hm0ZZ3tGXio8FHBIwSVE1ulo7A',
    'keiba_nankan/umaren_frozen/info.json': '13QZm2-OoFjxLrchrCRKT1naTyyD6bssr',
    'v32_code.ipynb': '12uS8ux9LvpUv3p14BBE_GesDxkPe3fwY',
}
for path, fid in DRIVE.items():
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    out = gdown.download(id=fid, output=path, quiet=True)
    if not out or not os.path.exists(path):
        raise SystemExit(f'ドライブから取れませんでした：{path}（keiba_nankanフォルダの共有設定を確認してください）')
print('ドライブからモデルとデータを取ってきました')

# ---------- 2. v3.2のコードから必要な部分だけ読み込む ----------
os.environ['KEIBA_BASE'] = '.'
nb = json.load(open('v32_code.ipynb', encoding='utf-8'))
src = None
for c in nb.get('cells', []):
    if c.get('cell_type') != 'code':
        continue
    s = ''.join(c['source']) if isinstance(c['source'], list) else c['source']
    if 'CODE_VERSION' in s and "REGION = 'NANKAN'" in s and 'def race_tickets_v3' in s and 'まとめ版' not in s:
        src = s
if src is None:
    raise SystemExit('v3.2のコードが見つかりませんでした')
parts = re.split(r'(?m)^# %% セル(\d+)', src)
cells = {int(parts[i]): '#' + parts[i + 1] for i in range(1, len(parts) - 1, 2)}
for k in range(1, 6):
    if k in cells:
        exec(cells[k], globals())
for k in (6, 7):
    if k not in cells:
        continue
    for node in ast.parse(cells[k]).body:
        ok = isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))
        if isinstance(node, ast.Assign):
            ok = all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets)
        if not ok:
            continue
        try:
            exec(compile(ast.Module(body=[node], type_ignores=[]), f'cell{k}', 'exec'), globals())
        except Exception:
            pass
res, races, pay = load_all()
print(f'データ：{len(races)}レース分')
TODAY = DAY


# ---------- 3. v4の材料と馬連モデル ----------
def rate_before(df, keys, target, prior_n):
    t = df[keys + ['date']].copy()
    t['_v'] = df[target].fillna(0).values
    t['_c'] = df[target].notna().astype(int).values
    agg = (t.groupby(keys + ['date'], sort=False)[['_v', '_c']].sum()
             .reset_index().sort_values(keys + ['date']))
    gb = agg.groupby(keys, sort=False)
    agg['_cs'] = gb['_v'].cumsum() - agg['_v']
    agg['_cc'] = gb['_c'].cumsum() - agg['_c']
    agg['_r'] = agg['_cs'] / (agg['_cc'] + prior_n)
    out = t[keys + ['date']].merge(agg[keys + ['date', '_r', '_cc']], on=keys + ['date'], how='left')
    return out['_r'].values, out['_cc'].values


def add_v4_features(df):
    df = df.copy()
    df['top3_ex'] = df['top3'] - 3 / df['n_runners']
    df['jockey_place_ex'], df['jockey_place_n'] = rate_before(df, ['jockey_id', 'place'], 'top3_ex', 20)
    df['jockey_course_ex'], _ = rate_before(df, ['jockey_id', 'place', 'distance'], 'top3_ex', 20)
    df['jockey_horse_ex'], df['jockey_horse_n'] = rate_before(df, ['jockey_id', 'horse_id'], 'top3_ex', 3)
    df['waku_bias'], _ = rate_before(df, ['place', 'distance', 'waku'], 'top3_ex', 50)
    df['umaban_ratio'] = df['umaban'] / df['n_runners']
    df['race_level'] = df.groupby('race_id')['career_top3_rate'].transform('mean')
    df['fin_ratio'] = df['finish'] / df['n_runners']
    g = df.groupby('horse_id', sort=False)
    df['prev1_race_level'] = g['race_level'].shift(1)
    df['level_change'] = df['race_level'] - df['prev1_race_level']
    f = [g['fin_ratio'].shift(k) for k in (1, 2, 3)]
    df['form_trend'] = f[0] - pd.concat(f[1:], axis=1).mean(axis=1)
    s = [g['speed'].shift(k) for k in (1, 2, 3)]
    df['speed_trend'] = s[0] - pd.concat(s[1:], axis=1).mean(axis=1)
    b = [g['body_weight'].shift(k) for k in (1, 2, 3)]
    df['weight_vs_avg'] = df['body_weight'] - pd.concat(b, axis=1).mean(axis=1)
    df['prev1_pop_gap'] = g['popularity'].shift(1) - g['finish'].shift(1)
    return df


V4_NEW = ['jockey_place_ex', 'jockey_place_n', 'jockey_course_ex', 'jockey_horse_ex', 'jockey_horse_n',
          'waku_bias', 'umaban_ratio', 'race_level', 'prev1_race_level', 'level_change',
          'form_trend', 'speed_trend', 'weight_vs_avg', 'prev1_pop_gap']
U_FEATS = FEATURES_V3 + V4_NEW + MKT_FEATURES
MU, INFOU = load_models(['win', 'top2'], f'{SAVE_DIR}/umaren_frozen')
K_U = INFOU['k']


def pair_probs(nums, w):
    w = np.asarray(w, float)
    w = w / w.sum()
    out = {}
    for i in range(len(nums)):
        for j in range(i + 1, len(nums)):
            p = w[i] * w[j] / max(1 - w[i], 1e-9) + w[j] * w[i] / max(1 - w[j], 1e-9)
            out[tuple(sorted((int(nums[i]), int(nums[j]))))] = p
    return out


# ---------- 4. 出馬表・発走時刻・オッズ ----------
def fetch_entry2(rid):
    page = get(ENTRY_URL.format(rid))
    if not page or 'HorseList' not in page:
        return None, None, None
    soup = BeautifulSoup(page, 'html.parser')
    rows = []
    for tr in soup.select('tr.HorseList'):
        if '取消' in tr.get_text() or '除外' in tr.get_text():
            continue
        um = tr.find('td', class_=re.compile(r'^Umaban'))
        wk = tr.find('td', class_=re.compile(r'^Waku'))
        a_h = tr.find('a', href=re.compile(r'/horse/'))
        a_j = tr.find('a', href=re.compile(r'/jockey/'))
        ba = tr.find('td', class_='Barei')
        kin = ba.find_next_sibling('td') if ba else None
        m_h = re.search(r'/horse/(\w+)', a_h['href']) if a_h else None
        m_j = re.search(r'/jockey/(?:result/recent/)?(\w+)', a_j['href']) if a_j else None
        sa = ba.get_text(strip=True) if ba else ''
        rows.append({'race_id': rid,
                     'umaban': to_num(um.get_text(strip=True)) if um else np.nan,
                     'waku': to_num(wk.get_text(strip=True)) if wk else np.nan,
                     'horse_id': m_h.group(1) if m_h else None,
                     'horse_name': a_h.get_text(strip=True) if a_h else '',
                     'jockey_id': m_j.group(1) if m_j else None,
                     'sex': sa[:1], 'age': to_num(sa[1:]),
                     'kinryo': to_num(kin.get_text(strip=True)) if kin else np.nan})
    texts = [soup.find(class_=c) for c in ['RaceName', 'RaceData01', 'RaceData02']]
    itext = ' '.join(t.get_text(' ', strip=True) for t in texts if t is not None)
    tn = nfkc(itext)
    m_c = re.search(r'(芝|ダ|障)(\d{3,4})m', tn)
    m_g = re.search(r'馬場\s*:\s*(良|稍|重|不)', tn)
    m_p = re.search(r'本賞金\s*:\s*([\d.,]+)', tn)
    m_t = re.search(r'(\d{1,2}):(\d{2})\s*発走', tn)
    entry = pd.DataFrame(rows)
    if entry.empty or m_t is None:
        return None, None, None
    entry = entry[entry['umaban'].notna()].copy()
    entry['umaban'] = entry['umaban'].astype(int)
    race = {'race_id': rid, 'date': DAY.isoformat(), 'place': rid[4:6],
            'surface_code': SURFACE.get(m_c.group(1)) if m_c else np.nan,
            'distance': to_num(m_c.group(2)) if m_c else np.nan,
            'going_code': GOING.get(m_g.group(1)) if m_g else np.nan,
            'class_code': class_code(itext), 'handicap': int('ハンデ' in itext),
            'n_runners': len(entry),
            'prize1': to_num(m_p.group(1).split(',')[0]) if m_p else np.nan}
    post = dt.datetime(DAY.year, DAY.month, DAY.day, int(m_t.group(1)), int(m_t.group(2)), tzinfo=JST)
    return entry, race, post


ODDS_URL = 'https://nar.netkeiba.com/api/api_get_nar_odds.html?race_id={}&type=1&action=update'


def fetch_odds(rid):
    try:
        t = get(ODDS_URL.format(rid))
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


# ---------- 5. 通知と保存 ----------
def gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True)


ISSUE = None


def notify(text):
    global ISSUE
    if ISSUE is None:
        r = gh('issue', 'create', '--title', f'{DAY} ワイド3点予想（{LABEL}）', '--assignee', OWNER,
               '--body', f'@{OWNER} {DAY}（{LABEL}）のワイド3点予想です。各レースの発走{PRED_MIN}分前ごろのオッズで計算し、'
                         'このあとコメントで届きます。\n\n※本番の検証前のため、参考として見てください。')
        m = re.search(r'/issues/(\d+)', r.stdout)
        if not m:
            print('通知を作れませんでした：', r.stderr[:200])
            return
        ISSUE = m.group(1)
    r = gh('issue', 'comment', ISSUE, '--body', f'@{OWNER} ' + text)
    if r.returncode != 0:
        print('コメントできませんでした：', r.stderr[:200])


def git(*args):
    return subprocess.run(['git', *args], capture_output=True, text=True)


PRED_DIR = 'keiba_preds'
PRED_OUT = f'{PRED_DIR}/{DAY}.csv'


def save_pred(rows):
    os.makedirs(PRED_DIR, exist_ok=True)
    pd.DataFrame(rows).to_csv(PRED_OUT, mode='a', index=False,
                              header=not os.path.exists(PRED_OUT), encoding='utf-8-sig')


def commit(msg):
    if not os.path.exists(PRED_OUT):
        return
    tmp = '/tmp/keiba_preds_tmp.csv'
    shutil.copy(PRED_OUT, tmp)
    git('config', 'user.name', 'github-actions')
    git('config', 'user.email', 'actions@github.com')
    for _ in range(3):
        git('rebase', '--abort')
        git('fetch', 'origin', 'main')
        git('checkout', '-f', '-B', 'main', 'origin/main')
        os.makedirs(PRED_DIR, exist_ok=True)
        shutil.copy(tmp, PRED_OUT)
        git('add', PRED_OUT)
        r = git('commit', '-m', msg)
        if r.returncode != 0 and 'nothing to commit' in (r.stdout + r.stderr):
            return
        if git('push', 'origin', 'main').returncode == 0:
            return
        time.sleep(10)


# ---------- 6. 今日のレースを集め、材料を先に作っておく ----------
NAMES = PLACE_NAMES
ents, rcs, posts = [], {}, {}
for code in ['43', '44', '45']:
    for r in range(1, 13):
        rid = f'{DAY.year}{code}{DAY:%m%d}{r:02d}'
        entry, race, post = fetch_entry2(rid)
        if entry is None:
            if r == 1:
                break
            continue
        if post > STOP + dt.timedelta(minutes=1) or post < now() + dt.timedelta(minutes=3):
            continue
        e = entry.copy()
        for c in ['finish', 'time_sec', 'pos_first', 'pos_last', 'agari',
                  'odds', 'popularity', 'body_weight', 'body_diff']:
            e[c] = np.nan
        e['is_entry'] = True
        ents.append(e)
        rcs[rid] = race
        posts[rid] = post
if not rcs:
    print(f'{DAY}（{LABEL}）：これから予想できるレースはありません（浦和は対象外）')
    raise SystemExit(0)
for rid in sorted(posts, key=posts.get):
    print(f'  {NAMES.get(rid[4:6])}{int(rid[-2:])}R {posts[rid]:%H:%M}発走')
print('材料を作っています…')
ids = list(rcs)
allres = pd.concat([res[~res['race_id'].isin(ids)]] + ents, ignore_index=True)
allraces = pd.concat([races[~races['race_id'].isin(ids)], pd.DataFrame(list(rcs.values()))], ignore_index=True)
FT = add_v4_features(build_features(allres, allraces))
FT = FT[FT['race_id'].isin(ids)].copy()
print('準備ができました。発走の約10分前に予想します。')


# ---------- 7. 発走10分前ごろに予想して通知 ----------
def predict(rid):
    rno = int(rid[-2:])
    place = NAMES.get(rid[4:6], rid[4:6])
    head = f'{place}{rno}R（{posts[rid]:%H:%M}発走）'
    od = fetch_odds(rid)
    f = FT[FT['race_id'] == rid].copy()
    f['odds'] = f['umaban'].astype(int).map(od)
    f = f[f['odds'].notna()].copy()
    if len(f) < 3:
        print(head + '：オッズが取れませんでした')
        return
    f['popularity'] = f['odds'].rank(method='first')
    mins = (posts[rid] - now()).total_seconds() / 60
    n, fo = len(f), float(f['odds'].min())
    stop = (['8頭未満'] if n < 8 else []) + (['1番人気が1倍台'] if fo < 2.0 else [])
    if stop:
        msg = f'{head}　{n}頭・1番人気{fo:.1f}倍 → 見送り（' + '、'.join(stop) + '）'
        notify(msg)
        print(msg)
        save_pred([{'race_id': rid, 'min_to_post': round(mins, 1), 'pair': '', 'yen': 0, 'ev': np.nan, 'note': '見送り'}])
        return
    f = add_mkt_features(f)
    raw = predict_lgb(MU['win'], f[U_FEATS])
    f['pw'] = raw / raw.sum()
    nums = f['umaban'].astype(int).values
    names = dict(zip(nums, f['horse_name']))
    pm = pair_probs(nums, f['p_market'].values)
    pmod = pair_probs(nums, f['pw'].values)
    ev = {t: pmod[t] * K_U / pm[t] for t in pmod if pm[t] > 0}
    top10 = sorted(pmod, key=pmod.get, reverse=True)[:10]
    best = sorted(top10, key=lambda t: ev.get(t, 0), reverse=True)[:3]
    tag = 'WD8・WD10' if n >= 10 else 'WD8だけ（10頭未満）'
    lines = [f'{head}　発走{mins:.0f}分前のオッズ　{n}頭・1番人気{fo:.1f}倍　対象：{tag}']
    rows = []
    for t, y in zip(best, [1500, 1000, 500]):
        a, b = t
        lines.append(f'- ワイド **{a}-{b}**　{y}円　{names.get(a, "")}・{names.get(b, "")}　割安さ{ev[t]:.2f}')
        rows.append({'race_id': rid, 'min_to_post': round(mins, 1), 'pair': f'{a}-{b}', 'yen': y,
                     'ev': round(ev[t], 3), 'note': tag})
    msg = '\n'.join(lines)
    notify(msg)
    print(msg)
    save_pred(rows)


done = set()
last_commit = now()
while now() < STOP:
    left = [r for r in ids if r not in done and (posts[r] - now()).total_seconds() / 60 > 2]
    if not left:
        break
    for rid in sorted(left, key=posts.get):
        mins = (posts[rid] - now()).total_seconds() / 60
        if mins <= PRED_MIN:
            try:
                predict(rid)
            except Exception as e:
                print(f'{rid}：予想の途中でエラー：{e}')
            done.add(rid)
    if (now() - last_commit).total_seconds() > 30 * 60:
        commit(f'keiba preds {DAY} {now():%H:%M}')
        last_commit = now()
    time.sleep(30)
commit(f'keiba preds {DAY} final {now():%H:%M}')
print('今日の予想は終わりました')
