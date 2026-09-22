export const runtime = 'nodejs';

export async function GET() {
  return Response.json({
    ok: true,
    platform: 'vercel',
    ai: 'vercel-ai-gateway-oidc',
    telegramConfigured: Boolean(process.env.TELEGRAM_BOT_TOKEN),
    blobConfigured: Boolean(process.env.BLOB_READ_WRITE_TOKEN || process.env.VERCEL_OIDC_TOKEN),
    version: '1.0.0',
  });
}
