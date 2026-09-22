import crypto from 'node:crypto';

export function botToken(): string {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  if (!token) throw new Error('TELEGRAM_BOT_TOKEN is not configured');
  return token;
}

export function webhookPathSecret(token = botToken()): string {
  return crypto.createHash('sha256').update(`hook:${token}`).digest('hex').slice(0, 40);
}

export function telegramHeaderSecret(token = botToken()): string {
  return crypto.createHash('sha256').update(`header:${token}`).digest('base64url').slice(0, 48);
}

export function bindCode(token = botToken()): string {
  return token.slice(-8);
}

async function telegramApi<T = any>(method: string, body?: Record<string, unknown>): Promise<T> {
  const token = botToken();
  const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body ?? {}),
    cache: 'no-store',
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(`Telegram ${method} failed: ${data.description ?? response.status}`);
  }
  return data.result as T;
}

export async function setWebhook(origin: string) {
  const token = botToken();
  const secret = webhookPathSecret(token);
  const headerSecret = telegramHeaderSecret(token);
  return telegramApi('setWebhook', {
    url: `${origin}/api/telegram/${secret}`,
    secret_token: headerSecret,
    allowed_updates: ['message'],
    drop_pending_updates: false,
  });
}

export async function setCommands() {
  return telegramApi('setMyCommands', {
    commands: [
      { command: 'start', description: 'Запуск и состояние привязки' },
      { command: 'status', description: 'Состояние агента' },
      { command: 'bind', description: 'Привязать владельца' },
      { command: 'memory', description: 'Показать недавнюю память' },
      { command: 'forget', description: 'Очистить разговорную память' },
      { command: 'research', description: 'Исследовать тему через AI Gateway' },
      { command: 'help', description: 'Команды' }
    ]
  });
}

export async function sendChatAction(chatId: number, action = 'typing') {
  try { await telegramApi('sendChatAction', { chat_id: chatId, action }); } catch {}
}

export async function sendMessage(chatId: number, text: string) {
  const chunks = splitTelegram(text);
  for (const chunk of chunks) {
    await telegramApi('sendMessage', {
      chat_id: chatId,
      text: chunk,
      disable_web_page_preview: true,
    });
  }
}

function splitTelegram(text: string, max = 3900): string[] {
  const clean = text.trim() || 'Готово.';
  if (clean.length <= max) return [clean];
  const out: string[] = [];
  let rest = clean;
  while (rest.length > max) {
    let cut = rest.lastIndexOf('\n', max);
    if (cut < max * 0.5) cut = rest.lastIndexOf(' ', max);
    if (cut < max * 0.5) cut = max;
    out.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) out.push(rest);
  return out;
}

export type TelegramMessage = {
  message_id: number;
  text?: string;
  caption?: string;
  chat: { id: number; type: string };
  from?: { id: number; is_bot?: boolean; first_name?: string; username?: string };
};

export type TelegramUpdate = { update_id: number; message?: TelegramMessage };
