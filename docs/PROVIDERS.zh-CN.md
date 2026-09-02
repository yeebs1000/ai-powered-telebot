# 选择服务商与分配模型

[English](PROVIDERS.md) · **简体中文**

机器人从不直接调用任何厂商 SDK。所有 AI 调用都经过
[`providers/`](../providers/) 中的适配层，因此**换服务商只是改一个环境变量**，
不需要改代码。

## 最短可用配置

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5
```

这样就能跑起来。下面都是在此基础上的调优。

## 支持的服务商

| `AI_PROVIDER` | 密钥 | 默认模型 | 嵌入（语义记忆） |
|---|---|---|---|
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | ✅ 原生支持 |
| `gemini` | `GEMINI_API_KEY` | 服务商默认 | ✅ 原生支持 |
| `claude` | `ANTHROPIC_API_KEY` | 服务商默认 | ❌ 无嵌入 API |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` | ❌ 仅对话 |
| `openrouter` | `OPENROUTER_API_KEY` | **必须设置 `AI_MODEL`** | ❌ 仅对话 |
| `groq` | `GROQ_API_KEY` | **必须设置 `AI_MODEL`** | ❌ 仅对话 |
| `together` | `TOGETHER_API_KEY` | **必须设置 `AI_MODEL`** | ✅ |
| `local` | 不需要 | **必须设置 `AI_MODEL`** | 通过 `EMBED_*` |

`openai`、`gemini`、`claude` 有各自的适配器，因为它们的 API 形态不同；
其余服务商都使用 OpenAI 的接口格式，因此复用同一个适配器，只是换了
base URL——这也是为什么再接入一个 OpenAI 兼容服务，只需要在
`providers/__init__.py` 里加一行。

### 路由服务商刻意没有默认模型

路由（router）的意义就在于**由你选择模型**，所以不存在合理的默认值，
机器人也不会替你猜：

```
AI_PROVIDER=openrouter has no default model — set AI_MODEL to the model you
want. Example for openrouter: AI_MODEL=anthropic/claude-sonnet-4.5
```

模型名称使用各服务商自己的写法。OpenRouter 带命名空间
（`anthropic/claude-sonnet-4.5`、`deepseek/deepseek-chat`、
`meta-llama/llama-3.3-70b-instruct`）；Groq 与 DeepSeek 则是裸名称
（`deepseek-chat`）。

## 给不同任务分配不同模型

每处理一条消息，机器人会发起**两次**模型调用：

1. **意图路由** —— 一次严格 JSON 调用，判断这条消息属于什么
   （需要实时数据的提问、提醒、投票、「我错过了什么」，还是普通聊天）；
2. **生成回复** —— 真正的回答。

两者诉求不同：路由要**快**且 JSON 可靠，回复要**好**。可以分开指定：

```bash
AI_MODEL=anthropic/claude-sonnet-4.5              # 所有角色的默认值
AI_MODEL_ROUTER=meta-llama/llama-3.1-8b-instruct  # 只用于 JSON 路由
```

`AI_MODEL` 是所有角色的基础默认值；`AI_MODEL_<角色>` 只覆盖该角色，
因此设置 `AI_MODEL_CHAT` **不会**连带改变路由所用的模型。

| 变量 | 作用范围 |
|---|---|
| `AI_MODEL` | 所有角色（未被覆盖时） |
| `AI_MODEL_CHAT` | 生成回复 |
| `AI_MODEL_ROUTER` | 严格 JSON 的意图路由 |

在按量计费的路由上，这个拆分往往是账单的大头——路由调用短小而频繁，
小模型完全够用；在本地运行时，它则是延迟的大头。

### 路由模型必须能输出合法 JSON

意图路由要求返回严格的 JSON 对象。参数量低于约 7B 的模型在这件事上不可靠，
而解析失败会**静默**退化为普通聊天——功能不会报错，只是不再触发。
如果提醒和投票突然失灵，请先怀疑路由模型。

## 服务商没有嵌入能力时，如何保留语义记忆

语义记忆（「我们当时说的那家餐厅是哪家」）需要嵌入接口。
路由类服务商通常只代理对话补全，不提供嵌入端点，记忆功能会因此悄悄关闭。

把嵌入指向别处即可——通常用本地 Ollama，零成本：

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
AI_MODEL=anthropic/claude-sonnet-4.5

EMBED_BASE_URL=http://localhost:11434/v1   # 仅用于嵌入
EMBED_MODEL=nomic-embed-text               # 768 维
```

对话走路由，嵌入走本机：两个客户端，一个机器人。

| 变量 | 用途 |
|---|---|
| `EMBED_BASE_URL` | OpenAI 兼容的嵌入端点 |
| `EMBED_MODEL` | 嵌入模型名称 |
| `EMBED_API_KEY` | 该端点需要密钥时才填 |

不设置这些变量时，机器人会使用主服务商自带的嵌入能力；若其没有，
则关闭语义记忆并明确说明，而不是报错。

## 完全本地运行

```bash
AI_PROVIDER=local
OPENAI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen3.5:9b
EMBED_BASE_URL=http://localhost:11434/v1
EMBED_MODEL=nomic-embed-text
OPENAI_REASONING_EFFORT=none
```

无需密钥、没有账单，数据不出本机。

### 推理模型务必关闭思考过程

推理型模型会把绝大部分输出花在随后被丢弃的思考上。
在 `qwen3.5-agent:32k` 上实测同一条回复：

| | tokens | 耗时 |
|---|---|---|
| 开启思考 | 1686 | 118 秒 |
| 关闭思考 | 68 | 5.8 秒 |

每条消息要调用两次模型，所以这就是全部的延迟预算。
解决办法是 `OPENAI_REASONING_EFFORT=none`。注意：在 Ollama 的 OpenAI 兼容
接口上**只有 `reasoning_effort` 有效**——`"think": false` 和
`chat_template_kwargs: {"enable_thinking": false}` 都会被忽略。

### 确认服务实际监听的地址

只有当 Ollama 确实绑定在 `localhost:11434` 时，这个地址才是对的。
如果你设置过 `OLLAMA_HOST`（例如绑定到 Docker 网桥以便容器访问），
环回地址上就没有任何服务在监听，机器人会连接失败。
用 `ss -lntp | grep 11434` 确认。

## 成本与隐私一览

| 方案 | 成本 | 离开本机的内容 |
|---|---|---|
| 本地（Ollama） | 无 | 无 |
| DeepSeek | 极低 | 发给模型的消息 |
| 路由（OpenRouter / Groq / Together） | 取决于所选模型 | 经路由发给模型的消息 |
| OpenAI / Gemini / Claude | 取决于用量 | 发给模型的消息 |

无论使用哪种方案，聊天记录始终保存在**本地 SQLite** 中。
