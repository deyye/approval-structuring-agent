# -*- coding: utf-8 -*-
"""一次性维护：把库内旧 source_id 迁移到唯一源 ID。

背景：源 ID 原先是"域名首段 + prov_" 推出来的，于是
fgw.beijing.gov.cn / fgw.shanxi.gov.cn / fgw.ln.gov.cn …
全部落成同一个 'prov_fgw'，'prov_fzggw' 也覆盖了 4 个省。
后果：--sources 选不中单个省、按源统计互相覆盖、库内无法按省归集。

修复后省份码改为显式维护（PROVINCES 表第二列），本脚本按 region 回填。
可重复执行（幂等）。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector.sources import PROVINCES, SOURCES   # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'out', 'policies.db')
by_region = {r: 'prov_' + c for r, c, _ in PROVINCES}
known = {s['id'] for s in SOURCES}

c = sqlite3.connect(DB)
rows = c.execute("SELECT DISTINCT source_id, level, region FROM policies").fetchall()
fix = 0
for sid, level, region in rows:
    if sid in known:
        continue
    new = by_region.get(region) if level == '省' else None
    if not new:
        print('  ! 无法归属：%s / %s / %s —— 保留原值' % (sid, level, region))
        continue
    n = c.execute('UPDATE policies SET source_id=? WHERE source_id=? AND region=?',
                  (new, sid, region)).rowcount
    print('  %-12s + %-12s → %-18s %d 条' % (sid, region, new, n))
    fix += n
c.commit()
print('\n迁移 %d 条。当前分布：' % fix)
for r in c.execute('SELECT source_id, region, COUNT(*) n FROM policies WHERE level="省" '
                   'GROUP BY source_id ORDER BY n DESC'):
    print('  %-18s %-14s %d' % (r[0], r[1], r[2]))
