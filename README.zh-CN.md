# AI-Powered Telebot（AI 驱动的 Telegram 群组助手）

[English](README.md) · **简体中文**

一个可自选 AI 服务商的 Telegram 群组智能助手——Gemini、OpenAI 或 Claude，
只需一个环境变量即可切换。它能自然对话、记住群里聊过的内容，
并把股票、外汇、大宗商品和网络搜索的实时数据带进对话。

## 功能

- **自然对话** —— 在群里 @ 它（或私聊），它会以朋友般随意的语气回复。
- **自选 AI 服务商** —— 一个环境变量在 Gemini / OpenAI / Claude 之间切换，
  无需改动代码。
- **实时表情回应** —— 对明显值得回应的群消息加上表情（🤣🎉🔥❤😢🤯🙏💯），
  无论是否被 @。不调用 AI，使用本地轻量启发式规则。
- **语音消息** —— 发送语音，机器人先转写为文字，再按普通消息处理
  （所以口述「六点提醒我给妈妈打电话」同样会建立提醒）。
  需要支持音频的服务商（Gemini 或 OpenAI）。
- **群聊记录与摘要** —— 让它「总结一下」「我错过了什么」，
  它会基于 Supabase 中的聊天记录回顾近况。
- **成员画像** —— 「你怎么看某某」会调取此人的历史发言并给出轻松的评价。
- **语义记忆检索** —— 按**含义**而非关键词检索历史消息（向量嵌入）。
  需要服务商支持嵌入；`AI_PROVIDER=claude` 时该功能自动关闭并优雅降级。
- **实时数据** —— 股票、外汇、大宗商品报价，以及网络搜索。
- **自然语言提醒**、**互动投票**、**图片理解**。

## 服务商能力对照

| 服务商 | 对话 | 嵌入（语义记忆） | 音频 | 图片 |
|---|---|---|---|---|
| `gemini` | ✅ | ✅ | ✅ | ✅ |
| `openai` | ✅ | ✅ | ✅ | ✅ |
| `claude` | ✅ | ❌（无嵌入 API） | ❌ | ✅ |

## 环境变量

| 变量 | 必填 | 用途 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | 是 | @BotFather 提供的机器人令牌 |
| `AI_PROVIDER` | 是 | `gemini`、`openai` 或 `claude` |
| `AI_MODEL` | 否 | 覆盖该服务商的默认模型 |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | 视服务商 | 对应密钥 |
| `SUPABASE_URL` | 是 | Supabase 项目地址 |
| `SUPABASE_SERVICE_ROLE_KEY` | 是 | 服务端密钥，**务必保密**，仅用于服务器侧 |
| `SUPABASE_ANON_KEY` | 是 | 匿名/公开密钥，用于受 RLS 保护的操作 |
| `TAVILY_API_KEY` | 否 | 启用网络搜索 |
| `ALPHA_VANTAGE_KEY` | 否 | 启用股票/外汇/大宗商品行情 |

### 关于双密钥与 RLS

本项目区分 **service role** 与 **anon** 两把 Supabase 密钥，并配合行级安全
（RLS）策略使用：service role 密钥拥有绕过 RLS 的权限，**绝不能**出现在
任何客户端或公开位置；anon 密钥在 RLS 约束下工作。请按
[`supabase_schema.sql`](supabase_schema.sql) 建表并启用其中的策略。

## 安装

1. 安装 Python 3.11+，创建虚拟环境并安装依赖：
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
2. 在 [supabase.com](https://supabase.com) 建项目，在 **SQL Editor** 中执行
   [`supabase_schema.sql`](supabase_schema.sql)。
3. 复制 `.env.example` 为 `.env` 并填写密钥。
4. 运行：
   ```bash
   .venv/bin/python main.py
   ```

> **一个令牌只能有一个轮询进程。** 如果同一个 bot token 在别处（例如
> Railway）还在运行，Telegram 会返回 `409 Conflict`，两边都会出问题。

## 工作原理

`main.py` 是基于 `python-telegram-bot` 的单文件主程序；AI 调用统一经过
`providers/` 适配层，因此主程序不直接依赖任何厂商 SDK。
每条消息先经过一次 JSON **意图路由**调用，再分发到对应功能分支
（行情、搜索、提醒、投票、摘要、画像、语义检索或普通聊天）。

未直接 @ 机器人的群消息会在后台写入聊天记录（以及嵌入向量，
若当前服务商支持），用于之后的摘要与语义检索。

## 许可

见 [LICENSE](LICENSE)。
