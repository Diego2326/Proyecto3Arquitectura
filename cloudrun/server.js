import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { timingSafeEqual } from 'node:crypto';

const PORT = Number(process.env.PORT || 8080);
const DEVICE_TOKEN = process.env.DEVICE_TOKEN || '';
const MAX_BODY_BYTES = 8 * 1024;
const here = dirname(fileURLToPath(import.meta.url));
const dashboard = await readFile(join(here, 'index.html'));

const state = {
  angle: 0,
  distance_cm: null,
  threshold_cm: 15,
  mode: 'AUTO',
  desired_mode: 'AUTO',
  desired_angle: 0,
  desired_threshold_cm: 15,
  revision: 0,
  updated_at: null,
};

function sendJson(response, status, body) {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  response.end(JSON.stringify(body));
}

function authorized(request) {
  const supplied = request.headers.authorization || '';
  const expected = `Bearer ${DEVICE_TOKEN}`;
  if (!DEVICE_TOKEN) return false;
  const suppliedBuffer = Buffer.from(supplied);
  const expectedBuffer = Buffer.from(expected);
  if (suppliedBuffer.length !== expectedBuffer.length) return false;
  return timingSafeEqual(suppliedBuffer, expectedBuffer);
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

async function readJson(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) throw new Error('body_too_large');
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new Error('invalid_json');
  }
}

function snapshot() {
  const online = state.updated_at !== null &&
    (Date.now() - Date.parse(state.updated_at)) < 10_000;
  return { ...state, online };
}

async function handle(request, response) {
  const url = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`);
  const route = `${request.method} ${url.pathname}`;

  if (route === 'GET /' || route === 'GET /index.html') {
    response.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-cache',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
    });
    response.end(dashboard);
    return;
  }

  if (route === 'GET /health') {
    sendJson(response, 200, { status: 'ok' });
    return;
  }

  if (route === 'GET /api/state') {
    sendJson(response, 200, snapshot());
    return;
  }

  if (route === 'GET /api/command') {
    if (!authorized(request)) {
      sendJson(response, 401, { error: 'unauthorized' });
      return;
    }
    sendJson(response, 200, {
      mode: state.desired_mode,
      angle: state.desired_angle,
      threshold_cm: state.desired_threshold_cm,
      revision: state.revision,
    });
    return;
  }

  if (route === 'POST /api/telemetry') {
    if (!authorized(request)) {
      sendJson(response, 401, { error: 'unauthorized' });
      return;
    }
    let body;
    try {
      body = await readJson(request);
    } catch (error) {
      const status = error.message === 'body_too_large' ? 413 : 400;
      sendJson(response, status, { error: 'Invalid JSON telemetry.' });
      return;
    }
    if (!isObject(body)) {
      sendJson(response, 400, { error: 'Telemetry must be a JSON object.' });
      return;
    }

    const angle = Number(body.angle);
    const distanceCm = Number(body.distance_cm);
    const thresholdCm = Number(body.threshold_cm);
    const mode = String(body.mode || '').toUpperCase();
    if (!Number.isInteger(angle) || angle < 0 || angle > 180 ||
        !Number.isInteger(distanceCm) || distanceCm < -1 || distanceCm > 500 ||
        !Number.isInteger(thresholdCm) || thresholdCm < 5 || thresholdCm > 100 ||
        !['AUTO', 'MANUAL'].includes(mode)) {
      sendJson(response, 400, { error: 'Telemetry values are outside the accepted range.' });
      return;
    }

    state.angle = angle;
    state.distance_cm = distanceCm;
    state.threshold_cm = thresholdCm;
    state.mode = mode;
    state.updated_at = new Date().toISOString();
    sendJson(response, 200, { ok: true });
    return;
  }

  if (route === 'POST /api/command') {
    let body;
    try {
      body = await readJson(request);
    } catch (error) {
      const status = error.message === 'body_too_large' ? 413 : 400;
      sendJson(response, status, { error: 'Envía un objeto JSON válido.' });
      return;
    }
    if (!isObject(body)) {
      sendJson(response, 400, { error: 'El comando debe ser un objeto JSON.' });
      return;
    }

    const mode = String(body.mode || '').toUpperCase();
    const thresholdCm = Number(body.threshold_cm ?? state.desired_threshold_cm);
    const angle = Number(body.angle ?? 0);
    if (!['AUTO', 'MANUAL'].includes(mode)) {
      sendJson(response, 400, { error: 'mode debe ser AUTO o MANUAL.' });
      return;
    }
    if (!Number.isInteger(thresholdCm) || thresholdCm < 5 || thresholdCm > 100) {
      sendJson(response, 400, { error: 'El umbral debe ser un entero entre 5 y 100 cm.' });
      return;
    }
    if (mode === 'MANUAL' && (!Number.isInteger(angle) || angle < 0 || angle > 180)) {
      sendJson(response, 400, { error: 'El ángulo manual debe ser un entero entre 0 y 180.' });
      return;
    }

    state.desired_mode = mode;
    state.desired_angle = mode === 'MANUAL' ? angle : 0;
    state.desired_threshold_cm = thresholdCm;
    state.revision += 1;
    sendJson(response, 200, {
      mode: state.desired_mode,
      angle: state.desired_angle,
      threshold_cm: state.desired_threshold_cm,
      revision: state.revision,
    });
    return;
  }

  sendJson(response, 404, { error: 'Not found.' });
}

const server = createServer((request, response) => {
  handle(request, response).catch((error) => {
    console.error('Request error:', error);
    if (!response.headersSent) sendJson(response, 500, { error: 'Internal server error.' });
    else response.destroy();
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`IoT dashboard listening on port ${PORT}`);
});
