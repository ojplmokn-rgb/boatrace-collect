# 前日までの公式の競走成績ファイル（着順・払戻）を保存する
import requests, subprocess, datetime as dt
from pathlib import Path
JST = dt.timezone(dt.timedelta(hours=9))
for k in range(1, 4):
    d = dt.datetime.now(JST) - dt.timedelta(days=k)
    f = Path('data') / d.strftime('%Y%m%d') / ('k%s.lzh' % d.strftime('%y%m%d'))
    if f.exists():
        continue
    try:
        r = requests.get('https://www1.mbrace.or.jp/od2/K/%s/%s' % (d.strftime('%Y%m'), f.name), timeout=30)
    except Exception as e:
        print('エラー', e)
        continue
    if r.status_code == 200 and len(r.content) > 1000:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.content)
        print('保存', f, len(r.content))
subprocess.run('git config user.name bot && git config user.email bot@example.com && git add data && '
               '(git diff --cached --quiet || (git commit -q -m results && git pull -q --rebase && git push -q))',
               shell=True)
