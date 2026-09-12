# -*- coding: utf-8 -*-
"""一次性维护：用库内已存的标题/正文，回填缺失的文号（不联网）。

背景：文号有两种形态——
  A) 机关代字号：发改投资规〔2025〕1728号、国办发〔2026〕24号
  B) 令号/公告号：2026年第44号令、2026年第3号公告
采集器原先只认 A。发改委令与部门公告用的是 B，且编号写在**标题末尾**，
于是 19 条发改委令（重要类目）全都没有文号。
文号是去重的第二键，漏抽会削弱去重能力，所以必须补齐。

B 形态所需的两个输入（标题、正文）库里都有，故无需重新抓取。
幂等：只填空的，不覆盖已有值。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collector.extract import find_doc_number   # noqa: E402

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'out', 'policies.db')

c = sqlite3.connect(DB)
rows = c.execute("SELECT id, title, content, doc_number FROM policies "
                 "WHERE doc_number IS NULL OR doc_number=''").fetchall()
filled, samples = 0, []
for pid, title, content, _ in rows:
    num = find_doc_number(title or '', '', (content or '')[:1200])
    if not num:
        continue
    c.execute('UPDATE policies SET doc_number=? WHERE id=?', (num, pid))
    filled += 1
    if len(samples) < 8:
        samples.append((num, (title or '')[:52]))
c.commit()

total = c.execute('SELECT COUNT(*) FROM policies').fetchone()[0]
have = c.execute("SELECT COUNT(*) FROM policies WHERE doc_number<>''").fetchone()[0]
print('回填 %d 条。当前文号覆盖：%d/%d (%.0f%%)' % (filled, have, total, 100.0 * have / total))
for n, t in samples:
    print('   · %-22s %s' % (n, t))
print('\n按体裁：')
for dt, t, w in c.execute("SELECT doc_type, COUNT(*), SUM(CASE WHEN doc_number<>'' THEN 1 ELSE 0 END) "
                          "FROM policies GROUP BY doc_type"):
    print('   %-8s %d/%d (%.0f%%)' % (dt, w, t, 100.0 * w / t))
