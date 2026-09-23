import {
  botToken,
  getBotIdentity,
  getWebhookDiagnostics,
  setCommands,
  setWebhook,
} from '../../../lib/telegram';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function canonicalProductionOrigin(request: Request): string {
  const productionHost = process.env.VERCEL_PROJECT_PRODUCTION_URL?.trim();
  if (productionHost) return `https://${productionHost}`;
  return new URL(request.url).origin;
}

export async function GET(request: Request) {
  try {
    const origin = canonicalProductionOrigin(request);
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
      productionOrigin: origin,
      webhook,
      next: 'Открой этого Telegram-бота и отправь /bind плюс последние 8 символов токена BotFather.',
      note: 'Webhook всегда привязывается к публичному production domain, а не к временному deployment URL.',
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
