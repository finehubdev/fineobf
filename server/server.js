'use strict';

/**
 * Darc Obfuscator — Fastify HTTP API.
 *
 * POST /obfuscate  { source, options }  -> { output }
 * GET  /health
 *
 * The obfuscation engine is the Python package `darcobfuscator`; this server
 * shells out to it. Configure via env:
 *   PORT            (default 8080)
 *   DARC_PYTHON     python executable (default: ../.venv/bin/python then python3)
 *   DARC_ROOT       repo root that contains the darcobfuscator package
 *   DARC_API_KEY    if set, requests must send `x-api-key` matching it
 *   DARC_MAX_BYTES  max source size (default 524288)
 */

const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const Fastify = require('fastify');

const ROOT = process.env.DARC_ROOT || path.join(__dirname, '..');
const MAX_BYTES = parseInt(process.env.DARC_MAX_BYTES || '524288', 10);
const API_KEY = process.env.DARC_API_KEY || null;
const HEADER = '-- Protected with Darc Obfuscator.';

function resolvePython() {
  if (process.env.DARC_PYTHON) return process.env.DARC_PYTHON;
  const venv = path.join(ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(venv)) return venv;
  return 'python3';
}
const PYTHON = resolvePython();

/** Build CLI args from a validated options object. */
function optionArgs(options) {
  const args = ['-m', 'darcobfuscator', '-'];
  if (options.roblox_check === false) args.push('--no-roblox-check');
  if (options.anti_tamper === false) args.push('--no-anti-tamper');
  if (options.rename === false) args.push('--no-rename');
  if (options.silent_fail === true) args.push('--silent-fail');
  if (Number.isInteger(options.seed)) args.push('--seed', String(options.seed));
  return args;
}

function runObfuscator(source, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, optionArgs(options), {
      cwd: ROOT,
      env: { ...process.env, PYTHONPATH: ROOT },
    });
    let out = '';
    let err = '';
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('obfuscation timed out'));
    }, 30000);

    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error(err.trim() || `exited ${code}`));
    });
    child.stdin.write(source);
    child.stdin.end();
  });
}

function build() {
  const app = Fastify({
    logger: true,
    bodyLimit: MAX_BYTES + 4096,
  });

  // simple in-memory rate limiter: 30 req / 60s per IP
  const hits = new Map();
  app.addHook('onRequest', async (req, reply) => {
    if (req.url === '/health') return;
    const now = Date.now();
    const ip = req.ip;
    const rec = hits.get(ip) || { count: 0, reset: now + 60000 };
    if (now > rec.reset) { rec.count = 0; rec.reset = now + 60000; }
    rec.count += 1;
    hits.set(ip, rec);
    if (rec.count > 30) {
      reply.code(429).send({ error: 'rate limit exceeded, try again shortly' });
    }
  });

  // optional API key
  app.addHook('preHandler', async (req, reply) => {
    if (req.url === '/health') return;
    if (API_KEY && req.headers['x-api-key'] !== API_KEY) {
      reply.code(401).send({ error: 'invalid or missing x-api-key' });
    }
  });

  app.get('/health', async () => ({ status: 'ok', engine: 'darcobfuscator', python: PYTHON }));

  app.post('/obfuscate', {
    schema: {
      body: {
        type: 'object',
        required: ['source'],
        properties: {
          source: { type: 'string', minLength: 1 },
          options: {
            type: 'object',
            properties: {
              roblox_check: { type: 'boolean' },
              anti_tamper: { type: 'boolean' },
              rename: { type: 'boolean' },
              silent_fail: { type: 'boolean' },
              seed: { type: 'integer' },
            },
            additionalProperties: false,
          },
        },
        additionalProperties: false,
      },
    },
  }, async (req, reply) => {
    const { source, options = {} } = req.body;
    if (Buffer.byteLength(source, 'utf8') > MAX_BYTES) {
      return reply.code(413).send({ error: `source exceeds ${MAX_BYTES} bytes` });
    }
    try {
      const output = await runObfuscator(source, options);
      // guarantee the required header even if the engine changes
      const finalOutput = output.startsWith(HEADER)
        ? output
        : `${HEADER}\n${output}`;
      return { output: finalOutput, bytes: Buffer.byteLength(finalOutput, 'utf8') };
    } catch (e) {
      req.log.error(e);
      return reply.code(400).send({ error: String(e.message || e) });
    }
  });

  return app;
}

if (require.main === module) {
  const app = build();
  const port = parseInt(process.env.PORT || '8080', 10);
  app.listen({ port, host: '0.0.0.0' })
    .then(() => app.log.info(`Darc Obfuscator API on :${port}`))
    .catch((e) => { app.log.error(e); process.exit(1); });
}

module.exports = { build, runObfuscator };
