#!/usr/bin/env python3
"""Puente WebSocket entre Arduino por USB y el panel Cloud Run."""

import json
import os
import queue
import threading
import time
from typing import Optional
from urllib.parse import urlparse, urlunparse

import serial
from serial.tools import list_ports
from websocket import WebSocketApp


CLOUD_RUN_URL = os.environ.get(
    "CLOUD_RUN_URL",
    "https://proyecto3arquitectura-990072178406.northamerica-south1.run.app",
).rstrip("/")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
SERIAL_PORT = os.environ.get("SERIAL_PORT", "").strip()
BAUD_RATE = int(os.environ.get("BAUD_RATE", "115200"))
TELEMETRY_INTERVAL_SECONDS = float(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "0.5"))

commands: queue.Queue = queue.Queue(maxsize=1)
socket_lock = threading.Lock()
active_socket: Optional[WebSocketApp] = None


def parse_arduino_line(line: bytes) -> Optional[dict]:
    """Interpreta STATE,<ángulo>,<distancia_cm>,<umbral_cm>,<modo>."""
    try:
        fields = line.decode("ascii", errors="strict").strip().split(",")
        if len(fields) != 5 or fields[0] != "STATE":
            return None
        angle, distance_cm, threshold_cm = map(int, fields[1:4])
        mode = fields[4]
        if not 0 <= angle <= 180 or not -1 <= distance_cm <= 500:
            return None
        if not 5 <= threshold_cm <= 100 or mode not in ("AUTO", "MANUAL"):
            return None
        return {
            "angle": angle,
            "distance_cm": distance_cm,
            "threshold_cm": threshold_cm,
            "mode": mode,
        }
    except (UnicodeDecodeError, ValueError):
        return None


def websocket_url() -> str:
    if not CLOUD_RUN_URL:
        raise SystemExit("Configura CLOUD_RUN_URL con la URL base de Cloud Run.")
    parsed = urlparse(CLOUD_RUN_URL)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise SystemExit("CLOUD_RUN_URL debe ser una URL http(s) válida.")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/ws"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def candidate_ports() -> list:
    """Lista puertos USB serial; prioriza Arduino y adaptadores USB comunes."""
    if SERIAL_PORT:
        return [SERIAL_PORT]

    usb_serial_ports = []
    known_usb_ids = {
        (0x2341, 0x0043),  # Arduino Uno R3 (ATmega16U2)
        (0x2341, 0x0001),
        (0x2A03, 0x0043),
        (0x2A03, 0x0001),
        (0x1A86, 0x7523),  # CH340
        (0x1A86, 0x5523),
        (0x0403, 0x6001),  # FTDI
        (0x10C4, 0xEA60),  # CP210x
    }
    for port in list_ports.comports():
        device = port.device or ""
        if not (device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB")):
            continue
        identity = (port.vid, port.pid)
        description = (port.description or "").lower()
        priority = 0 if identity in known_usb_ids else 1
        if "arduino" in description or "ch340" in description:
            priority = 0
        usb_serial_ports.append((priority, device))

    return [device for _, device in sorted(usb_serial_ports)]


def detect_arduino():
    """Abre y reconoce el Arduino leyendo un mensaje STATE del firmware."""
    while True:
        ports = candidate_ports()
        if not ports:
            print("No encuentro puertos Arduino USB; reviso de nuevo en 3 segundos.")

        for port in ports:
            arduino = None
            try:
                print(f"Probando puerto USB {port}…")
                arduino = serial.Serial(port, BAUD_RATE, timeout=0.2, write_timeout=1)
                # Al abrir el puerto, el Uno se reinicia. Espera el protocolo del firmware.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    telemetry = parse_arduino_line(arduino.readline())
                    if telemetry is not None:
                        print(f"Arduino detectado en {port}.")
                        return arduino, telemetry
                print(f"{port} no respondió con el protocolo esperado.")
            except (serial.SerialException, OSError) as error:
                print(f"No pude abrir {port}: {error}")
            if arduino is not None and arduino.is_open:
                arduino.close()

        time.sleep(3)


def queue_latest_command(command: dict) -> None:
    """Conserva el comando más reciente si llegaron varios durante una pausa."""
    try:
        commands.put_nowait(command)
    except queue.Full:
        try:
            commands.get_nowait()
        except queue.Empty:
            pass
        try:
            commands.put_nowait(command)
        except queue.Full:
            pass


def on_open(socket: WebSocketApp) -> None:
    global active_socket
    with socket_lock:
        active_socket = socket
    print("WebSocket conectado a Cloud Run.")
    socket.send(json.dumps({"type": "sync"}))


def on_message(_socket: WebSocketApp, raw_message: str) -> None:
    try:
        message = json.loads(raw_message)
    except (TypeError, json.JSONDecodeError):
        print("Cloud Run envió un mensaje JSON inválido.")
        return

    if message.get("type") == "command" and isinstance(message.get("data"), dict):
        data = message["data"]
        try:
            mode = str(data["mode"]).upper()
            angle = int(data.get("angle", 0))
            threshold_cm = int(data["threshold_cm"])
            revision = int(data.get("revision", 0))
        except (KeyError, TypeError, ValueError):
            print("Cloud Run envió un comando incompleto.")
            return
        if mode not in ("AUTO", "MANUAL") or not 5 <= threshold_cm <= 100:
            print("Cloud Run envió un comando fuera de rango.")
            return
        if mode == "MANUAL" and not 0 <= angle <= 180:
            print("Cloud Run envió un ángulo fuera del rango 0–180°.")
            return
        queue_latest_command({
            "mode": mode,
            "angle": angle,
            "threshold_cm": threshold_cm,
            "revision": revision,
        })
    elif message.get("type") == "error":
        print(f"Error de Cloud Run: {message.get('message', 'sin detalle')}")


def on_error(_socket: WebSocketApp, error: Exception) -> None:
    print(f"WebSocket: {error}")


def on_close(socket: WebSocketApp, status_code, _message) -> None:
    global active_socket
    with socket_lock:
        if active_socket is socket:
            active_socket = None
    print(f"WebSocket desconectado ({status_code}); intentaré reconectar.")


def websocket_worker(url: str) -> None:
    """Mantiene el WebSocket y reconecta si Cloud Run cierra la sesión."""
    delay = 1.0
    while True:
        socket = WebSocketApp(
            url,
            header={"Authorization": f"Bearer {DEVICE_TOKEN}"},
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        try:
            socket.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as error:  # la conexión se reintenta ante cortes de red
            print(f"No se pudo mantener WebSocket: {error}")
        finally:
            global active_socket
            with socket_lock:
                if active_socket is socket:
                    active_socket = None
        time.sleep(delay)
        delay = min(delay * 1.7, 10.0)


def send_ws(message: dict) -> bool:
    with socket_lock:
        socket = active_socket
    if socket is None:
        return False
    try:
        socket.send(json.dumps(message, separators=(",", ":")))
        return True
    except Exception as error:
        print(f"No se pudo enviar por WebSocket: {error}")
        return False


def apply_serial_command(arduino, command: dict) -> None:
    mode = command["mode"]
    threshold_cm = command["threshold_cm"]
    if mode == "AUTO":
        arduino.write(f"A,{threshold_cm}\n".encode("ascii"))
        print(f"Modo automático: umbral {threshold_cm} cm (rev. {command['revision']})")
    else:
        angle = command["angle"]
        arduino.write(f"M,{angle},{threshold_cm}\n".encode("ascii"))
        print(f"Modo manual: objetivo {angle}° (rev. {command['revision']})")


def run_bridge() -> None:
    if not DEVICE_TOKEN:
        raise SystemExit("Configura DEVICE_TOKEN con el mismo secreto que usa Cloud Run.")
    url = websocket_url()
    threading.Thread(target=websocket_worker, args=(url,), daemon=True).start()

    port_mode = SERIAL_PORT or "detección automática USB"
    print(f"Serial: {port_mode} a {BAUD_RATE} baudios; WebSocket: {url}")
    while True:
        arduino, initial_telemetry = detect_arduino()
        # El Uno vuelve a AUTO al reiniciarse: solicita el último comando del servidor.
        send_ws({"type": "sync"})
        next_telemetry_at = 0.0
        try:
            with arduino:
                latest_telemetry = initial_telemetry
                while True:
                    line = arduino.readline()
                    if line:
                        telemetry = parse_arduino_line(line)
                        if telemetry is not None:
                            latest_telemetry = telemetry

                    try:
                        command = commands.get_nowait()
                    except queue.Empty:
                        command = None
                    if command is not None:
                        apply_serial_command(arduino, command)

                    now = time.monotonic()
                    if latest_telemetry is not None and now >= next_telemetry_at:
                        send_ws({"type": "telemetry", "data": latest_telemetry})
                        next_telemetry_at = now + TELEMETRY_INTERVAL_SECONDS

        except (serial.SerialException, OSError) as error:
            print(f"Se perdió la conexión USB: {error}. Busco de nuevo el Arduino.")
            time.sleep(3)


if __name__ == "__main__":
    try:
        run_bridge()
    except KeyboardInterrupt:
        print("Puente detenido.")
