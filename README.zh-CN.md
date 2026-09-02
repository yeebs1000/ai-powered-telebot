# AI-Powered Telebot（AI 驱动的 Telegram 群组助手）

[English](README.md) · **简体中文**

一个可自选 AI 服务商的 Telegram 群组智能助手——Gemini、OpenAI 或 Claude，
只需一个环境变量即可切换。它能自然对话、记住群里聊过的内容，并把股票、
外汇、大宗商品和网络搜索的实时数据带进对话。

也可以**完全本地运行**：把它指向自建的 Ollama，数据不出本机。

## 功能

- **自然对话** —— 在群里 @ 它（或私聊），它会以朋友般随意的语气回复。
- **自选 AI 服务商** —— 一个环境变量在 Gemini / OpenAI / Claude 之间切换，
  无需改代码。参见下方[选择 AI 服务商](#选择-ai-服务商)。
- **语义化表情回应** —— 按**含义**而非关键词给群消息加表情回应。
  「我爷爷昨晚过世了」在关键词方案下会因为 "passed" 命中「祝贺」而回 🎉；
  语义方案会正确地回 😢。嵌入向量每条消息只算一次，与语义记忆共用。
- **语音消息** —— 发语音，机器人转成文字后按普通消息处理（所以口述
  「六点提醒我给妈妈打电话」同样会建提醒）。需要支持音频的服务商，
  或本地 Whisper。
- **群聊记录与摘要** —— 让它「总结一下」，它会根据本地 SQLite 记录回顾近况。
- **「我错过了什么」** —— 与普通摘要不同，它锚定在**你自己**最后一条发言之后，
  因此每个人得到的答案不同。刚发过言的人会被告知「没有新内容」。
- **成员身份识别** —— 以 Telegram 的 user_id 为准（显示名会变），
  支持精确、别名、子串到模糊匹配：`yuanbing` 能匹配到 `Yuan Bing`。
  两个成员相似度接近时**拒绝猜测**，而不是随便选一个。
- **教它认人** —— 回复某人的消息并 @ 机器人说明身份
  （`@bot 这位是 Marcus`），名字就会绑定到那个人。
  这是给显示名无法输入（例如纯 emoji 昵称）的成员准备的退路。
- **群体语言习惯学习** —— 从聊天记录中统计每位成员**比别人更常用**的词，
  以及消息长度、表情使用、大写、提问频率等习惯，并注入到回复提示中。
  这是聚合统计，**不是微调**（原因见下）。
- **语义记忆检索** —— 按含义而非关键词检索历史消息（向量嵌入）。
- **实时数据** —— 股票、外汇、大宗商品报价，以及网络搜索。
- **自然语言提醒** —— 「下午三点提醒我交表」。
- **互动投票** —— 五分钟窗口的内联键盘投票。
- **图片理解** —— 发图并提问，走同一套流程。
- **知识库引用（可选）** —— 只读挂载**一个**你指定的 Obsidian 目录作为参考资料。

## 为什么是「聚合」而不是「微调」

群成员画像是从聊天记录**实时统计**出来的，而不是训练进模型权重里。

微调需要的数据量远超一个群聊记录；它把某一时刻的快照固化下来，
群里一说新话题就过时；每次更新都要重新训练。而聚合只花几毫秒、
每来一条消息就自动变好，而且**人可以直接读出画像内容并判断它是否公道**。

两个刻意的取舍，让它诚实而不是看起来厉害：

- **区分度**以全群基准衡量。所有人都在说的词不构成任何人的特征。
  单纯取词频前十会给每个成员同样一份列表，看着像画像，其实什么都没说。
- 消息少于 5 条的成员**不做画像**；没有特征词也没有明显习惯的成员，
  干脆不写这一行。历史很短时输出就该是空的。

## 选择 AI 服务商

| 服务商 | 对话 | 嵌入（语义记忆） | 音频 | 图片 |
|---|---|---|---|---|
| `gemini` | ✅ | ✅ | ✅ | ✅ |
| `openai` | ✅ | ✅ | ✅ | ✅ |
| `claude` | ✅ | ❌（无嵌入 API） | ❌ | ✅ |
| 本地（Ollama 等，走 `openai` 适配） | ✅ | 需配置嵌入模型 | 需本地 Whisper | 取决于模型 |

`AI_PROVIDER=claude` 时语义记忆会自动关闭并优雅降级，不会报错。

## 环境变量

| 变量 | 必填 | 用途 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 是 | @BotFather 提供的机器人令牌 |
| `AI_PROVIDER` | 是 | `gemini`、`openai` 或 `claude` |
| `AI_MODEL` | 否 | 覆盖默认模型 |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | 视服务商 | 对应密钥 |
| `OPENAI_BASE_URL` | 否 | 指向任意 OpenAI 兼容服务（Ollama / LM Studio / vLLM） |
| `OPENAI_EMBED_MODEL` | 否 | 本地嵌入模型（需 768 维） |
| `OPENAI_REASONING_EFFORT` | 否 | 本地推理模型设为 `none`，见下 |
| `TELEBOT_DB` | 否 | SQLite 路径，默认 `/var/lib/telebot/telebot.db` |
| `VAULT_REF_ROOT` | 否 | 只读参考资料目录；不设则关闭 |
| `WHISPER_MODEL` | 否 | 本地语音转写模型（`base` / `small`） |
| `TAVILY_API_KEY` / `ALPHA_VANTAGE_KEY` | 否 | 网络搜索 / 行情 |

## 在 Linux 小主机上自建（搭配本地大模型）

机器人使用 Telegram **长轮询**，因此**不需要任何入站端口、域名或 TLS**，
在家庭路由器 NAT 后面也能直接跑。

1. 安装 Ollama 并拉取模型：

   ```bash
   ollama pull qwen3.5:9b            # 对话
   ollama pull nomic-embed-text      # 嵌入，768 维
   ```

2. 配置指向本地：

   ```bash
   AI_PROVIDER=openai
   OPENAI_BASE_URL=http://localhost:11434/v1
   OPENAI_API_KEY=ollama
   AI_MODEL=qwen3.5:9b
   OPENAI_EMBED_MODEL=nomic-embed-text
   OPENAI_REASONING_EFFORT=none
   ```

   > **注意 Ollama 的监听地址。** 如果你改过 `OLLAMA_HOST`（例如为了让
   > 容器能访问而绑定到 Docker 网桥），`localhost:11434` 就是错的，
   > 环回地址上没有任何服务在监听。以实际监听地址为准。

3. 建虚拟环境并作为服务运行：

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   sudo cp deploy/telebot.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now telebot
   journalctl -u telebot -f
   ```

### 本地模型的几个要点

- **一个令牌只能有一个轮询进程。** 如果旧的部署（例如 Railway）还在跑，
  Telegram 会返回 `409 Conflict`，两边都会出问题。
- **推理模型务必关闭思考过程。** 推理型模型会把绝大部分输出花在随后被
  丢弃的思考上。实测 `qwen3.5-agent:32k` 回一句 127 字符的话，
  开启思考需要 1686 tokens、约 118 秒；关闭后 68 tokens、约 5.8 秒。
  机器人每条消息要调用两次模型（意图路由 + 回复），所以这就是全部的延迟预算。
  在 Ollama 的 OpenAI 兼容接口上，只有 `reasoning_effort` 有效——
  `"think": false` 和 `chat_template_kwargs` 都不起作用。
- **JSON 意图路由需要有一定能力的模型**，3B 以下的模型常常输出不可靠的 JSON，
  解析失败会退化为普通聊天（功能会静默失效）。建议 7B 以上的 instruct 模型。
- **不要把 Ollama 暴露到公网**（保持默认的本地监听）。

## 隐私

- 聊天记录、嵌入向量和投票都存在**一个本地 SQLite 文件**里，没有托管数据库。
- 该文件在创建时会被设为 `0600`——SQLite 默认按 umask 创建，通常是全局可读，
  而里面是别人的聊天内容。
- 只有**未直接 @ 机器人**的群消息会被记录（用于摘要与记忆）；私聊不记录。
- 参考资料目录是**只读挂载的单个目录**。范围本身就是防线：
  机器人无法遍历知识库其余部分，因此别处的分类标注错误也不会泄漏到群里。
  带 `classification: deny` 的笔记即使在该目录内也会被跳过。

## 工作原理

`main.py` 是基于 `python-telegram-bot` 的单文件主程序，AI 调用统一走
`providers/` 适配层，因此主程序不直接依赖任何厂商 SDK。周边模块：

| 模块 | 职责 |
|---|---|
| `store.py` | SQLite：消息、嵌入、成员身份、投票 |
| `reactions.py` | 表情回应：语义优先，关键词兜底 |
| `profiles.py` | 从聊天记录统计群体与个人语言习惯 |
| `vault.py` | 只读参考资料检索 |
| `project_vault.py` | 把记录投影成 Obsidian 每日笔记 |

每条消息的流程：先按语义决定是否加表情回应 → 未 @ 的群消息记入库并生成嵌入
→ 被 @ 或私聊时调用意图路由（一次 JSON 调用）→ 按意图分支处理 → 回复。

## 测试

```bash
for t in tests/t_*.py; do .venv/bin/python "$t"; done
```

覆盖消息排序、成员模糊匹配与歧义拒绝、身份绑定、表情回应的语义与降级、
群体画像的区分度、按人锚定的「我错过了什么」、向量检索往返，
以及参考资料的范围隔离。

## 许可

见 [LICENSE](LICENSE)。
