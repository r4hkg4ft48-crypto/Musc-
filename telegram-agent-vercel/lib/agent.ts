import { generateText } from 'ai';
import type { AgentState } from './storage';

const GENERAL_MODEL = 'alibaba/qwen3-next-80b-a3b-instruct';
const CODE_MODEL = 'alibaba/qwen3-coder-next';
const FREE_FALLBACK = 'poolside/laguna-s-2.1-free';
const RESEARCH_MODEL = 'perplexity/sonar';

function isCodeTask(text: string) {
  return /(код|code|github|репозитор|repository|bug|ошибк|api|typescript|javascript|python|deploy|vercel|docker|mcp)/i.test(text);
}

function compactMemory(state: AgentState) {
  return state.memory.slice(-10).map((m, i) => `${i + 1}. USER: ${m.user}\nASSISTANT: ${m.assistant}`).join('\n\n');
}

async function callModel(model: string, system: string, prompt: string, maxOutputTokens = 1800) {
  try {
    const result = await generateText({ model, system, prompt, maxOutputTokens, temperature: 0.25 });
    return { text: result.text.trim(), model };
  } catch (primaryError) {
    if (model === FREE_FALLBACK) throw primaryError;
    const result = await generateText({ model: FREE_FALLBACK, system, prompt, maxOutputTokens, temperature: 0.25 });
    return { text: result.text.trim(), model: FREE_FALLBACK };
  }
}

export async function runAgent(objective: string, state: AgentState, mode: 'run' | 'research' = 'run') {
  const memory = compactMemory(state) || '(память пока пуста)';

  if (mode === 'research') {
    const research = await callModel(
      RESEARCH_MODEL,
      'Ты исследователь личного ИИ-агента. Отвечай по-русски. Используй доступное поисковое заземление модели. Отделяй факты от предположений, называй даты, не выдумывай источники.',
      `Тема исследования: ${objective}\n\nНедавний контекст:\n${memory}`,
      2400,
    );
    return { answer: research.text, plan: ['Исследовать актуальные источники', 'Сопоставить факты', 'Сформировать вывод'], model: research.model };
  }

  const chosen = isCodeTask(objective) ? CODE_MODEL : GENERAL_MODEL;
  const planner = await callModel(
    chosen,
    'Ты Planner центрального ИИ-агента. Верни только короткий нумерованный план из 2-6 конкретных шагов. Не выполняй задачу. Учитывай, что агент работает из Telegram на Vercel.',
    `Цель пользователя: ${objective}\n\nНедавняя память:\n${memory}`,
    700,
  );

  const executor = await callModel(
    chosen,
    `Ты Executor личного ИИ-агента. Выполни задачу пользователя максимально полезно и конкретно. Отвечай по-русски, если пользователь не просит другое. Не утверждай, что выполнил внешнее действие, если у тебя нет соответствующего инструмента. Для кода давай целостные решения.\n\nПлан Planner:\n${planner.text}`,
    `Задача: ${objective}\n\nНедавняя память:\n${memory}`,
    2400,
  );

  const reviewer = await callModel(
    GENERAL_MODEL,
    'Ты Reviewer. Проверь черновик на точность, полноту, ложные заявления о выполненных действиях и лишнюю воду. Верни только улучшенный финальный ответ пользователю, без описания своей проверки.',
    `Задача пользователя:\n${objective}\n\nПлан:\n${planner.text}\n\nЧерновик Executor:\n${executor.text}`,
    2400,
  );

  return {
    answer: reviewer.text || executor.text,
    plan: planner.text.split('\n').map(s => s.trim()).filter(Boolean).slice(0, 6),
    model: reviewer.model,
  };
}

export async function extractFacts(user: string, assistant: string): Promise<string[]> {
  try {
    const result = await generateText({
      model: GENERAL_MODEL,
      system: 'Извлеки только устойчивые факты или предпочтения, полезные в будущих разговорах. Не сохраняй секреты, токены, пароли, платёжные данные. Если сохранять нечего, верни NONE. Каждый факт с новой строки, максимум 4.',
      prompt: `USER: ${user}\nASSISTANT: ${assistant}`,
      maxOutputTokens: 350,
      temperature: 0,
    });
    const text = result.text.trim();
    if (!text || /^none$/i.test(text)) return [];
    return text.split('\n').map(s => s.replace(/^[-*\d.\s]+/, '').trim()).filter(Boolean).slice(0, 4);
  } catch {
    return [];
  }
}
