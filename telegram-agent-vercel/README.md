# Telegram AI Agent — Vercel-native

Telegram webhook → Vercel Function → Planner → Executor → Reviewer → Vercel AI Gateway → Vercel Blob memory.

## Why

- No OpenAI API key.
- Vercel AI Gateway authenticates with deployment OIDC.
- Private Vercel Blob stores owner binding and rolling memory.
- Primary models use Vercel Gateway credits; a zero-priced Gateway model is the fallback.

## Setup

1. Deploy this subdirectory to Vercel and create a private Blob store.
2. Add `TELEGRAM_BOT_TOKEN` during the Vercel Deploy flow.
3. Open `/api/setup` once on the production URL.
4. Send `/bind <last 8 characters of the BotFather token>` to the bot.
5. Send `/status`.

## Security

- Private Telegram chats only.
- Only the bound Telegram user ID can execute the agent.
- The webhook path and Telegram secret-token header are derived from the bot token.
- Full bot token is never returned by the app.
- Memory extraction is instructed not to retain tokens, passwords, payment data, or other secrets.
