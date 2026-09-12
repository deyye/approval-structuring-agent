# -*- coding: utf-8 -*-
"""一次性维护：清掉库内不符合收录口径的噪声记录。

来源：解析器改成"整页扫 <a>"后范围变宽，把站点的侧栏/导航也收进来了——
典型是吉林省发改委「领导分工」页（/jggk/ldfg/）的 9 条人名职务，
被当成政策文件入了库。修解析器后新数据不会再进来，但**已入库的不会自动消失**，
所以需要这一步。

判据与 collector/sources.py 保持一致（同一套规则，避免两处口径漂移）。
带 --dry-run 只打印不删除；幂等。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector.sources import (URL_SEGMENT_DENY, _is_section_landing,  # noqa: E402
                              _noise_title)

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'out', 'policies.db')
DRY = '--dry-run' in sys.argv

c = sqlite3.connect(DB)
rows = c.execute('SELECT id, title, url, source_id, region FROM policies').fetchall()
kill = []
for pid, title, url, sid, region in rows:
    low = (url or '').lower()
    reason = None
    if any(seg in low for seg in URL_SEGMENT_DENY):
        reason = 'URL 落在非政策栏目（%s）' % next(s for s in URL_SEGMENT_DENY if s in low)
    elif _is_section_landing(low):
        reason = 'URL 是栏目落地页（父目录非文章 ID），不是文章页'
    elif _noise_title(title or ''):
        reason = '标题是导航/人事页'
    if reason:
        kill.append((pid, title, region, reason))

print('待清理 %d 条（库内共 %d 条）%s' % (len(kill), len(rows), '  [dry-run，未删除]' if DRY else ''))
for pid, title, region, reason in kill:
    print('   %-12s %-40s %s' % (region or '', (title or '')[:40], reason))
if not DRY:
    c.executemany('DELETE FROM policies WHERE id=?', [(k[0],) for k in kill])
    c.commit()
    print('\n已删除 %d 条。剩余 %d 条。' % (len(kill), c.execute('SELECT COUNT(*) FROM policies').fetchone()[0]))
