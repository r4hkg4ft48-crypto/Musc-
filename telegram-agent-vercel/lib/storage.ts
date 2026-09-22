import { get, put } from '@vercel/blob';

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

const EMPTY: AgentState = { version: 1, memory: [], facts: [] };

export async function readState(): Promise<AgentState> {
  try {
    const result = await get(STATE_PATH, { access: 'private', useCache: false });
    if (!result || result.statusCode !== 200) return { ...EMPTY, memory: [], facts: [] };
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
  const compact: AgentState = {
    ...state,
    version: 1,
    memory: state.memory.slice(-24),
    facts: state.facts.slice(-30),
  };
  await put(STATE_PATH, JSON.stringify(compact), {
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
