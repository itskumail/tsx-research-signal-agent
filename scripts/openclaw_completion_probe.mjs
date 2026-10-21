// Diagnostic fixture: public evidence only, with no tools or credential output.
import { createRequire } from 'node:module';
import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const root = process.env.TRADING_OPENCLAW_PACKAGE;
const cli = process.env.TRADING_OPENCLAW_CLI;
if (!root && !cli) throw new Error('OpenClaw package or CLI path required');
const require = createRequire(cli ? realpathSync(cli) : `${root}/package.json`);
const sdk = async (name) => import(pathToFileURL(require.resolve(`openclaw/plugin-sdk/${name}`)));
const { getRuntimeConfig, resolveDefaultAgentId } = await sdk('config-runtime');
const { prepareSimpleCompletionModelForAgent, completeWithPreparedSimpleCompletionModel,
  extractAssistantText } = await sdk('simple-completion-runtime');
const signal = AbortSignal.timeout(90000);
const started = Date.now();
const classify = (error) => {
  const message = String(error?.message ?? error).toLowerCase();
  if (/auth|bearer|credential/.test(message)) return 'authentication_unavailable';
  if (/not allowed/.test(message)) return 'model_policy_rejected';
  if (/timeout|aborted/.test(message)) return 'request_timeout';
  return 'completion_unavailable';
};
try {
  const cfg = getRuntimeConfig();
  const prepared = await prepareSimpleCompletionModelForAgent({
    cfg, agentId: resolveDefaultAgentId(cfg), modelRef: 'openai/gpt-6.1-sol',
    bindAuthOwner: true, signal,
  });
  if ('error' in prepared) throw new Error(prepared.error);
  if (prepared.model.provider !== 'openai' || prepared.model.id !== 'gpt-6.1-sol') {
    throw new Error('Model selection mismatch');
  }
  if (process.argv.includes('--prepare-only')) {
    console.log(JSON.stringify({ status: 'prepared', provider: prepared.model.provider,
      model: prepared.model.id, auth_mode: prepared.auth.mode, tools: 0,
      seconds: (Date.now() - started) / 1000 }));
  } else {
    const assistant = await completeWithPreparedSimpleCompletionModel({
      cfg, model: prepared.model, auth: prepared.auth,
      context: { systemPrompt: 'Review only the supplied public fixture. No tools. Return only JSON.',
        messages: [{ role: 'user', timestamp: Date.now(), content:
          'A public Canadian-stock excerpt for TEST.TO has no quote timestamp. '
          + 'Do not invent prices or contracts. Return {"reviews":[{"symbol":"TEST.TO",'
          + '"summary":"brief evidence assessment","concerns":"brief missing-date concern"}]}.' }],
        tools: [] },
      options: { reasoning: 'medium', maxTokens: 400, signal },
    });
    const text = extractAssistantText(assistant);
    let recognized = 0;
    try {
      const parsed = JSON.parse(text.replace(/^```(?:json)?\s*|\s*```$/g, '').trim());
      recognized = (parsed.reviews ?? []).filter(r => r.symbol === 'TEST.TO'
        && typeof r.summary === 'string' && typeof r.concerns === 'string').length;
    } catch {}
    const toolCalls = (assistant.content ?? []).filter(p => p.type === 'toolCall').length;
    console.log(JSON.stringify({ status: recognized && !toolCalls ? 'accepted' : 'failed',
      reason: recognized && !toolCalls ? null : classify(assistant.errorMessage ?? 'Invalid final response'),
      provider: assistant.provider, model: assistant.model, stop_reason: assistant.stopReason,
      recognized_notes: recognized, tools: toolCalls, seconds: (Date.now() - started) / 1000 }));
  }
} catch (error) {
  console.log(JSON.stringify({ status: 'failed', reason: classify(error),
    seconds: (Date.now() - started) / 1000 }));
  process.exitCode = 1;
}
// The public standalone SDK owns prepared resources for this process lifetime.
process.exit(process.exitCode ?? 0);
