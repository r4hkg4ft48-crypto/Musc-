import {
  botToken,
  getBotIdentity,
  getWebhookDiagnostics,
  setCommands,
  setWebhook,
} from '../../../lib/telegram';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  try {
    const origin = new URL(request.url).origin;
    botToken();

    const bot = await getBotIdentity();
    await setCommands();
    await setWebhook(origin);
    const webhook = await getWebhookDiagnostics(origin);

    return Response.json({
      ok: true,
      bot: {
        id: bot.id,
        username: bot.username ?? null,
        firstName: bot.first_name,
      },
      webhook,
      next: 'Открой этого Telegram-бота и отправь /bind плюс последние 8 символов токена BotFather.',
      note: 'Полный токен, webhook secret и bind-код этот endpoint не показывает.',
    });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: error instanceof Error ? error.message : 'Setup failed',
      },
      { status: 500 },
    );
  }
}
