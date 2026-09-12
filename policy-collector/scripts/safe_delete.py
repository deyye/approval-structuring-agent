# -*- coding: utf-8 -*-
"""安全删除工具：任何对 policies 表的批量删除都走这里。

为什么需要它
------------
本轮我在维护库时**两次**因为删除语句写错造成数据损失：

1. 清理规则写成"URL 以 index 结尾即栏目页"，误删 59 条
   （辽宁等站的文章地址是 `/fgw/zc/zcjd/<文章ID>/index.shtml`）。
2. 用 `url LIKE '%gov.cn%'` 想删 2 条 gov.cn 记录，结果这个模式匹配**所有** `.gov.cn` 域名，
   一次性删掉了全部 1102 条省级记录——只剩国家级。

两次都不是"判断错了"，而是**删除动作本身没有护栏**。这个脚本把护栏固化成强制步骤：

  ① 先 `--count` 只数不删，把数量和样本打出来
  ② 删除前**自动把待删行整行导出成 JSON 备份**（可还原）
  ③ 必须显式给出 `--max N`：实际条数超过 N 就拒绝执行（防手滑的通配符）
  ④ 必须显式给 `--yes` 才真删，否则只演练

用法::

    # 第一步：只数（不出错也看不出风险的写法，先跑这个）
    python scripts/safe_delete.py --where "url LIKE '%gov.cn%' AND source_id LIKE 'prov_%'"

    # 第二步：确认数量合理，再删
    python scripts/safe_delete.py --where "..." --max 10 --yes

备份落在 out/deleted/ 下，文件名带时间戳；还原时读该 JSON 重新 insert 即可。
"""
import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'out', 'policies.db')
BACKUP_DIR = os.path.join(ROOT, 'out', 'deleted')


def main():
    ap = argparse.ArgumentParser(description='对 policies 表执行带护栏的批量删除')
    ap.add_argument('--where', required=True, help='SQL 的 WHERE 子句（不含 WHERE 关键字）')
    ap.add_argument('--max', type=int, default=0,
                    help='允许删除的最大条数；实际超过即拒绝（防通配符手滑）。不传则只演练')
    ap.add_argument('--yes', action='store_true', help='确认执行删除（不传则只演练）')
    ap.add_argument('--sample', type=int, default=10, help='打印样本条数')
    args = ap.parse_args()

    if not os.path.exists(DB):
        print('找不到数据库 %s' % DB)
        return 1

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    n = conn.execute('SELECT COUNT(*) FROM policies WHERE ' + args.where).fetchone()[0]
    print('=' * 72)
    print('WHERE : %s' % args.where)
    print('命中  : %d 条' % n)
    print('=' * 72)

    if n == 0:
        print('没有命中，无需处理。')
        return 0

    rows = conn.execute('SELECT * FROM policies WHERE ' + args.where).fetchall()
    print('样本（前 %d 条）：' % min(args.sample, n))
    for r in rows[:args.sample]:
        print('  [%-12s] %s' % (r['region'] or r['level'] or '', (r['title'] or '')[:56]))
        print('        %s' % (r['url'] or '')[:92])
    if n > args.sample:
        print('  … 其余 %d 条' % (n - args.sample))

    # 步③：数量护栏
    if not args.max:
        print('\n未提供 --max，仅演练。确认上面的数量与样本无误后，'
              '再加 --max %d --yes 执行。' % n)
        return 0
    if n > args.max:
        print('\n✗ 拒绝执行：命中 %d 条，超过 --max %d。' % (n, args.max))
        print('  这说明 WHERE 写得比预期宽（通配符最容易出这种事）。')
        print('  请先收紧条件，或确认真要删这么多再调大 --max。')
        return 2

    if not args.yes:
        print('\n演练结束（未删除）。加 --yes 才会真正执行。')
        return 0

    # 步②：删前备份
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    bpath = os.path.join(BACKUP_DIR, 'deleted-%s.json' % stamp)
    payload = {
        'deleted_at': stamp,
        'where': args.where,
        'count': n,
        'rows': [dict(r) for r in rows],
    }
    with open(bpath, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print('\n已备份 %d 条到 %s' % (n, os.path.relpath(bpath, ROOT)))

    conn.executemany('DELETE FROM policies WHERE id=?', [(r['id'],) for r in rows])
    conn.commit()
    left = conn.execute('SELECT COUNT(*) FROM policies').fetchone()[0]
    print('已删除 %d 条；库内剩余 %d 条。' % (n, left))
    print('如需还原：读取上面那份 JSON，逐行 insert 回 policies 表即可。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
