"""Exact unit normalisation. Raw strings, bounds, qualifiers and dimensions survive."""
import re
from decimal import Decimal

NUM = r'(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?'
FACTORS = {
 '平方米':('area','平方米',Decimal(1)), '平米':('area','平方米',Decimal(1)),
 '万平方米':('area','平方米',Decimal(10000)), '公顷':('area','平方米',Decimal(10000)),
 '平方千米':('area','平方米',Decimal(1000000)), '亩':('area','平方米',Decimal(2000)/3),
 '米':('length','米',Decimal(1)), '千米':('length','米',Decimal(1000)), '公里':('length','米',Decimal(1000)),
 'm':('length','米',Decimal(1)), 'km':('length','米',Decimal(1000)),
 # 符号形式的面积/体积单位（hm2=公顷）。生态修复、水利类批复大量使用 hm2，
 # 中文单位表里的「公顷」覆盖不到符号写法。与 agent_loop 的备用形态动作配合才有收益：
 # 主干模式要求「度量后缀+数值」，这些文档是「工作内容名+数值+hm2」，仅补单位在主干的
 # 47 份基线里零收益，加上备用形态后才救回指标。
 'hm2':('area','平方米',Decimal(10000)), 'hm²':('area','平方米',Decimal(10000)),
 'm2':('area','平方米',Decimal(1)), 'm²':('area','平方米',Decimal(1)),
 'm3':('volume','立方米',Decimal(1)), 'm³':('volume','立方米',Decimal(1)),
 '厘米':('length','米',Decimal('.01')), '毫米':('length','米',Decimal('.001')),
 '立方米':('volume','立方米',Decimal(1)), '万立方米':('volume','立方米',Decimal(10000)),
 '万元':('money','万元',Decimal(1)), '亿元':('money','万元',Decimal(10000)), '元':('money','万元',Decimal('.0001')),
 '个月':('duration','个月',Decimal(1)), '月':('duration','个月',Decimal(1)), '年':('duration','个月',Decimal(12)),
 '千米/小时':('speed','千米/小时',Decimal(1)), '公里/小时':('speed','千米/小时',Decimal(1)),
 '千瓦':('power','千瓦',Decimal(1)), '兆瓦':('power','千瓦',Decimal(1000)), '万千瓦':('power','千瓦',Decimal(10000)),
 '千瓦时':('energy','千瓦时',Decimal(1)), '兆瓦时':('energy','千瓦时',Decimal(1000)),
 '吨':('mass','吨',Decimal(1)), '万吨':('mass','吨',Decimal(10000)),
 # 吨的符号写法。公开批复里「可削减主要污染物镉2.26t、铬85.50t」这类逐项列举很常见，
 # 缺 t 会让整组数据全丢（实测 seq=13 因此七个污染物削减量一个都没抽到）。
 # T 是同一单位的全角/大写书写（「1T玻璃钢化粪池」）。
 't':('mass','吨',Decimal(1)), 'T':('mass','吨',Decimal(1)),
 '吨/年':('capacity','吨/年',Decimal(1)), '万吨/年':('capacity','吨/年',Decimal(10000)),
 # 处理规模常用吨/日符号写法（「一体化负压泵站200t/d两座」）。
 't/d':('capacity','吨/日',Decimal(1)),
 'MPa':('pressure','MPa',Decimal(1)),
 # ⚠️ 量词表必须与 app/agent_loop.py 的 ALT_UNITS 保持一致。
 # 这两张表曾漂移：ALT_UNITS 里有 座/套/台/株/条/口/井/段/根/孔，这里只有 个/处/盏/层/栋/车道，
 # 于是 agent 备用形态动作里 `if not numeric(value): continue` 把差集的命中**静默全部丢弃**
 # ——`座` 在 47 份语料里出现 150 次，是最高频的量词。tests 里有 ALT_UNITS ⊆ FACTORS 的断言。
 **{u:('count:'+u,u,Decimal(1)) for u in ['个','处','盏','层','栋','车道',
                                        '座','套','台','株','条','口','井','段','根','孔']}}
UNIT='|'.join(re.escape(u) for u in sorted(FACTORS,key=len,reverse=True))
VALUE=rf'(?:约|不超过|不少于|不低于|不高于)?{NUM}(?:(?:{UNIT})?[-—~～至]{NUM})?(?:{UNIT})(?:以上|以下|左右)?'

def numeric(raw):
    s=re.sub(r'\s+','',raw or '').replace('Mpa','MPa')
    # Require boundaries to avoid interpreting a suffix of a negative amount.
    p=rf'(?<![\d.\-])(约|不超过|不少于|不低于|不高于)?({NUM})(?:({UNIT})?[-—~～至]({NUM}))?({UNIT})(以上|以下|左右)?'
    m=re.search(p,s)
    if not m:return None
    pre,a,lu,b,ru,post=m.groups();dim,unit,rf=FACTORS[ru]
    lf=FACTORS[lu][2] if lu else rf
    if lu and FACTORS[lu][0]!=dim:return None
    def dec(v):return Decimal(v.replace(',','').replace('，',''))
    low=dec(a)*lf;high=dec(b)*rf if b else None
    if high is not None and high<low:return None
    return {'number':str(low),'upper':str(high) if high is not None else None,'unit':unit,
        'dimension':dim,'qualifier':(pre or '')+(post or ''),'raw_unit':ru,'approximate_conversion':lu=='亩' or ru=='亩'}

def quantity_key(n):
    if not n:return None
    return (Decimal(n['number']),Decimal(n['upper']) if n['upper'] is not None else None,n['unit'],n['qualifier'])
