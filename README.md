# 批文研析 · 文件结构化智能体 v2

对政府投资项目建议书/立项、可行性研究和初步设计批复进行结构化，形成可定位原文的项目阶段对照表。

## 运行与演示

要求 Python 3.10+。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m app.server
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m app.server
```

打开 `http://127.0.0.1:8765`，上传PDF。无需模型密钥也能运行本地解析模式。

**一键虚构演示**（已激活虚拟环境后）：

```bash
python scripts/demo.py --serve
```

生成两个明确标注为虚构的项目、各三份PDF，通过实际解析流程导入并启动服务，不加载预填预测结果。已有自己的文件时：

```bash
python scripts/demo.py --samples /path/to/pdfs --serve
```

## 功能

- 14项固定字段：发文机关标志、发文字号、标题、印章、印发机关、印发日期、项目名称、项目代码、项目单位、建设内容、建设地点、总投资/匡算/估算/概算、资金来源、建设周期。
- 建设数字指标拆分，保留指标名、单位、约数、上下界和原文证据。
- 项目代码归组、跨阶段比对、差异着色、单文档视图、阶段人工修正。
- 点击结果跳到原文页并高亮；跨页证据可分别查看。
- 人工修订值和证据、修改原因与历史、版本冲突校验；重新提取保留人工确认。
- Excel包含项目对照、单文档结构化、证据索引、待复核事项、修订记录；同时支持JSON导出。
- 本地规则流程和可配置大模型工具，模型结果必须有可匹配的引用原文。

每批最多10份PDF，单份20MB、批次总计23MB以内，单份最多100页。运行结果保存在`data/`，重启后保留。

## 模型配置

复制 `.env.example` 为 `.env`，填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`；视觉模型另填 `VISION_MODEL`。

- BASE_URL为服务根地址，程序追加`/chat/completions`；需支持messages、temperature=0及JSON object响应格式。
- 修改配置后重启，在界面勾选“使用大模型辅助抽取”。勾选后才将批文发送至配置的模型服务，密钥留在后端。
- `VISION_MODEL`用于确认印章候选，可不填。
- 已上传文件在单文档视图点击“重新提取”，会保留人工修订；重复上传默认去重。
- 真实模型服务尚需用自己的凭据完成联调；本项目测试覆盖证据校验、协议代码和失败降级，不冒充真实模型质量测评。

## 中文扫描与印章

原生PDF读取文本坐标；扫描页依赖Tesseract和`chi_sim`中文语言包。Linux可安装：

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
python scripts/check_ocr.py
```

OCR验证脚本生成独立的中文扫描页，检查真实识别结果。缺少OCR能力时明确提示不确定。

本地印章检测只识别红色近圆形候选，需人工或视觉模型复核。未检出显示“无法判断”，不等于“无”。不鉴定印章真实性或法律效力。

## 文档与评测

- [验收记录与限制](VALIDATION.md)
- [字段和证据口径](docs/FIELD_SPEC.md)
- [演示步骤](docs/DEMO.md)
- [公开虚构标注集](evaluation/gold.json)
- [虚构演示评测结果](evaluation/latest-report.json)

公开仓库的标注和评测数据**仅包含虚构演示**。用户材料及其派生标注、预测、导出文件不得提交到公开仓库。私有材料可在本机使用：

```bash
python scripts/evaluate.py --samples /path/to/private/pdfs --gold /path/to/private/gold.json --out /path/to/private/results
```

运行公开演示评测与自动化测试：

```bash
python scripts/demo.py --data /tmp/approval-demo
python scripts/evaluate.py --samples /tmp/approval-demo/demo-source
python -m unittest discover -s tests -v
```

历史材料回归测试需要本地`SAMPLE_DIR`，不设置时跳过相关测试。公开CI运行无私有材料的测试、OCR验证和虚构演示。标注样例成绩不代表未知文档泛化准确率。

## 部署

```bash
docker build -t approval-structuring-agent .
docker run --rm -p 127.0.0.1:8765:8765 -v approval-data:/app/data approval-structuring-agent
```

使用模型时添加`--env-file .env`。Dockerfile含中文OCR；镜像构建需在有Docker的环境验收。本版本为本机单用户工具，无公网账号权限体系，不应直接暴露公网。

Python标准库HTTP服务 + PyMuPDF + NumPy/Pillow + openpyxl；原生JavaScript前端，无需Node构建。可选`npm run dev`用于统一开发入口，仍启动Python服务。

核心目录：`app/`解析/比对/复核与服务；`app/static/`页面；`scripts/`演示和评测；`evaluation/`虚构标注；`tests/`测试。源码仓库为 https://github.com/deyye/approval-structuring-agent 。
