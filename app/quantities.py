"""Exact unit normalisation. Raw strings, bounds, qualifiers and dimensions survive."""
import re
from decimal import Decimal

NUM = r'(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?'
FACTORS = {
 '平方米':('area','平方米',Decimal(1)), '平米':('area','平方米',Decimal(1)),
 '万平方米':('area','平方米',Decimal(10000)), '公顷':('area','平方米',Decimal(10000)),
 '平方千米':('area','平方米',Decimal(1000000)), '亩':('area','平方米',Decimal(2000)/3),
 '米':('length','米',Decimal(1)), '千米':('length','米',Decimal(1000)), '公里':('length','米',Decimal(1000)),
 '厘米':('length','米',Decimal('.01')), '毫米':('length','米',Decimal('.001')),
 '立方米':('volume','立方米',Decimal(1)), '万立方米':('volume','立方米',Decimal(10000)),
 '万元':('money','万元',Decimal(1)), '亿元':('money','万元',Decimal(10000)), '元':('money','万元',Decimal('.0001')),
 '个月':('duration','个月',Decimal(1)), '月':('duration','个月',Decimal(1)), '年':('duration','个月',Decimal(12)),
 '千米/小时':('speed','千米/小时',Decimal(1)), '公里/小时':('speed','千米/小时',Decimal(1)),
 '千瓦':('power','千瓦',Decimal(1)), '兆瓦':('power','千瓦',Decimal(1000)), '万千瓦':('power','千瓦',Decimal(10000)),
 '千瓦时':('energy','千瓦时',Decimal(1)), '兆瓦时':('energy','千瓦时',Decimal(1000)),
 '吨':('mass','吨',Decimal(1)), '万吨':('mass','吨',Decimal(10000)),
 '吨/年':('capacity','吨/年',Decimal(1)), '万吨/年':('capacity','吨/年',Decimal(10000)),
 'MPa':('pressure','MPa',Decimal(1)),
 **{u:('count:'+u,u,Decimal(1)) for u in ['个','处','盏','层','栋','车道']}}
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
