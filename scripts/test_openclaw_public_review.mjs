import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runPublicReview } from './openclaw_public_review.mjs';

const gpt = 'openai/gpt-6.1-sol';
const claude = 'anthropic/claude-opus-5-5';
const request = { model: gpt, thinking: 'medium', timeout: 90, prompt: 'Public fixture only' };

function fixture(overrides = {}) {
  const calls = [];
  const model = { provider: 'openai', id: 'gpt-6.1-sol' };
  const completion = {
    prepareSimpleCompletionModelForAgent: async params => {
      calls.push(['prepare', params]);
      return { model, auth: { mode: 'oauth' } };
    },
    completeWithPreparedSimpleCompletionModel: async params => {
      calls.push(['complete', params]);
      return { ...model, model: model.id, stopReason: 'stop', content: [] };
    },
    extractAssistantText: () => '{"reviews":[{"symbol":"TEST.TO","summary":"Incomplete"}]}',
    ...overrides,
  };
  return { calls, dependencies: { sdk: async name => name === 'config-runtime' ? {
    getRuntimeConfig: () => ({ agents: { defaults: { modelPolicy: { allow: [gpt] } } } }),
    resolveDefaultAgentId: () => 'fixture-owner',
  } : completion } };
}

test('GPT uses protected SDK auth and zero tools at requested effort', async () => {
  const f = fixture();
  const result = await runPublicReview(request, '/fixture/openclaw', f.dependencies);
  assert.equal(result.ok, true);
  assert.equal(result.model, 'gpt-6.1-sol');
  assert.deepEqual(result.toolSummary, { calls: 0 });
  assert.equal(f.calls[0][1].bindAuthOwner, true);
  assert.equal(f.calls[0][1].modelRef, gpt);
  assert.equal(f.calls[1][1].options.reasoning, 'medium');
  assert.deepEqual(f.calls[1][1].context.tools, []);
  assert.equal(f.calls[1][1].context.messages.length, 1);
  assert.equal(JSON.stringify(result).includes('oauth'), false);
});

test('invalid effort and model strings never reach either transport', async () => {
  for (const change of [{ thinking: 'ultra' }, { thinking: 'xhigh' }, { model: 'openai/gpt;bad' }]) {
    const f = fixture();
    assert.equal((await runPublicReview({ ...request, ...change }, '/fixture', f.dependencies)).ok, false);
    assert.equal(f.calls.length, 0);
  }
});

test('missing live allowlist rejects before authentication preparation', async () => {
  const f = fixture();
  const sdk = f.dependencies.sdk;
  f.dependencies.sdk = async name => name === 'config-runtime' ? {
    getRuntimeConfig: () => ({}), resolveDefaultAgentId: () => 'fixture',
  } : sdk(name);
  const result = await runPublicReview(request, '/fixture', f.dependencies);
  assert.equal(result.error.kind, 'model_policy_rejected');
  assert.equal(f.calls.length, 0);
});

test('authentication errors are redacted, never passed through', async () => {
  const f = fixture({ prepareSimpleCompletionModelForAgent: async () => {
    throw new Error('No route-compatible authentication source. private-secret');
  } });
  const result = await runPublicReview(request, '/fixture', f.dependencies);
  assert.equal(result.error.kind, 'authentication_unavailable');
  assert.equal(JSON.stringify(result).includes('private-secret'), false);
});

test('mismatched selected models and truncated completions fail closed', async () => {
  for (const overrides of [
    { prepareSimpleCompletionModelForAgent: async () => ({ model: { provider: 'openai', id: 'other' } }) },
    { completeWithPreparedSimpleCompletionModel: async () => ({ stopReason: 'length', content: [] }) },
    { completeWithPreparedSimpleCompletionModel: async () => ({ stopReason: 'stop',
        content: [{ type: 'toolCall', name: 'unexpected' }] }) },
  ]) {
    const f = fixture(overrides);
    assert.equal((await runPublicReview(request, '/fixture', f.dependencies)).ok, false);
  }
});

test('alternate fallback keeps native Claude safe mode and shares remaining budget', async () => {
  let clock = 0;
  const f = fixture({ prepareSimpleCompletionModelForAgent: async () => {
    clock = 20000;
    throw new Error('Authentication unavailable');
  } });
  f.dependencies.clock = () => clock;
  f.dependencies.execute = async (cli, args, options) => {
    assert.equal(cli, '/fixture/openclaw');
    assert.deepEqual(args.slice(0, 4), ['agent', 'exec', '--model', claude]);
    assert.equal(args[args.indexOf('--timeout') + 1], '70');
    assert.equal(options.timeout, 70000);
    assert.equal(options.env.CLAUDE_CODE_SAFE_MODE, '1');
    assert.equal(args.includes('--fallback'), false);
    return { stdout: JSON.stringify({ ok: true, provider: 'claude-cli', model: 'claude-opus-5-5',
      toolSummary: { calls: 0 }, final: '{"reviews":[]}' }) };
  };
  const result = await runPublicReview({ ...request, fallback: claude }, '/fixture/openclaw', f.dependencies);
  assert.equal(result.ok, true);
  assert.equal(result.reviewAttempts.length, 2);
});

test('exhausted request budget does not start a fallback', async () => {
  let clock = 0;
  const f = fixture({ prepareSimpleCompletionModelForAgent: async () => {
    clock = 89000;
    throw new Error('Timeout');
  } });
  f.dependencies.clock = () => clock;
  f.dependencies.execute = async () => assert.fail('Fallback should not start');
  const result = await runPublicReview({ ...request, fallback: claude }, '/fixture', f.dependencies);
  assert.equal(result.ok, false);
  assert.equal(result.reviewAttempts.length, 1);
});
