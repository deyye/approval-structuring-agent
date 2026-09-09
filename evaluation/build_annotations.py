"""Public ground truth for the explicitly fictional scripts/demo.py documents.
This file contains no data from user-uploaded PDFs.
"""
import json
from pathlib import Path
KEYS=['发文机关标志','发文字号','标题','印章','印发机关','印发日期','项目名称','项目代码','项目单位','建设内容','建设地点','总投资/匡算/估算/概算','资金来源','建设周期']
documents=[]
for project,code,areas in [('演示文体中心项目','2609-330100-04-01-100001',[4700,3990,3580]),('演示连接线工程项目','2609-330100-04-01-100002',[200000,136930,136929.6])]:
    for i,stage in enumerate(['项目建议书','可行性研究报告','初步设计']):
        vals=['演示市发展和改革局文件',f'演发改投〔2026〕{101+i}号',f'关于{project}{stage}的批复','有','演示市发展和改革局办公室',f'2026年{i+1}月1日',project,code,'演示市建设有限公司',f'项目总用地面积{areas[i]}平方米，总建筑面积{areas[i]}平方米。主要建设公共服务设施及相关配套工程。','项目位于演示园区。',f'项目估算总投资{2998-i*10}万元','建设资金由业主自筹解决',f'{60 if i==0 else 24}个月']
        ps=[[1],[1],[1],[2],[2],[2],[1],[2],[1],[1],[1],[1],[1],[1]]
        documents.append({'filename':f'{project}-{stage}.pdf','project':project,'stage':['建议书/立项','可行性研究','初步设计'][i],'fields':{k:{'value':v,'pages':p} for k,v,p in zip(KEYS,vals,ps)},'construction_metrics':[{'name':n,'value':f'{areas[i]}平方米','pages':[1]} for n in ['总用地面积','总建筑面积']]})
Path(__file__).with_name('gold.json').write_text(json.dumps({'version':1,'annotation_method':'按虚构演示材料生成规范独立定义预期值，不含用户原始材料。','scope':'仅两个虚构演示项目的14项字段与主要建设内容数量指标，不能代表真实业务准确率。','documents':documents},ensure_ascii=False,indent=2),encoding='utf-8')
