# 安装与配置

[English](SETUP.md) · **简体中文**

本文假设你没有任何相关经验，大约需要十分钟。

中途出问题时，运行 `python doctor.py`——它会检查整套配置，并直接告诉你哪里要改。

---

## 1. 安装 Python

需要 **Python 3.11 或更高版本**。

- **Windows / macOS** —— 从 [python.org/downloads](https://www.python.org/downloads/) 下载。
  Windows 安装时**务必勾选 "Add Python to PATH"**。
- **Linux** —— 通常已自带，用 `python3 --version` 确认。

## 2. 获取代码

```bash
git clone https://github.com/yeebs1000/ai-powered-telebot
cd ai-powered-telebot
```

没有 git？点绿色 **Code** 按钮 → **Download ZIP**，解压后在该文件夹打开终端。

## 3. 安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Windows 上把 `.venv/bin/pip` 换成 `.venv\Scripts\pip`。

## 4. 创建你的机器人

1. 打开 Telegram，找 [@BotFather](https://t.me/BotFather)。
2. 发送 `/newbot`，起一个名字和用户名。
3. 他会返回一个**令牌（token）**，形如 `123456789:AAE...`。
   请妥善保管：拿到它的人就能控制你的机器人。

**然后关闭隐私模式**，否则机器人读不到群消息，看起来就像坏了：

```
/setprivacy  →  选择你的 bot  →  Disable
```

如果机器人已经在群里，改完设置后需要**移出再重新加入**——该设置只在加入时生效。

## 5. 选择它的「大脑」

复制示例配置：

```bash
cp .env.example .env
```

用任意文本编辑器打开 `.env`，填入令牌，然后**三选一**：

**最便宜的付费方案**

```bash
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
```

**一把密钥，任意模型**（[openrouter.ai](https://openrouter.ai/keys)）

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5
```

**免费且私密** —— 需要先装 [Ollama](https://ollama.com)

```bash
AI_PROVIDER=local
OPENAI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
OPENAI_REASONING_EFFORT=none
```

然后执行 `ollama pull qwen3.5:9b`。

更多细节（包括给不同任务分配不同模型）见
[docs/PROVIDERS.zh-CN.md](docs/PROVIDERS.zh-CN.md)。

## 6. 启动前先自检

```bash
.venv/bin/python doctor.py
```

它会向 Telegram 验证令牌、确认你的服务商和模型确实能应答、检查数据库可写，
并对最常被忽略的两个坑发出提醒。把标记为 `FAIL` 的项目改好，再运行一次。

## 7. 运行

```bash
.venv/bin/python main.py
```

把机器人加进群，@ 它一下，它就该回复了。按 Ctrl-C 停止。

---

## 让它持续运行

机器人必须处于运行状态才会回应。几种做法：

**一台常开的机器** —— 小主机、旧笔记本、树莓派。用 [`deploy/`](deploy/)
里的 systemd 服务文件：

```bash
sudo cp deploy/telebot.service /etc/systemd/system/
# 先修改里面的 User= 和各处路径
sudo systemctl daemon-reload
sudo systemctl enable --now telebot
journalctl -u telebot -f
```

**Docker** —— 见 [`docker-compose.yml`](docker-compose.yml)：

```bash
docker compose up -d
docker compose logs -f
```

**云端 worker** —— 已附带 `Procfile`。

> **同一时间只能运行一份。** 两个进程共用同一个 bot token 会让 Telegram
> 返回 `409 Conflict`，两边都不正常。换机器部署后最常见的问题就是这个——
> 记得先把旧的停掉。

## 常见问题

| 现象 | 原因 |
|---|---|
| 群里 @ 它没反应 | 隐私模式仍开着（第 4 步），或改设置前就已入群 |
| 日志里出现 `409 Conflict` | 有另一份进程在用同一个令牌 |
| 回复要等一分钟 | 推理模型没关思考——设置 `OPENAI_REASONING_EFFORT=none` |
| 提示「Semantic memory is OFF」 | 当前服务商没有嵌入能力，见 [docs/PROVIDERS.zh-CN.md](docs/PROVIDERS.zh-CN.md) |
| 就是跑不起来，不知道为什么 | `python doctor.py` |

## 它保存了什么

一个 SQLite 文件（由 `TELEBOT_DB` 指定，在普通目录下默认是 `./telebot.db`），
其中包含群消息、嵌入向量、成员名称和投票。删掉这个文件即可清空机器人的全部记忆。

只有**未直接 @ 机器人**的群消息会被记录；私聊内容不会被保存。
