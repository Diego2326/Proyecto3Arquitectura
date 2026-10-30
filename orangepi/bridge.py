#!/usr/bin/env python3
"""Puente USB serial entre Arduino y el servicio web desplegado en Cloud Run."""

import os
import time
from typing import Optional

import requests
import serial
from serial.tools import list_ports


CLOUD_RUN_URL = os.environ.get("CLOUD_RUN_URL", "").rstrip("/")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
SERIAL_PORT = os.environ.get("SERIAL_PORT", "").strip()
BAUD_RATE = int(os.environ.get("BAUD_RATE", "115200"))
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "1.0"))
TELEMETRY_INTERVAL_SECONDS = float(os.environ.get("TELEMETRY_INTERVAL_SECONDS", "1.0"))


def parse_arduino_line(line: bytes) -> Optional[dict]:
    """Interpreta STATE,<ángulo>,<distancia_cm>,<umbral_cm>,<modo>."""
    try:
        fields = line.decode("ascii", errors="strict").strip().split(",")
        if len(fields) != 5 or fields[0] != "STATE":
            return None
        angle, distance_cm, threshold_cm = map(int, fields[1:4])
        mode = fields[4]
        if angle not in (0, 90) or not -1 <= distance_cm <= 500:
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


def candidate_ports() -> list:
    """Lista puertos USB serial; prioriza Arduino y adaptadores USB comunes."""
    if SERIAL_PORT:
        return [SERIAL_PORT]

    usb_serial_ports = []
    for port in list_ports.comports():
        device = port.device or ""
        if not (device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB")):
            continue

        # Arduino oficial y adaptadores frecuentes en clones del Uno.
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
                # Al abrir el puerto, el Uno se reinicia. Espera el mensaje del firmware.
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


def run_bridge() -> None:
    if not CLOUD_RUN_URL:
        raise SystemExit("Configura CLOUD_RUN_URL con la URL base del servicio de Cloud Run.")
    if not DEVICE_TOKEN:
        raise SystemExit("Configura DEVICE_TOKEN con el mismo secreto que usa Cloud Run.")

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {DEVICE_TOKEN}"})
    last_revision = -1
    last_command = None
    latest_telemetry = None
    next_poll_at = 0.0
    next_telemetry_at = 0.0

    mode = SERIAL_PORT or "detección automática USB"
    print(f"Serial: {mode} a {BAUD_RATE} baudios; servicio: {CLOUD_RUN_URL}")
    while True:
        arduino, initial_telemetry = detect_arduino()
        # Al reconectar, el Uno pudo reiniciarse y perder el modo manual.
        # Fuerza una nueva sincronización del comando actual de Cloud Run.
        last_revision = -1
        last_command = None
        next_poll_at = 0.0
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

                    now = time.monotonic()
                    if now >= next_poll_at:
                        try:
                            response = session.get(
                                f"{CLOUD_RUN_URL}/api/command",
                                params={"since": last_revision},
                                timeout=5,
                            )
                            response.raise_for_status()
                            command = response.json()
                            revision = int(command["revision"])
                            mode = str(command["mode"])
                            angle = int(command["angle"])
                            threshold_cm = int(command["threshold_cm"])
                            command_values = (mode, angle, threshold_cm)
                            if revision != last_revision or command_values != last_command:
                                if mode == "AUTO" and 5 <= threshold_cm <= 100:
                                    arduino.write(f"A,{threshold_cm}\n".encode("ascii"))
                                    last_revision = revision
                                    last_command = command_values
                                    print(f"Modo automático: umbral {threshold_cm} cm (rev. {revision})")
                                elif mode == "MANUAL" and angle in (0, 90) and 5 <= threshold_cm <= 100:
                                    arduino.write(f"M,{angle},{threshold_cm}\n".encode("ascii"))
                                    last_revision = revision
                                    last_command = command_values
                                    action = "abrir" if angle == 90 else "cerrar"
                                    print(f"Modo manual: {action} barrera (rev. {revision})")
                        except (requests.RequestException, ValueError, KeyError) as error:
                            print(f"No se pudo consultar el comando: {error}")
                        next_poll_at = now + POLL_INTERVAL_SECONDS

                    if latest_telemetry is not None and now >= next_telemetry_at:
                        try:
                            response = session.post(
                                f"{CLOUD_RUN_URL}/api/telemetry",
                                json=latest_telemetry,
                                timeout=5,
                            )
                            response.raise_for_status()
                        except requests.RequestException as error:
                            print(f"No se pudo enviar la telemetría: {error}")
                        next_telemetry_at = now + TELEMETRY_INTERVAL_SECONDS

        except (serial.SerialException, OSError) as error:
            print(f"Se perdió la conexión USB: {error}. Busco de nuevo el Arduino.")
            time.sleep(3)


if __name__ == "__main__":
    run_bridge()
