# Control IoT con Arduino, Orange Pi y Cloud Run

El HC-SR04 mide la distancia a un objeto. En modo automático, el Arduino convierte la distancia en un ángulo proporcional: a 5 cm o menos apunta a 180°, y al llegar al umbral configurado (15 cm inicialmente) apunta a 0°. Entre esos puntos el ángulo cambia gradualmente, y el servo recorre la posición a pasos cortos. La Orange Pi Zero 2W retransmite la distancia y el estado a Cloud Run. En la web se puede cambiar el umbral y escoger manualmente cualquier ángulo de 0° a 180°; el override permanece activo hasta volver al automático.

## Archivos

- `arduino/hc_sr04_barrier/hc_sr04_barrier.ino`: firmware para Arduino Uno R3.
- `orangepi/bridge.py`: puente USB serial y cliente del servicio.
- `cloudrun/server.js`: API HTTP para Cloud Run con Node.js.
- `cloudrun/index.html`: panel web minimalista y adaptable a móvil.
- `cloudrun/Dockerfile`: imagen para desplegar en Cloud Run.

El servicio de Cloud Run usa Node.js 24 LTS y módulos integrados de Node, sin dependencias npm externas.

## Conexiones

| Componente | Pin del Arduino Uno R3 |
|---|---|
| HC-SR04 TRIG | D2 |
| HC-SR04 ECHO | D3 |
| HC-SR04 VCC / GND | 5V / GND |
| SG90 señal | D9 |
| SG90 alimentación | Fuente regulada externa de 5 V, con GND común al Arduino |

No alimentes el SG90 desde el pin 5V del Arduino. Aunque la Orange Pi tiene pines de 5 V, su alimentación principal recomendada es 5 V/2 A o 5 V/3 A para la propia placa; para evitar bajones cuando el servo arranca o cambia de posición, usa una fuente regulada separada para el SG90 y une su GND al GND del Arduino. Conecta Arduino al USB de la Orange Pi. En algunos clones el puerto serial podría aparecer como `/dev/ttyUSB0` en vez de `/dev/ttyACM0`.

## Subir el firmware

1. Abre `arduino/hc_sr04_barrier/hc_sr04_barrier.ino` en Arduino IDE.
2. Selecciona **Arduino Uno** y el puerto conectado.
3. Sube el programa. El monitor serial usa 115200 baudios.

El umbral inicial es 15 cm. Arduino envía por USB `STATE,ángulo,distancia_cm,umbral_cm,modo`. Orange Pi manda `A,umbral_cm` para volver al automático o `M,ángulo,umbral_cm` para fijar cualquier ángulo manual entre 0 y 180. Si el sensor pierde uno o dos ecos, conserva la posición filtrada; después de tres lecturas sin eco, en automático apunta a 0°. Los mensajes terminan en salto de línea.

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

- `GET /`: panel web para ver el estado y controlar el servo.
- `GET /api/state`: último estado observado por la Orange Pi.
- `POST /api/command`: controla el modo desde la web. Automático: `{ "mode": "AUTO", "threshold_cm": 25 }`. Override manual: `{ "mode": "MANUAL", "angle": 123 }`.
- `GET /api/command?since=REVISION`: comando pendiente para la Orange Pi.
- `POST /api/telemetry`: telemetría de la Orange Pi, protegida con Bearer token.
- `GET /health`: verificación básica del servicio.

## Alcance de la demo

La API guarda el estado en memoria y está configurada para una instancia. Esto simplifica la práctica, pero se pierde el estado cuando Cloud Run reinicia o elimina la instancia. Para una versión de producción se debe guardar el estado y los comandos en Firestore o una base de datos. El panel queda público con `--allow-unauthenticated`; cualquier persona con la URL puede cambiar el modo o mover la barrera. El token solo se configura en la Orange Pi y protege sus llamadas de dispositivo.
