import { checkBlobHealth } from '@/lib/storage';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  const blob = await checkBlobHealth();

  return Response.json({
    ok: true,
    platform: 'vercel',
    ai: 'vercel-ai-gateway-oidc',
    telegramConfigured: Boolean(process.env.TELEGRAM_BOT_TOKEN),
    blobConfigured: blob.reachable,
    blobStoreLinked: blob.linked,
    blobAuth: blob.auth,
    version: '1.1.0',
  });
}
