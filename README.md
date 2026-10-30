# Control IoT con Arduino, Orange Pi y Cloud Run

El HC-SR04 mide la distancia a un objeto. En modo automático, si está dentro del umbral configurado (15 cm inicialmente), el Arduino mueve el servo SG90 para abrir una barrera; cuando se aleja, la cierra. La Orange Pi Zero 2W retransmite la distancia y el estado a Cloud Run. Desde la página web se puede consultar la lectura, cambiar el umbral entre 5 y 100 cm y activar un override manual para forzar la apertura o el cierre. El modo manual permanece activo hasta volver al automático.

## Archivos

- `arduino/hc_sr04_barrier.ino`: firmware para Arduino Uno R3.
- `orangepi/bridge.py`: puente USB serial y cliente del servicio.
- `cloudrun/main.py`: API HTTP y página de control.
- `cloudrun/Dockerfile`: imagen para desplegar en Cloud Run.

## Conexiones

| Componente | Pin del Arduino Uno R3 |
|---|---|
| HC-SR04 TRIG | D2 |
| HC-SR04 ECHO | D3 |
| HC-SR04 VCC / GND | 5V / GND |
| SG90 señal | D9 |
| SG90 alimentación | 5 V externa, con GND común al Arduino |

No alimentes el SG90 desde el pin 5V del Arduino. Conecta Arduino al USB de la Orange Pi. En algunos clones el puerto serial podría aparecer como `/dev/ttyUSB0` en vez de `/dev/ttyACM0`.

## Subir el firmware

1. Abre `arduino/hc_sr04_barrier.ino` en Arduino IDE.
2. Selecciona **Arduino Uno** y el puerto conectado.
3. Sube el programa. El monitor serial usa 115200 baudios.

El umbral inicial es 15 cm. Arduino envía por USB `STATE,ángulo,distancia_cm,umbral_cm,modo`. Orange Pi manda `A,umbral_cm` para volver al automático o `M,ángulo,umbral_cm` para el modo manual (ángulo 0 cierra y 90 abre). Si el sensor no recibe eco, informa distancia `-1`; en automático la barrera queda cerrada. Los mensajes terminan en salto de línea.

## Desplegar el servicio en Google Cloud Run

Activa Cloud Run y Artifact Registry en el proyecto de Google Cloud y configura `gcloud` con ese proyecto. Genera un secreto compartido, por ejemplo con `openssl rand -hex 32`, y guárdalo en Secret Manager. Luego, desde la carpeta `cloudrun`, crea el secreto y despliega:

```sh
printf '%s' 'PEGA_AQUI_EL_SECRETO' | gcloud secrets create iot-device-token --data-file=-
gcloud run deploy arduino-orange-pi --source . --region us-central1 \
  --allow-unauthenticated --max 1 --min 0 \
  --set-secrets DEVICE_TOKEN=iot-device-token:latest
```

Si el secreto ya existe, crea una versión nueva con `gcloud secrets versions add iot-device-token --data-file=-`. Asegura que la cuenta de servicio de Cloud Run pueda leerlo (`Secret Manager Secret Accessor`). `--min 0` permite que Cloud Run escale a cero si no recibe solicitudes; el puente de la Orange Pi lo consulta periódicamente mientras está encendida. Anota la URL HTTPS que muestra `gcloud run deploy`.

## Preparar la Orange Pi Zero 2W

Instala Python 3, `pip` y las herramientas de compilación de `pyserial` si la distribución las requiere. Copia o clona este proyecto en la Orange Pi y ejecuta:

```sh
cd orangepi
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export CLOUD_RUN_URL='https://TU-SERVICIO-xxxxx-uc.a.run.app'
export DEVICE_TOKEN='EL_MISMO_SECRETO_DE_SECRET_MANAGER'
python bridge.py
```

Para obtener el valor del token desde una terminal autenticada de Google Cloud, ejecuta `gcloud secrets versions access latest --secret=iot-device-token` y asígnalo a `DEVICE_TOKEN` en la Orange Pi. No publiques ese valor en el repositorio.

Por defecto el script busca puertos USB tipo `/dev/ttyACM*` y `/dev/ttyUSB*`, prueba cada candidato y acepta el que transmite mensajes `STATE` del firmware. Así reconoce el Arduino aunque Linux le asigne otro nombre después de reiniciar. Si hay varios dispositivos seriales o quieres fijar uno, establece `SERIAL_PORT`, por ejemplo `export SERIAL_PORT='/dev/ttyACM0'`. El usuario que ejecuta Python debe tener permiso de acceso al puerto serial (normalmente pertenecer al grupo `dialout`; el nombre del grupo puede variar).

## Flujo y rutas

- `GET /`: panel web para ver el estado y solicitar una posición.
- `GET /api/state`: último estado observado por la Orange Pi.
- `POST /api/command`: controla el modo desde la web. Automático: `{ "mode": "AUTO", "threshold_cm": 25 }`. Override manual abierto: `{ "mode": "MANUAL", "angle": 90 }`; cerrado: `{ "mode": "MANUAL", "angle": 0 }`.
- `GET /api/command?since=REVISION`: comando pendiente para la Orange Pi.
- `POST /api/telemetry`: telemetría de la Orange Pi, protegida con Bearer token.
- `GET /health`: verificación básica del servicio.

## Alcance de la demo

La API guarda el estado en memoria y está configurada para una instancia. Esto simplifica la práctica, pero se pierde el estado cuando Cloud Run reinicia o elimina la instancia. Para una versión de producción se debe guardar el estado y los comandos en Firestore o una base de datos. El panel queda público con `--allow-unauthenticated`; cualquier persona con la URL puede cambiar el modo o mover la barrera. El token solo se configura en la Orange Pi y protege sus llamadas de dispositivo.
