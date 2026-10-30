import os
import threading
from datetime import datetime, timezone

from flask import Flask, jsonify, request


app = Flask(__name__)
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
state_lock = threading.Lock()
state = {
    "angle": 0,
    "distance_cm": None,
    "threshold_cm": 15,
    "mode": "AUTO",
    "desired_mode": "AUTO",
    "desired_angle": 0,
    "desired_threshold_cm": 15,
    "revision": 0,
    "updated_at": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def device_is_authorized() -> bool:
    if not DEVICE_TOKEN:
        # Fallar cerrado si no se configuró el secreto.
        return False
    return request.headers.get("Authorization", "") == f"Bearer {DEVICE_TOKEN}"


@app.after_request
def prevent_api_caching(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/api/state")
def get_state():
    with state_lock:
        result = dict(state)
    result["online"] = False
    if result["updated_at"]:
        try:
            updated = datetime.fromisoformat(result["updated_at"])
            result["online"] = (datetime.now(timezone.utc) - updated).total_seconds() < 10
        except ValueError:
            pass
    return jsonify(result)


@app.get("/api/command")
def get_command():
    if not device_is_authorized():
        return jsonify(error="unauthorized"), 401
    with state_lock:
        return jsonify(
            mode=state["desired_mode"],
            angle=state["desired_angle"],
            threshold_cm=state["desired_threshold_cm"],
            revision=state["revision"],
        )


@app.post("/api/telemetry")
def post_telemetry():
    if not device_is_authorized():
        return jsonify(error="unauthorized"), 401
    body = request.get_json(silent=True) or {}
    try:
        angle = int(body["angle"])
        distance_cm = int(body["distance_cm"])
        threshold_cm = int(body["threshold_cm"])
        mode = str(body["mode"]).upper()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Se requieren angle, distance_cm, threshold_cm y mode válidos."), 400
    if (not 0 <= angle <= 180 or not -1 <= distance_cm <= 500 or
            not 5 <= threshold_cm <= 100 or mode not in ("AUTO", "MANUAL")):
        return jsonify(error="Telemetría fuera de rango."), 400

    with state_lock:
        state["angle"] = angle
        state["distance_cm"] = distance_cm
        state["threshold_cm"] = threshold_cm
        state["mode"] = mode
        state["updated_at"] = utc_now()
    return jsonify(ok=True)


@app.post("/api/command")
def set_command():
    # La página de control es pública para la demostración. Cualquier persona
    # con la URL puede cambiar el modo y controlar la barrera manualmente.
    body = request.get_json(silent=True) or {}
    try:
        mode = str(body["mode"]).upper()
        with state_lock:
            threshold_cm = int(body.get("threshold_cm", state["desired_threshold_cm"]))
        angle = int(body.get("angle", 0))
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Envía mode, threshold_cm y, para modo manual, angle."), 400
    if not 5 <= threshold_cm <= 100:
        return jsonify(error="El umbral debe estar entre 5 y 100 cm."), 400
    if mode not in ("AUTO", "MANUAL"):
        return jsonify(error="mode debe ser AUTO o MANUAL."), 400
    if mode == "MANUAL" and not 0 <= angle <= 180:
        return jsonify(error="En modo manual angle debe estar entre 0 y 180 grados."), 400
    if mode == "AUTO":
        angle = 0

    with state_lock:
        state["desired_mode"] = mode
        state["desired_angle"] = angle
        state["desired_threshold_cm"] = threshold_cm
        state["revision"] += 1
        result = {
            "mode": mode,
            "angle": angle,
            "threshold_cm": threshold_cm,
            "revision": state["revision"],
        }
    return jsonify(result)


@app.get("/")
def dashboard():
    return DASHBOARD, 200, {"Content-Type": "text/html; charset=utf-8"}


DASHBOARD = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Barrera de proximidad · IoT</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, sans-serif; background: #f2f5f9; color: #172033; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; }
    main { width: min(100%, 620px); }
    .card { background: white; border: 1px solid #e2e8f0; border-radius: 20px; padding: 28px; box-shadow: 0 12px 36px #15233a12; }
    .eyebrow { color: #536987; font-size: .78rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }
    h1 { margin: 8px 0 4px; font-size: clamp(1.7rem, 5vw, 2.25rem); }
    .sub { color: #5c6b80; margin-top: 0; }
    .status { display: inline-flex; align-items: center; gap: 8px; margin: 14px 0 22px; padding: 8px 12px; border-radius: 999px; background: #edf2f7; font-weight: 650; font-size: .9rem; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: #94a3b8; }
    .online .dot { background: #16a34a; }
    .offline .dot { background: #dc2626; }
    .metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 24px; }
    .metric { padding: 14px; border-radius: 14px; background: #f6f8fb; }
    .metric small { display: block; color: #64748b; margin-bottom: 5px; }
    .metric strong { font-size: 1.25rem; }
    label { display: flex; justify-content: space-between; align-items: center; font-weight: 700; }
    output { color: #2563eb; font-size: 1.2rem; }
    input[type=range] { width: 100%; margin: 20px 0 8px; accent-color: #2563eb; }
    .range-ends { display: flex; justify-content: space-between; color: #64748b; font-size: .85rem; }
    button { margin-top: 20px; width: 100%; padding: 13px 18px; border: 0; border-radius: 12px; color: white; background: #2563eb; font: inherit; font-weight: 750; cursor: pointer; }
    .button-row { display: grid; grid-template-columns: 1fr; gap: 10px; }
    .button-row button { margin-top: 10px; }
    button.secondary { background: #334155; }
    button:disabled { opacity: .6; cursor: wait; }
    #message { min-height: 1.4em; color: #536987; font-size: .9rem; margin: 12px 0 0; }
    footer { text-align: center; color: #64748b; font-size: .82rem; margin-top: 16px; }
    @media (max-width: 480px) { .card { padding: 21px; } .metrics { gap: 7px; } .metric { padding: 11px 8px; } .metric strong { font-size: 1.05rem; } }
  </style>
</head>
<body>
<main>
  <section class="card">
    <div class="eyebrow">Proyecto IoT · Arduino + Orange Pi + Cloud Run</div>
    <h1>Barrera de proximidad</h1>
    <p class="sub">En automático, el HC-SR04 traduce la distancia a una posición proporcional del SG90. En manual puedes elegir cualquier ángulo.</p>
    <div id="status" class="status"><span class="dot"></span><span id="statusText">Conectando…</span></div>
    <div class="metrics">
      <div class="metric"><small>Distancia</small><strong id="distance">—</strong></div>
      <div class="metric"><small>Ángulo servo</small><strong id="servoAngle">—°</strong></div>
      <div class="metric"><small>Modo</small><strong id="modeState">Automático</strong></div>
    </div>
    <p id="appliedThreshold" class="sub">Umbral aplicado: 15 cm</p>
    <label for="threshold"><span>Umbral para modo automático</span><output id="thresholdOutput">15 cm</output></label>
    <input id="threshold" type="range" min="5" max="100" value="15">
    <div class="range-ends"><span>5 cm</span><span>100 cm</span></div>
    <label for="manualAngle"><span>Posición manual</span><output id="manualAngleOutput">0°</output></label>
    <input id="manualAngle" type="range" min="0" max="180" value="0">
    <div class="range-ends"><span>0°</span><span>180°</span></div>
    <div class="button-row">
      <button id="autoButton" type="button">Aplicar umbral y activar automático</button>
      <button id="manualButton" class="secondary" type="button">Forzar ángulo manual</button>
    </div>
    <p class="sub">El modo manual mantiene el ángulo elegido aunque cambie la distancia, hasta que vuelvas al automático.</p>
    <p id="message" role="status"></p>
  </section>
  <footer>Arduino mide y controla el servo; la Orange Pi sincroniza los datos con este servicio.</footer>
</main>
<script>
  const slider = document.querySelector('#threshold');
  const output = document.querySelector('#thresholdOutput');
  const manualSlider = document.querySelector('#manualAngle');
  const manualOutput = document.querySelector('#manualAngleOutput');
  const controlButtons = [
    document.querySelector('#autoButton'),
    document.querySelector('#manualButton')
  ];
  const message = document.querySelector('#message');
  let thresholdEdited = false;
  let manualAngleEdited = false;
  slider.addEventListener('input', () => {
    thresholdEdited = true;
    output.value = `${slider.value} cm`;
  });
  manualSlider.addEventListener('input', () => {
    manualAngleEdited = true;
    manualOutput.value = `${manualSlider.value}°`;
  });
  async function sendCommand(mode, angle = 0) {
    controlButtons.forEach(button => button.disabled = true);
    message.textContent = 'Enviando…';
    try {
      const response = await fetch('/api/command', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, angle, threshold_cm: Number(slider.value) })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Error al enviar');
      if (mode === 'AUTO') thresholdEdited = false;
      if (mode === 'MANUAL') manualAngleEdited = false;
      message.textContent = mode === 'AUTO'
        ? `Modo automático activado con umbral de ${result.threshold_cm} cm.`
        : `Modo manual: servo a ${angle}°.`;
    } catch (error) {
      message.textContent = `No se pudo enviar: ${error.message}`;
    } finally { controlButtons.forEach(button => button.disabled = false); }
  }
  controlButtons[0].addEventListener('click', () => sendCommand('AUTO'));
  controlButtons[1].addEventListener('click', () => sendCommand('MANUAL', Number(manualSlider.value)));
  async function refresh() {
    try {
      const response = await fetch('/api/state', { cache: 'no-store' });
      const s = await response.json();
      document.querySelector('#distance').textContent = s.distance_cm < 0 || s.distance_cm === null
        ? 'Sin eco' : `${s.distance_cm} cm`;
      document.querySelector('#servoAngle').textContent = `${s.angle}°`;
      document.querySelector('#modeState').textContent = s.mode === 'MANUAL' ? 'Manual' : 'Automático';
      document.querySelector('#appliedThreshold').textContent = `Umbral aplicado: ${s.threshold_cm} cm`;
      document.querySelector('#status').className = `status ${s.online ? 'online' : 'offline'}`;
      document.querySelector('#statusText').textContent = s.online ? 'Arduino conectado' : 'Sin datos recientes';
      if (!thresholdEdited) {
        slider.value = s.desired_threshold_cm;
        output.value = `${s.desired_threshold_cm} cm`;
      }
      if (!manualAngleEdited) {
        manualSlider.value = s.desired_angle;
        manualOutput.value = `${s.desired_angle}°`;
      }
    } catch (_) {
      document.querySelector('#status').className = 'status offline';
      document.querySelector('#statusText').textContent = 'Servicio no disponible';
    }
  }
  refresh();
  setInterval(refresh, 1000);
</script>
</body>
</html>"""
