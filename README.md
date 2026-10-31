# Control IoT con Arduino, Orange Pi y Cloud Run

El HC-SR04 mide la distancia a un objeto. En modo automático, el Arduino convierte la distancia en un ángulo proporcional: a 5 cm o menos apunta a 180°, y al llegar al umbral configurado (15 cm inicialmente) apunta a 0°. Entre esos puntos el ángulo cambia gradualmente, y el servo recorre la posición a pasos cortos. La Orange Pi Zero 2W retransmite el estado a Cloud Run por WebSocket seguro (`wss://`), y el navegador recibe actualizaciones y envía controles por ese mismo protocolo. En la web se puede cambiar el umbral y escoger manualmente cualquier ángulo de 0° a 180°; el override permanece activo hasta volver al automático.

## Archivos

- `arduino/hc_sr04_barrier/hc_sr04_barrier.ino`: firmware para Arduino Uno R3.
- `orangepi/bridge.py`: puente USB serial y cliente WebSocket del servicio.
- `server.js`: servicio HTTP y WebSocket para Cloud Run con Node.js.
- `index.html`: panel web minimalista y adaptable a móvil.
- `package.json` y `Dockerfile`: configuración del proyecto y su imagen para Cloud Run.

El servicio de Cloud Run usa Node.js 24 LTS y el paquete `ws` para WebSocket. La Orange Pi usa `websocket-client` y reconecta si se corta la red o Cloud Run cierra una sesión.

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

Activa Cloud Run y Artifact Registry en el proyecto de Google Cloud y configura `gcloud` con ese proyecto. Desde la raíz del repositorio, despliega:

```sh
gcloud run deploy proyecto3arquitectura --source . --region northamerica-south1 \
  --allow-unauthenticated --max 1 --min 0 --timeout=3600
```

Anota la URL HTTPS que muestra `gcloud run deploy`; el puente la convierte a `wss://` automáticamente.

Cloud Run limita cada conexión WebSocket al timeout configurado (máximo 60 minutos), así que ambos clientes se reconectan automáticamente. La instancia permanece activa mientras haya conexiones WebSocket abiertas y puede generar cargos durante ese tiempo. `--max 1` mantiene en una instancia el estado de demostración, que se guarda en memoria. Consulta [la documentación de Cloud Run para WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets) antes de cambiar esta configuración.

## Iniciar el puente automáticamente al arrancar

Clona o actualiza este repositorio en la Orange Pi. Desde una terminal de `diego`, ejecuta:

```sh
cd ~/Proyecto3Arquitectura
git pull origin main
sudo bash orangepi/install-service.sh
```

El instalador crea el entorno Python, instala las dependencias, agrega `diego` al grupo serial `dialout` y habilita el servicio al inicio del sistema. La URL de Cloud Run ya viene configurada en `/etc/default/proyecto3-iot-bridge`; si cambia, edita ese archivo y reinicia el servicio.

Para comprobarlo o ver los mensajes:

```sh
sudo systemctl status proyecto3-iot-bridge
sudo journalctl -u proyecto3-iot-bridge -f
```

Si `systemctl` indica `masked`, vuelve a ejecutar `sudo bash orangepi/install-service.sh`; el instalador deshace la máscara y vuelve a habilitar el servicio. Un reinicio normal no lo vuelve a enmascarar.

El servicio busca el Arduino automáticamente por USB y vuelve a intentar si se desconecta. Para iniciarlo manualmente sin `systemd`, ejecuta `cd orangepi`, `. .venv/bin/activate` y `python bridge.py`.

Por defecto el script busca puertos USB tipo `/dev/ttyACM*` y `/dev/ttyUSB*`, prueba cada candidato y acepta el que transmite mensajes `STATE` del firmware. Así reconoce el Arduino aunque Linux le asigne otro nombre después de reiniciar. Si hay varios dispositivos seriales o quieres fijar uno, establece `SERIAL_PORT`, por ejemplo `export SERIAL_PORT='/dev/ttyACM0'`. El usuario que ejecuta Python debe tener permiso de acceso al puerto serial (normalmente pertenecer al grupo `dialout`; el nombre del grupo puede variar).

## Flujo de WebSocket

- Navegador ↔ `wss://.../ws`: recibe eventos `state` y envía `command` para cambiar entre automático y manual.
- Orange Pi ↔ `wss://.../ws?device=1`: recibe los comandos y envía telemetría periódica sin token.
- Orange Pi ↔ Arduino: USB serial a 115200 baudios; el puente manda órdenes `A,umbral` o `M,ángulo,umbral`.
- `GET /`: panel web; `GET /health`: verificación básica. También quedan disponibles las rutas REST `/api/state`, `/api/command` y `/api/telemetry` para diagnóstico.

## Alcance de la demo

El servicio guarda el estado en memoria y se configura para una instancia. Esto simplifica la práctica, pero se pierde el estado cuando Cloud Run reinicia o elimina la instancia. Para una versión de producción se debe guardar el estado y los comandos en Firestore o una base de datos. El panel y la conexión WebSocket del dispositivo son públicos: cualquiera con la URL puede cambiar el modo, mover el servo o enviar telemetría falsa.
