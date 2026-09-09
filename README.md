# 批文研析 · 投资项目文件结构化智能体

将政府投资项目的建议书/立项、可行性研究和初步设计批复，整理为可定位原文的结构化对照表。

## 已实现

- 单份或多份 PDF 上传，按项目代码归组，按审批阶段并列。
- 14 项固定字段：发文机关标志、发文字号、标题、印章、印发机关、印发日期、项目名称、项目代码、项目单位、建设内容、建设地点、总投资/匡算/估算/概算、资金来源、建设周期。
- 建设数字指标拆分：建筑面积、用地面积、道路长度、路基宽度、设计速度、挖填方、管线长度、停车位、绿化面积等；模型可补充其他指标。
- 原文差异着色、单位标准化、数值变动计算，保留“约”和范围值。
- 点击字段跳转原文页并高亮证据区域，支持跨页证据。
- 人工修订/确认，保存修订历史，重新计算差异。
- 导出 Excel（固定字段、建设指标、证据索引、待复核事项）和完整 JSON。
- 本地规则工作流可直接运行；可配置 OpenAI-compatible 模型增强抽取，模型输出须匹配引用原文。

## 快速运行

要求 Python 3.10 或更高版本。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.server
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m app.server
```

浏览器打开 `http://127.0.0.1:8765`。点击“上传批复文件”，选择项目资料 PDF，等待任务完成。点击对照表中的单元格可查看原文。

没有 API Key 也能使用本地模式。运行数据保存在 `data/`，重启后保留。源码不附带原始批文；将自己的六份资料直接上传即可。

## 配置大模型

复制 `.env.example` 为 `.env`，填写：

```dotenv
LLM_BASE_URL=https://你的模型服务地址/v1
LLM_API_KEY=你的密钥
LLM_MODEL=你的文本或多模态模型名称
VISION_MODEL=你的视觉模型名称
```

- BASE_URL 是服务根地址，程序在其后追加 `/chat/completions`。
- 接口需支持 `messages`、`temperature=0` 和 `response_format={"type":"json_object"}`。
- `VISION_MODEL` 可不配置；配置后使用图片输入确认印章候选。
- 修改配置后重启，在左栏启用“大模型辅助抽取”。启用会将批文文本发往该地址；印章确认会发送对应区域图像。密钥仅从后端读取，不发往网页。
- 同一 PDF 再次上传会去重；已在本地模式处理的文件，启用模型后再次上传可补充模型抽取，并保留人工修订。
- 调用失败会保留本地结果并显示提示；不能将调用失败视为模型抽取成功。
- 本次验证没有使用真实模型凭据；仅测试了模型结果的证据校验逻辑。请在自己的服务上执行端到端验收。

## 扫描 PDF 与印章

原生 PDF 直接读取文本坐标。扫描页使用 PyMuPDF 调用 Tesseract OCR，需额外安装 Tesseract 和 `chi_sim` 中文语言包。Linux 示例：

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
```

未安装中文 OCR 时，界面会明确提示不可识别/待复核，不生成猜测值。

本地印章检测寻找红色近圆形候选区域，显示“有”并标记“待核对”；可在原文面板人工确认。检测不到时显示“无法判断”。支持人工确认为“无”，不会把未检出直接解释为无印章。黑白章、印章与文字重叠、非圆形章需要人工或视觉模型确认，程序不验证印章法律效力。

## 比对口径

| 状态 | 显示 | 口径 |
|---|---|---|
| 一致 | 无底色 | 字段相同 |
| 内容变化 | 橙色 | 数值或文字内容变化 |
| 表述差异 | 蓝色 | 格式不同，或指标单位换算后相同 |
| 未载明 | 灰色 | 本份批文没有该字段，不跨文件补值 |
| 待核对 | 红色 | 视觉候选、角色推断或同文多值 |

- 13.693 公顷换算为 136930 平方米，与 136929.6 平方米仍显示 0.4 平方米差额，不擅自抹平。
- “约2.31公里”的约数限定保留；“24.5米-36米”保留上下界。
- 估算与概算保留各自口径，不直接认定违规或超概。
- 同项目同阶段多份文件全部保留为独立列，不自动覆盖版本。
- 项目代码缺失时文件保持独立；可人工修订项目代码实现确认后的归组。
- 标题中的“立项申请”归入“建议书/立项”并提示核对，不能据此推断所有地区事项法律等价。
- 自由文本仅进行确定性的格式归一，未引入不透明的语义相似度分数。

## 架构与目录

```text
app/extract.py       逐页解析、证据坐标、固定字段、动态指标、模型接口
app/compare.py       单位换算后的比对与 Excel 导出
app/server.py        本地服务、批量任务、持久化及人工修订
app/static/          三栏操作界面：项目列表 / 字段对照 / 原文定位
tests/              单元与 HTTP 合同测试、六份资料回归
.env.example         模型配置模板
Dockerfile           可选容器运行，含中文 OCR
VALIDATION.md        实测结果与尚未验证的能力
```

证据对象包含文档 ID、页码、文本行 ID、原文片段、PDF 坐标。页面渲染和坐标统一为无旋转页面，网页根据图片尺寸映射高亮位置。字段可能引用多行/多页。

技术栈为 Python 标准库 HTTP 服务 + PyMuPDF + NumPy/Pillow + openpyxl，前端为原生 JavaScript/CSS。无需额外 Node 构建；主工作流以代码编排，模型作为可替换工具。

该版本面向本机单用户演示/验证：绑定 127.0.0.1，每批最多10份，单份20MB，批次总计23MB以内，单份最多100页。尚未实现多人账号、生产级队列和百万文件调度。生产接入需要身份认证、组织权限及数据库/对象存储等基础能力。

## 测试

```bash
python -m unittest discover -s tests -v
```

六份批复不打入源码包。将其放在同一目录，保留原文件名（含 `(1)`），再运行：

```bash
SAMPLE_DIR=/path/to/pdfs python -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:SAMPLE_DIR="C:\path\to\pdfs"
python -m unittest discover -s tests -v
```

没有 SAMPLE_DIR 时跳过资料回归，其余测试正常运行。

## 容器运行

```bash
docker build -t approval-structuring-agent .
docker run --rm -p 127.0.0.1:8765:8765 -v approval-data:/app/data --env-file .env approval-structuring-agent
```

不用模型时可以省略 `--env-file .env`。不要把无认证的本地服务直接暴露到公网。

## 源码仓库

https://github.com/deyye/approval-structuring-agent

主分支为 `main`。`.env`、`data/` 和原始批文不提交。克隆后按上面的步骤安装依赖、启动并上传自己的文件。
