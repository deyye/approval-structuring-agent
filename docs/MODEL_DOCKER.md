# 外部模型与 Docker 部署

## 一键启动

安装 Docker Engine 或 Docker Desktop（Linux 容器模式）及 Compose v2。在仓库根目录执行：

```bash
cp .env.example .env
docker compose up -d --build --wait
```

Windows PowerShell 使用 `Copy-Item .env.example .env`。访问 `http://127.0.0.1:8765`。默认本地解析即可使用。镜像内含 Python、依赖和中文 OCR，无需在宿主机安装 Python。镜像不包含上传材料、密钥或评测输出；进程使用 UID 10001。

`APP_PORT` 控制宿主机端口。容器内 `HOST=0.0.0.0`、`PORT=8765`、`DATA_DIR=/app/data` 固定，不受本机 `.env` 的 HOST/PORT/DATA_DIR 干扰。宿主机只映射回环地址。已有旧 root 容器数据卷需要先备份，再由管理员将目录所有者调整为 UID/GID 10001。

## 配置外部模型

在服务提供商控制台取得 API 基础地址、模型 ID、API Key，写入本机 `.env`：

```dotenv
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=填写自己的密钥
LLM_MODEL=填写可用的文字模型ID
VISION_MODEL=填写可用的视觉模型ID或留空
LLM_TIMEOUT_SECONDS=90
LLM_MAX_RETRIES=2
LLM_MAX_TOKENS=8192
LLM_JSON_MODE=true
LLM_EXTRA_BODY={}
```

支持 OpenAI Chat Completions 兼容协议，包括支持此协议的百炼等外部服务和内网网关。基础地址应包含供应商规定的路径前缀，不能仅填首页；也可填写完整 `/chat/completions` 地址。视觉模型共用同一地址和密钥，必须支持 image_url。具体模型名、地域及权限以你的账户为准：[百炼兼容接口说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。

**也可以不碰这些文件**：界面左侧「模型设置」支持直接填写服务地址、密钥与模型名并保存，**改完立即生效、无需重启**，可在 DeepSeek／通义千问／智谱等任意 OpenAI 兼容服务间随时切换。界面保存的值写入 `data/model_config.json`（随数据卷持久化），**优先级高于环境变量**；点「恢复默认」即回落到本文档的配置方式。密钥只存在服务端，接口不回显明文，仅返回是否已配置及末四位。

若模型不支持 response_format，设置 `LLM_JSON_MODE=false`，输出仍必须能解析为 JSON 对象。不自动放宽或绕过证据校验。需要关闭思考的供应商可按其文档配置 `LLM_EXTRA_BODY={"enable_thinking":false}`；仅允许 enable_thinking/reasoning_effort 两个附加参数。不要给不支持的模型传入这些参数。

配置后重建服务使环境生效：

```bash
docker compose up -d --force-recreate --wait
docker compose exec -T app python scripts/check_model.py
docker compose exec -T app python scripts/check_model.py --vision
```

最后一个命令仅在配置视觉模型后执行。本机 Python 模式执行 `python scripts/check_model.py`，脚本自动加载 `.env`。界面左侧也有两个“测试模型”按钮，只发送固定测试文字或合成白图，不读取已有批文；可能产生少量 API 费用。成功输出模型名、耗时和 ok，不输出密钥。

连接成功后勾选“使用大模型辅助抽取”再上传。已有文件选择单文档→重新提取。模型只给出带证据的候选值，失败保留本地结果并显示原因；人工修订始终保留。视觉连接测试通过仅表示接口可接收图像，不代表印章判别准确率已完成评测。

限流和 500/502/503/504 或网络失败才重试，最多 3 次；401/403/400/404 不重试。拒绝重定向，避免将凭据发送至跳转地址；限制响应大小并拒绝截断输出。每次请求超时独立计时，总等待时间可能为（重试次数+1）×超时再加退避时间。

内网 HTTP 模型需明确指定主机，例如 `LLM_HTTP_HOSTS=model-gateway`，地址 `http://model-gateway:8000/v1`；需另外确保容器网络可以解析并访问该主机。容器内的 localhost 指容器自身。无鉴权的自托管模型可设置 `LLM_ALLOW_NO_KEY=true`，默认要求密钥。

## 文件密钥方式（可选）

将密钥保存至本机 `secrets/llm_api_key.txt`（不要提交），通过 Compose secret 挂载：

```bash
docker compose -f compose.yaml -f compose.secrets.yaml up -d --build --wait
```

后续管理这个实例时均使用相同的两个 `-f` 参数。此方式由后端读取 `LLM_API_KEY_FILE`，不将 Key 存入镜像或普通环境变量。确保密钥文件对容器 UID 10001 可读；宿主机目录仅管理员可访问。挂载机制见 [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)。

## 运行管理与数据

```bash
docker compose ps
docker compose logs --tail=100 app
docker compose stop
docker compose start
```

PDF、结构化 JSON 和修订历史在命名卷 `approval-data` 中，正常重建容器保留。`docker compose down` 保留卷；不要在真实实例上执行 `down -v`，它会删除数据。Compose 健康检查只判断应用存活，不调用收费模型。容器退出会自动重启；进程还活着但 unhealthy 时需检查日志，Docker 不会仅因 unhealthy 自动重启。

备份：先等待任务结束，停止服务以获得一致快照，然后执行（先创建 backups 目录）：

```bash
docker compose stop
docker compose run --rm --no-deps -v ./backups:/backup --user 0 app python -c "import shutil; shutil.make_archive('/backup/approval-data', 'gztar', '/app/data')"
docker compose start
```

备份包含原文与派生数据，应存放在受控位置。恢复前停止服务并备份现存卷；在维护窗口由管理员将已检查的归档恢复至 `/app/data`，还原 UID/GID 10001 后启动。避免对正在处理文档的卷直接覆盖恢复。

本应用仍为单用户服务。服务器使用可通过 `ssh -L 8765:127.0.0.1:8765 user@server` 隧道访问；多人公网部署需另接已有身份认证/HTTPS网关。不要通过改端口映射直接将文档接口暴露公网。

## 可复现验收

GitHub Actions 的 docker 作业执行真实构建、启动、健康检查、非 root 和中文 OCR 检查，随后上传合成 PDF、保存人工修订、读取高亮页和 Excel，并重建容器复查数据持久性。可在装有 Docker 的机器复现：

```bash
docker compose up -d --build --wait
python scripts/docker_smoke.py
docker compose up -d --force-recreate --wait
python scripts/docker_smoke.py --verify-persistence
```

验收脚本需要宿主机安装 requirements.lock 依赖，普通运行无需安装。脚本会写入一份虚构测试文件，请优先在独立验收实例运行。测试外部模型必须由实际凭据完成；本地 HTTP 测试服务通过不能替代供应商联调结果。
