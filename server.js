import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { timingSafeEqual } from 'node:crypto';
import { WebSocket, WebSocketServer } from 'ws';

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

const server = createServer((request, response) => {
  handleHttp(request, response).catch((error) => {
    console.error('HTTP request error:', error);
    if (!response.headersSent) sendJson(response, 500, { error: 'Internal server error.' });
    else response.destroy();
  });
});

const webSockets = new WebSocketServer({ noServer: true, perMessageDeflate: false });
const browserClients = new Set();
let deviceSocket = null;

function sendJson(response, status, body) {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  response.end(JSON.stringify(body));
}

function authorized(request) {
  if (!DEVICE_TOKEN) return false;
  const supplied = Buffer.from(request.headers.authorization || '');
  const expected = Buffer.from(`Bearer ${DEVICE_TOKEN}`);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
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
  const online = deviceSocket?.readyState === WebSocket.OPEN &&
    state.updated_at !== null && (Date.now() - Date.parse(state.updated_at)) < 10_000;
  return { ...state, online };
}

function currentCommand() {
  return {
    type: 'command',
    data: {
      mode: state.desired_mode,
      angle: state.desired_angle,
      threshold_cm: state.desired_threshold_cm,
      revision: state.revision,
    },
  };
}

function sendWs(socket, message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function broadcast(message) {
  const encoded = JSON.stringify(message);
  for (const client of browserClients) {
    if (client.readyState === WebSocket.OPEN) client.send(encoded);
  }
}

function broadcastState() {
  broadcast({ type: 'state', data: snapshot() });
}

function applyTelemetry(body) {
  if (!isObject(body)) throw new Error('La telemetría debe ser un objeto JSON.');
  const angle = Number(body.angle);
  const distanceCm = Number(body.distance_cm);
  const thresholdCm = Number(body.threshold_cm);
  const mode = String(body.mode || '').toUpperCase();
  if (!Number.isInteger(angle) || angle < 0 || angle > 180 ||
      !Number.isInteger(distanceCm) || distanceCm < -1 || distanceCm > 500 ||
      !Number.isInteger(thresholdCm) || thresholdCm < 5 || thresholdCm > 100 ||
      !['AUTO', 'MANUAL'].includes(mode)) {
    throw new Error('Los valores de telemetría están fuera del rango permitido.');
  }

  state.angle = angle;
  state.distance_cm = distanceCm;
  state.threshold_cm = thresholdCm;
  state.mode = mode;
  state.updated_at = new Date().toISOString();
}

function applyCommand(body) {
  if (!isObject(body)) throw new Error('El comando debe ser un objeto JSON.');
  const mode = String(body.mode || '').toUpperCase();
  const thresholdCm = Number(body.threshold_cm ?? state.desired_threshold_cm);
  const angle = Number(body.angle ?? 0);
  if (!['AUTO', 'MANUAL'].includes(mode)) throw new Error('mode debe ser AUTO o MANUAL.');
  if (!Number.isInteger(thresholdCm) || thresholdCm < 5 || thresholdCm > 100) {
    throw new Error('El umbral debe ser un entero entre 5 y 100 cm.');
  }
  if (mode === 'MANUAL' && (!Number.isInteger(angle) || angle < 0 || angle > 180)) {
    throw new Error('El ángulo manual debe ser un entero entre 0 y 180.');
  }

  state.desired_mode = mode;
  state.desired_angle = mode === 'MANUAL' ? angle : 0;
  state.desired_threshold_cm = thresholdCm;
  state.revision += 1;
  const command = currentCommand().data;
  sendWs(deviceSocket, { type: 'command', data: command });
  broadcastState();
  return command;
}

function rejectUpgrade(socket, status, message) {
  socket.write(`HTTP/1.1 ${status} ${message}\r\nConnection: close\r\n\r\n`);
  socket.destroy();
}

server.on('upgrade', (request, socket, head) => {
  const url = new URL(request.url || '/', `http://${request.headers.host || 'localhost'}`);
  if (url.pathname !== '/ws') {
    rejectUpgrade(socket, 404, 'Not Found');
    return;
  }

  const hasAuthorization = Boolean(request.headers.authorization);
  const role = hasAuthorization ? 'device' : 'browser';
  if (role === 'device' && !authorized(request)) {
    rejectUpgrade(socket, 401, 'Unauthorized');
    return;
  }
  if (role === 'browser' && request.headers.origin) {
    try {
      if (new URL(request.headers.origin).host !== request.headers.host) {
        rejectUpgrade(socket, 403, 'Forbidden');
        return;
      }
    } catch {
      rejectUpgrade(socket, 400, 'Bad Request');
      return;
    }
  }

  webSockets.handleUpgrade(request, socket, head, (client) => {
    client.role = role;
    webSockets.emit('connection', client, request);
  });
});

webSockets.on('connection', (socket) => {
  socket.isAlive = true;
  socket.on('pong', () => { socket.isAlive = true; });

  if (socket.role === 'device') {
    if (deviceSocket && deviceSocket !== socket) deviceSocket.close(1012, 'Device reconnected');
    deviceSocket = socket;
    sendWs(socket, currentCommand());
    broadcastState();
  } else {
    browserClients.add(socket);
    sendWs(socket, { type: 'state', data: snapshot() });
  }

  socket.on('message', (raw) => {
    let message;
    try {
      message = JSON.parse(raw.toString());
    } catch {
      sendWs(socket, { type: 'error', message: 'Mensaje JSON inválido.' });
      return;
    }

    if (socket.role === 'device') {
      if (message.type === 'telemetry') {
        try {
          applyTelemetry(message.data);
          broadcastState();
        } catch (error) {
          sendWs(socket, { type: 'error', message: error.message });
        }
      } else if (message.type === 'sync') {
        sendWs(socket, currentCommand());
      }
      return;
    }

    if (message.type === 'command') {
      try {
        const command = applyCommand(message.data);
        sendWs(socket, { type: 'ack', data: command });
      } catch (error) {
        sendWs(socket, { type: 'error', message: error.message });
      }
    }
  });

  socket.on('close', () => {
    browserClients.delete(socket);
    if (deviceSocket === socket) deviceSocket = null;
    broadcastState();
  });
  socket.on('error', (error) => console.error('WebSocket error:', error.message));
});

const heartbeat = setInterval(() => {
  for (const socket of webSockets.clients) {
    if (socket.isAlive === false) {
      socket.terminate();
      continue;
    }
    socket.isAlive = false;
    socket.ping();
  }
}, 25_000);

webSockets.on('close', () => clearInterval(heartbeat));

async function handleHttp(request, response) {
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
    sendJson(response, 200, currentCommand().data);
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
      applyTelemetry(body);
    } catch (error) {
      const status = error.message === 'body_too_large' ? 413 : 400;
      sendJson(response, status, { error: error.message });
      return;
    }
    broadcastState();
    sendJson(response, 200, { ok: true });
    return;
  }

  if (route === 'POST /api/command') {
    let body;
    try {
      body = await readJson(request);
      const command = applyCommand(body);
      sendJson(response, 200, command);
    } catch (error) {
      const status = error.message === 'body_too_large' ? 413 : 400;
      sendJson(response, status, { error: error.message });
    }
    return;
  }

  sendJson(response, 404, { error: 'Not found.' });
}

server.listen(PORT, '0.0.0.0', () => {
  console.log(`IoT WebSocket service listening on port ${PORT}`);
});
