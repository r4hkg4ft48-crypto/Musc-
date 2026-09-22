import { waitUntil } from '@vercel/functions';
import { bindCode, botToken, sendChatAction, sendMessage, telegramHeaderSecret, webhookPathSecret, type TelegramUpdate } from '../../../../lib/telegram';
import { bindOwner, clearMemory, readState, writeState } from '../../../../lib/storage';
import { extractFacts, runAgent } from '../../../../lib/agent';

export const runtime = 'nodejs';
export const maxDuration = 300;

function helpText() {
  return [
    'Команды:',
    '/status — состояние агента',
    '/memory — недавняя память',
    '/forget — очистить разговорную память',
    '/research <тема> — исследование через Vercel AI Gateway',
    '',
    'Обычный текст запускает Planner → Executor → Reviewer.',
  ].join('\n');
}

async function handleUpdate(update: TelegramUpdate) {
  const message = update.message;
  if (!message?.from || message.from.is_bot || message.chat.type !== 'private') return;
  const chatId = message.chat.id;
  const userId = message.from.id;
  const text = (message.text || message.caption || '').trim();
  if (!text) return;

  const state = await readState();

  if (!state.ownerId) {
    if (text.startsWith('/bind')) {
      const supplied = text.split(/\s+/)[1] || '';
      if (supplied !== bindCode()) {
        await sendMessage(chatId, 'Неверный код привязки. Возьми последние 8 символов токена BotFather и отправь: /bind XXXXXXXX');
        return;
      }
      await bindOwner(userId);
      await sendMessage(chatId, '✅ Владелец привязан. Теперь этот Telegram-аккаунт единственный, кто может управлять агентом.\n\nОтправь /status или просто напиши задачу.');
      return;
    }
    await sendMessage(chatId, 'Агент ещё не привязан. Отправь /bind и последние 8 символов токена этого бота. Полный токен не присылай.');
    return;
  }

  if (state.ownerId !== userId) {
    await sendMessage(chatId, '⛔ Этот агент привязан к другому владельцу.');
    return;
  }

  if (text === '/start' || text === '/help') {
    await sendMessage(chatId, `✅ Агент активен на Vercel.\n\n${helpText()}`);
    return;
  }

  if (text === '/status') {
    await sendMessage(chatId, [
      '✅ Telegram AI Agent работает',
      'Платформа: Vercel',
      'LLM: Vercel AI Gateway (OIDC, без отдельного API key)',
      'Память: Vercel Blob',
      `Записей памяти: ${state.memory.length}`,
      `Фактов: ${state.facts.length}`,
      state.lastModel ? `Последняя модель: ${state.lastModel}` : '',
    ].filter(Boolean).join('\n'));
    return;
  }

  if (text === '/memory') {
    const recent = state.memory.slice(-5).map((m, i) => `${i + 1}) ${m.user}\n→ ${m.assistant.slice(0, 500)}`).join('\n\n');
    const facts = state.facts.slice(-10).map(f => `• ${f}`).join('\n');
    await sendMessage(chatId, `Память:\n${recent || '(пока пусто)'}\n\nУстойчивые факты:\n${facts || '(нет)'}`);
    return;
  }

  if (text === '/forget') {
    await clearMemory();
    await sendMessage(chatId, '🧹 Разговорная память очищена. Привязка владельца сохранена.');
    return;
  }

  const isResearch = text.startsWith('/research ');
  const objective = isResearch ? text.slice('/research '.length).trim() : text;
  if (!objective) {
    await sendMessage(chatId, 'После /research укажи тему.');
    return;
  }

  await sendChatAction(chatId);
  try {
    const fresh = await readState();
    const result = await runAgent(objective, fresh, isResearch ? 'research' : 'run');
    await sendMessage(chatId, result.answer);

    const facts = await extractFacts(objective, result.answer);
    fresh.memory.push({ ts: new Date().toISOString(), user: objective, assistant: result.answer });
    for (const fact of facts) if (!fresh.facts.includes(fact)) fresh.facts.push(fact);
    fresh.lastModel = result.model;
    await writeState(fresh);
  } catch (error) {
    const messageText = error instanceof Error ? error.message : 'Unknown error';
    await sendMessage(chatId, `⚠️ Не удалось выполнить задачу. ${messageText.slice(0, 700)}`);
  }
}

export async function POST(request: Request, context: { params: Promise<{ secret: string }> }) {
  const { secret } = await context.params;
  const token = botToken();
  if (secret !== webhookPathSecret(token)) return new Response('Not found', { status: 404 });

  const expectedHeader = telegramHeaderSecret(token);
  if (request.headers.get('x-telegram-bot-api-secret-token') !== expectedHeader) {
    return new Response('Forbidden', { status: 403 });
  }

  const update = await request.json() as TelegramUpdate;
  waitUntil(handleUpdate(update));
  return Response.json({ ok: true });
}
