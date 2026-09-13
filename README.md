# 批文研析 · 文件结构化智能体 v2

对政府投资项目建议书/立项、可行性研究和初步设计批复进行结构化，形成可定位原文的项目阶段对照表。

> **本仓库包含两个独立工具**，各自有独立的说明与依赖，互不耦合：
>
> | 目录 | 工具 | 说明 |
> |---|---|---|
> | 仓库根 | 批文研析 · 文件结构化智能体 | 批复文件结构化与跨阶段比对，即本文档 |
> | [`policy-collector/`](policy-collector/README.md) | 投资项目政策文件采集器 | 采集政府网站政策专栏、研判是否属投资项目类政策、四类归集并落地数据库 |

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
- 空值分两种：**原文未载明**（灰色，已检索全文核实）与**未提取到**（红色，原文有线索）。
- 比对识别两类「不算变化」：书写精度或约数造成的微小差异；以及**数值相同但各阶段名称不同的疑似同一指标**，可一键统一名称（两行合并）或确认不合并。
- **核对进度**：每个项目显示还剩多少项待人工确认，清零即「已审完」；待办清单可逐条点击直达对应值。
- **自检循环**：自检发现异常时自动补救（放宽章节判据、换备用识别形态、定点回读），并把每一步的判断过程显示出来；补救值一律标「需人工确认」，来源单独标注。
- 点击结果跳到原文页并高亮；跨页证据可分别查看。
- 人工修订值和证据、修改原因与历史、版本冲突校验；重新提取保留人工确认。
- Excel包含项目对照、单文档结构化（含**来源**与**缺失原因**列）、证据索引、待复核事项、修订记录；同时支持JSON导出。
- 本地规则流程和可配置大模型工具，模型结果必须有可匹配的引用原文。

每批最多10份PDF，单份20MB、批次总计23MB以内，单份最多100页。运行结果保存在`data/`，重启后保留。

## 模型配置

模型随时可替换，三条路径任选，**优先级：界面保存 > `.env` > 环境变量**：

1. **界面配置（推荐，保存后立即生效，不用重启）**：左侧「模型设置」里选厂商预设或直接填服务地址、密钥、模型名，点「保存配置」。任何 OpenAI 兼容服务都能用，随时在 DeepSeek、通义千问、智谱、月之暗面、硅基流动、OpenAI 之间切换。界面保存的值存在 `data/model_config.json`。
2. **后端 `.env`**：复制 `.env.example` 为 `.env`，填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，重启服务生效。
3. **环境变量**：与 `.env` 同键名，适合容器化部署。

界面上的「恢复默认」会清除界面保存的配置，回落到 `.env`／环境变量。

- 密钥**只进不出**：界面从不回显明文，只显示是否已配置及末四位；未配置时「使用大模型辅助抽取」与测试按钮为置灰状态。
- `LLM_BASE_URL` 为服务根地址，程序追加 `/chat/completions`；需支持 messages、temperature=0 及 JSON object 响应格式。`VISION_MODEL` 用于确认印章候选，可留空。
- 勾选「使用大模型辅助抽取」后才将批文发送至该服务，密钥留在后端。
- 已上传文件在单文档视图点击「重新提取」，会保留人工修订；重复上传默认去重。
- 点「测试文字模型」或运行 `python scripts/check_model.py` 检查真实连接，只发送固定测试内容；视觉模型可用 `--vision`。实际凭据仅在本机配置。
- 支持可配置超时、限流重试、JSON模式和密钥文件；详见部署文档。

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
cp .env.example .env
docker compose up -d --build --wait
```

包含中文 OCR、非 root 运行、健康检查和数据持久化。模型配置与诊断、密钥挂载、服务器访问、备份、故障处理详见 [外部模型与 Docker 部署](docs/MODEL_DOCKER.md)。

Python标准库HTTP服务 + PyMuPDF + NumPy/Pillow + openpyxl；原生JavaScript前端，无需Node构建。可选`npm run dev`用于统一开发入口，仍启动Python服务。

核心目录：`app/`解析/比对/复核与服务；`app/static/`页面；`scripts/`演示和评测；`evaluation/`虚构标注；`tests/`测试。源码仓库为 https://github.com/deyye/approval-structuring-agent 。

## 简洁工作流演示

运行 `python scripts/demo.py --review-demo --data /tmp/approval-workflow-demo --serve`，可体验7份虚构批文的进度总览、阶段覆盖、连续核对、冲突修订、差异筛选及按项目导出。详见 [5分钟演示](docs/DEMO.md)。
