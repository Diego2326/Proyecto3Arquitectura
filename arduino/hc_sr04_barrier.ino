#include <Servo.h>
#include <stdlib.h>
#include <string.h>

// Barrera de proximidad con HC-SR04 y servo SG90.
// HC-SR04: VCC -> 5V, GND -> GND, TRIG -> D2, ECHO -> D3.
// SG90: señal -> D9. Usa una fuente externa de 5 V para el servo y conecta
// el GND de esa fuente con el GND del Arduino. No alimentes el SG90 desde el Uno.

const byte TRIG_PIN = 2;
const byte ECHO_PIN = 3;
const byte SERVO_PIN = 9;

const int CLOSED_ANGLE = 0;
const int OPEN_ANGLE = 90;
const int DEFAULT_THRESHOLD_CM = 15;
const int MIN_THRESHOLD_CM = 5;
const int MAX_THRESHOLD_CM = 100;
const int HYSTERESIS_CM = 2;
const unsigned long MEASURE_INTERVAL_MS = 100;
const unsigned long TELEMETRY_INTERVAL_MS = 500;

Servo barrierServo;
int thresholdCm = DEFAULT_THRESHOLD_CM;
int distanceCm = -1;  // -1 significa que el sensor no recibió eco.
int servoAngle = CLOSED_ANGLE;
bool barrierOpen = false;
bool manualMode = false;

char serialLine[32];
byte serialIndex = 0;
unsigned long lastMeasureMs = 0;
unsigned long lastTelemetryMs = 0;

void setBarrier(bool open) {
  barrierOpen = open;
  servoAngle = open ? OPEN_ANGLE : CLOSED_ANGLE;
  barrierServo.write(servoAngle);
}

void updateBarrier() {
  if (manualMode) return;

  // Si no hay eco, la barrera queda cerrada por seguridad.
  if (distanceCm < 0) {
    if (barrierOpen) setBarrier(false);
    return;
  }

  if (!barrierOpen && distanceCm <= thresholdCm) {
    setBarrier(true);
  } else if (barrierOpen && distanceCm > thresholdCm + HYSTERESIS_CM) {
    setBarrier(false);
  }
}

void measureDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long echoDuration = pulseIn(ECHO_PIN, HIGH, 30000UL);
  if (echoDuration == 0) {
    distanceCm = -1;
  } else {
    distanceCm = (int)((echoDuration + 29UL) / 58UL);
  }
  updateBarrier();
}

void handleSerialLine(char *line) {
  // Orange Pi -> Arduino: A,<umbral_cm> o M,<ángulo>,<umbral_cm>\n
  if (line[0] == 'A' && line[1] == ',') {
    int requestedThreshold = atoi(line + 2);
    if (requestedThreshold >= MIN_THRESHOLD_CM && requestedThreshold <= MAX_THRESHOLD_CM) {
      thresholdCm = requestedThreshold;
      manualMode = false;
      updateBarrier();
    }
    return;
  }

  if (line[0] == 'M' && line[1] == ',') {
    char *separator = strchr(line + 2, ',');
    if (separator == NULL) return;
    *separator = '\0';
    int requestedAngle = atoi(line + 2);
    int requestedThreshold = atoi(separator + 1);
    if ((requestedAngle == CLOSED_ANGLE || requestedAngle == OPEN_ANGLE) &&
        requestedThreshold >= MIN_THRESHOLD_CM && requestedThreshold <= MAX_THRESHOLD_CM) {
      thresholdCm = requestedThreshold;
      manualMode = true;
      setBarrier(requestedAngle == OPEN_ANGLE);
    }
  }
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char incoming = (char)Serial.read();
    if (incoming == '\n') {
      serialLine[serialIndex] = '\0';
      handleSerialLine(serialLine);
      serialIndex = 0;
    } else if (incoming != '\r') {
      if (serialIndex < sizeof(serialLine) - 1) {
        serialLine[serialIndex++] = incoming;
      } else {
        serialIndex = 0;
      }
    }
  }
}

void sendTelemetry() {
  // Arduino -> Orange Pi: STATE,<ángulo>,<distancia_cm>,<umbral_cm>,<modo>\n
  Serial.print(F("STATE,"));
  Serial.print(servoAngle);
  Serial.print(',');
  Serial.print(distanceCm);
  Serial.print(',');
  Serial.print(thresholdCm);
  Serial.print(',');
  Serial.println(manualMode ? F("MANUAL") : F("AUTO"));
}

void setup() {
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);

  barrierServo.attach(SERVO_PIN);
  setBarrier(false);
  Serial.begin(115200);
}

void loop() {
  readSerialCommands();

  unsigned long now = millis();
  if (now - lastMeasureMs >= MEASURE_INTERVAL_MS) {
    lastMeasureMs = now;
    measureDistance();
  }
  if (now - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryMs = now;
    sendTelemetry();
  }
}
