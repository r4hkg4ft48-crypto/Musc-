import { botToken, setCommands, setWebhook } from '../../../lib/telegram';

export const runtime = 'nodejs';

export async function GET(request: Request) {
  try {
    const origin = new URL(request.url).origin;
    botToken();
    await setCommands();
    await setWebhook(origin);
    return Response.json({
      ok: true,
      webhook: `${origin}/api/telegram/[protected]`,
      next: 'Открой своего Telegram-бота и отправь /bind и последние 8 символов токена BotFather.',
      note: 'Полный токен и bind-код этот HTTP endpoint никогда не показывает.',
    });
  } catch (error) {
    return Response.json({ ok: false, error: error instanceof Error ? error.message : 'Setup failed' }, { status: 500 });
  }
}
