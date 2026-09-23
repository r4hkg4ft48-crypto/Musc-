import { get, list, put } from '@vercel/blob';
import { getVercelOidcToken } from '@vercel/oidc';

const STATE_PATH = 'telegram-agent/state.json';

export type MemoryItem = {
  ts: string;
  user: string;
  assistant: string;
};

export type AgentState = {
  version: 1;
  ownerId?: number;
  ownerBoundAt?: string;
  memory: MemoryItem[];
  facts: string[];
  lastModel?: string;
};

type BlobAuth = {
  token?: string;
  oidcToken?: string;
  storeId?: string;
};

const EMPTY: AgentState = { version: 1, memory: [], facts: [] };

async function blobAuth(): Promise<BlobAuth> {
  const token = process.env.BLOB_READ_WRITE_TOKEN;
  const storeId = process.env.BLOB_STORE_ID;

  if (token) {
    return storeId ? { token, storeId } : { token };
  }

  if (!storeId) {
    throw new Error('BLOB_STORE_ID is not configured');
  }

  const oidcToken = await getVercelOidcToken();
  if (!oidcToken) {
    throw new Error('Vercel OIDC token is unavailable');
  }

  return { oidcToken, storeId };
}

export async function checkBlobHealth(): Promise<{
  linked: boolean;
  reachable: boolean;
  auth: 'token' | 'oidc' | 'missing';
}> {
  const linked = Boolean(process.env.BLOB_STORE_ID || process.env.BLOB_READ_WRITE_TOKEN);

  if (!linked) {
    return { linked: false, reachable: false, auth: 'missing' };
  }

  try {
    const auth = await blobAuth();
    await list({
      ...auth,
      limit: 1,
      prefix: 'telegram-agent/',
    });

    return {
      linked: true,
      reachable: true,
      auth: auth.token ? 'token' : 'oidc',
    };
  } catch {
    return {
      linked: true,
      reachable: false,
      auth: process.env.BLOB_READ_WRITE_TOKEN ? 'token' : 'missing',
    };
  }
}

export async function readState(): Promise<AgentState> {
  try {
    const auth = await blobAuth();
    const result = await get(STATE_PATH, {
      ...auth,
      access: 'private',
      useCache: false,
    });

    if (!result || result.statusCode !== 200) {
      return { ...EMPTY, memory: [], facts: [] };
    }

    const text = await new Response(result.stream).text();
    const parsed = JSON.parse(text) as AgentState;

    return {
      version: 1,
      ownerId: parsed.ownerId,
      ownerBoundAt: parsed.ownerBoundAt,
      memory: Array.isArray(parsed.memory) ? parsed.memory.slice(-24) : [],
      facts: Array.isArray(parsed.facts) ? parsed.facts.slice(-30) : [],
      lastModel: parsed.lastModel,
    };
  } catch {
    return { ...EMPTY, memory: [], facts: [] };
  }
}

export async function writeState(state: AgentState): Promise<void> {
  const auth = await blobAuth();
  const compact: AgentState = {
    ...state,
    version: 1,
    memory: state.memory.slice(-24),
    facts: state.facts.slice(-30),
  };

  await put(STATE_PATH, JSON.stringify(compact), {
    ...auth,
    access: 'private',
    allowOverwrite: true,
    contentType: 'application/json; charset=utf-8',
  });
}

export async function bindOwner(ownerId: number): Promise<AgentState> {
  const state = await readState();
  if (state.ownerId && state.ownerId !== ownerId) {
    throw new Error('Owner is already bound');
  }

  state.ownerId = ownerId;
  state.ownerBoundAt = state.ownerBoundAt ?? new Date().toISOString();
  await writeState(state);
  return state;
}

export async function clearMemory(): Promise<void> {
  const state = await readState();
  state.memory = [];
  state.facts = [];
  await writeState(state);
}
