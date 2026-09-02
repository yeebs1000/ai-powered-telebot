# AI-Powered Telebot（AI 驱动的 Telegram 群组助手）

[English](README.md) · **简体中文**

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Providers](https://img.shields.io/badge/AI-OpenAI%20%7C%20Gemini%20%7C%20Claude%20%7C%20DeepSeek%20%7C%20OpenRouter%20%7C%20local-brightgreen)

一个可以跑在**任意模型**上的 Telegram 群组助手——OpenAI、Gemini、Claude、
DeepSeek、OpenRouter / Groq / Together 这类路由服务，或本地 Ollama——
只用一个环境变量选择。不绑定厂商，不需要云数据库，
除了模型本身之外不需要注册任何服务。

它会学习你们群的说话方式、记得聊过什么，并且像一个真的在读消息的人一样回应。

## 为什么选它

- **任意服务商，一个变量。** `AI_PROVIDER=deepseek` 即可。
  所有 AI 调用都经过 [`providers/`](providers/) 中的适配层，
  代码的其余部分完全不知道你选了哪家。
- **不同任务用不同模型。** 便宜的快模型做意图路由，好模型写回复。
  在按量计费的路由上，这个拆分往往就是账单的大头。
  详见 [docs/PROVIDERS.zh-CN.md](docs/PROVIDERS.zh-CN.md)。
- **不需要准备数据库。** 聊天记录、嵌入向量、成员信息和投票都存在
  **一个本地 SQLite 文件**里。不用注册、不用付费，群里的消息留在你自己的机器上。
- **按含义回应，而不是按关键词。** 关键词方案会给
  「我爷爷昨晚过世了」加上 🎉——因为 "passed" 命中了「祝贺」。
  本项目在嵌入空间里比较语义，得到的是 😢。
- **学习你们群的语气。** 它统计每个人**比别人更常用**的词，
  以及消息长度、表情使用等习惯，并按这个语气写回复。
  这是从聊天记录**聚合统计**出来的，不是微调——所以每来一条消息它就更准，
  而且画像内容你可以自己读、自己判断是否公道。
- **可以完全离线运行。** 指向本地 Ollama，数据不出本机。

## 功能

- **自然对话** —— 群里 @ 它，或者私聊。
- **语义记忆** —— 「我们当时说的那家餐厅是哪家」按含义检索历史消息。
- **我错过了什么** —— 锚定在**你自己**最后一条发言之后，
  所以「错过的内容」是真的针对你。刚发过言的人会被直接告知没有新内容。
- **群聊摘要** —— 回顾最近的对话。
- **认得群里每个人** —— 以 Telegram user_id 为准，
  名字支持模糊匹配（`yuanbing` 能找到 `Yuan Bing`）；
  当两个人相似度接近时**拒绝猜测**。遇到认不出的人？
  回复他的消息并说 `@你的bot 这位是 Marcus`，名字就记住了。
- **成员画像** —— 「你怎么看 Ryan」，依据是 Ryan 真实写过的话。
- **表情回应** —— 按语义选择，并有冷却时间，保持克制。
- **实时网络搜索** —— 比分、新闻、天气、价格、进行中的事件。
- **提醒、投票、图片理解、语音消息。**
- **可选参考资料目录** —— 一个只读目录，回复时可以引用其中的笔记。

## 快速开始

> 第一次接触？**[SETUP.zh-CN.md](SETUP.zh-CN.md)** 是一份从零开始的分步指南。


```bash
git clone https://github.com/yeebs1000/ai-powered-telebot
cd ai-powered-telebot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python main.py
```

`.env` 里只需要两样东西：

1. **机器人令牌** —— 找 [@BotFather](https://t.me/BotFather) 发 `/newbot`。
   要让它能读到群消息：`/setprivacy` → 选择你的 bot → **Disable**。
2. **一个服务商** —— 三选一：

```bash
# 路由服务，模型随你挑
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5

# 或者便宜好用
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...

# 或者免费且私密
AI_PROVIDER=local
OPENAI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
OPENAI_REASONING_EFFORT=none
```

就这样。数据库会在首次运行时自动创建。

> **一个令牌只能有一个轮询进程。** 如果同一个 token 在别处还在运行，
> Telegram 会返回 `409 Conflict`，两边都会出问题。

不确定配置对不对？运行 `python doctor.py`，它会逐项检查并直接告诉你怎么改。

## 服务商与模型

| `AI_PROVIDER` | 密钥 | 默认模型 | 嵌入 |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | ✅ |
| `gemini` | `GEMINI_API_KEY` | 服务商默认 | ✅ |
| `claude` | `ANTHROPIC_API_KEY` | 服务商默认 | ❌ |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | ❌ |
| `openrouter` | `OPENROUTER_API_KEY` | 需设置 `AI_MODEL` | ❌ |
| `groq` | `GROQ_API_KEY` | 需设置 `AI_MODEL` | ❌ |
| `together` | `TOGETHER_API_KEY` | 需设置 `AI_MODEL` | ✅ |
| `local` | 不需要 | 需设置 `AI_MODEL` | 通过 `EMBED_*` |

路由服务只代理对话补全，不提供嵌入接口，语义记忆会因此关闭。
把嵌入指向本地 Ollama 即可保留该功能，且零成本：

```bash
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_MODEL=nomic-embed-text
```

**[docs/PROVIDERS.zh-CN.md](docs/PROVIDERS.zh-CN.md)** 有完整说明：
按角色分配模型、为什么路由模型必须足够聪明才能输出合法 JSON、
如何保住语义记忆，以及推理模型的延迟陷阱。

## 部署

使用长轮询——**不需要入站端口、域名或 TLS**，在家庭路由器 NAT 后也能直接跑。
任何长期开机的设备都可以：VPS、树莓派、闲置笔记本、小主机。
[`deploy/`](deploy/) 里有 systemd 服务文件，也附带了给 PaaS 用的 `Procfile`。

## 工作原理

[`main.py`](main.py) 是主程序，基于 `python-telegram-bot`。周边模块：

| 模块 | 职责 |
|---|---|
| [`providers/`](providers/) | 厂商适配层——唯一知道 AI API 长什么样的代码 |
| [`store.py`](store.py) | SQLite：消息、嵌入、成员、投票 |
| [`reactions.py`](reactions.py) | 表情回应：语义优先，关键词兜底 |
| [`profiles.py`](profiles.py) | 从聊天记录聚合出的成员语言习惯 |
| [`vault.py`](vault.py) | 可选的只读参考资料检索 |

每条消息的流程：判断是否值得加表情回应 → 若不是发给机器人的群消息，
则写入记录并生成嵌入 → 若是发给它的，用一次严格 JSON 调用判断意图，然后回复。

## 测试

```bash
for t in tests/t_*.py; do .venv/bin/python "$t"; done
```

覆盖服务商解析与报错信息、消息排序、名字模糊匹配与歧义拒绝、身份绑定、
语义表情回应及其降级、成员画像的区分度、按人锚定的「我错过了什么」、
向量检索往返，以及参考资料目录的范围隔离。

## 隐私

聊天记录、嵌入向量和投票都存在一个本地 SQLite 文件里，权限被设为 `0600`——
SQLite 默认按 umask 创建（通常全局可读），而里面是别人的聊天内容。
只有**未直接 @ 机器人**的群消息会被记录，私聊不记录。
使用 `AI_PROVIDER=local` 时，数据完全不出本机。

## 参与贡献

欢迎提 issue 和 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。
适合上手的方向：再接入一个 OpenAI 兼容的服务商
（在 `providers/__init__.py` 里加一行），或为表情回应新增一个语义类别及其示例短语。

## 许可

MIT，见 [LICENSE](LICENSE)。
