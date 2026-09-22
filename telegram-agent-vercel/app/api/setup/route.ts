import { bindCode, botToken, setCommands, setWebhook } from '../../../lib/telegram';

export const runtime = 'nodejs';

export async function GET(request: Request) {
  try {
    const origin = new URL(request.url).origin;
    const token = botToken();
    await setCommands();
    await setWebhook(origin);
    return Response.json({
      ok: true,
      webhook: `${origin}/api/telegram/[protected]`,
      next: `Откройте своего Telegram-бота и отправьте: /bind ${bindCode(token)}`,
      note: '8 символов в bind-коде — это только короткий суффикс токена. Полный токен не отправляйте в чат.',
    });
  } catch (error) {
    return Response.json({ ok: false, error: error instanceof Error ? error.message : 'Setup failed' }, { status: 500 });
  }
}
