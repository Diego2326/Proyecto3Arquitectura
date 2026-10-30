#include <Servo.h>
#include <stdlib.h>
#include <string.h>

// Servo proporcional controlado por HC-SR04, con override manual por serial.
// HC-SR04: VCC -> 5V, GND -> GND, TRIG -> D2, ECHO -> D3.
// SG90: señal -> D9. Usa una fuente externa de 5 V para el servo y conecta
// el GND de esa fuente con el GND del Arduino. No alimentes el SG90 desde el Uno.

const byte TRIG_PIN = 2;
const byte ECHO_PIN = 3;
const byte SERVO_PIN = 9;

const int DEFAULT_THRESHOLD_CM = 15;
const int MIN_THRESHOLD_CM = 5;
const int MAX_THRESHOLD_CM = 100;
const int CLOSEST_DISTANCE_CM = 5;
const unsigned long MEASURE_INTERVAL_MS = 100;
const unsigned long TELEMETRY_INTERVAL_MS = 500;
const unsigned long SERVO_STEP_INTERVAL_MS = 20;
const int SERVO_STEP_DEGREES = 2;
const byte MISSED_ECHOES_TO_CLOSE = 3;

Servo barrierServo;
int thresholdCm = DEFAULT_THRESHOLD_CM;
int distanceCm = -1;  // -1 significa que el sensor no recibió eco.
int servoAngle = 0;
int targetAngle = 0;
float filteredDistanceCm = -1.0f;
byte missedEchoCount = 0;
bool manualMode = false;

char serialLine[32];
byte serialIndex = 0;
unsigned long lastMeasureMs = 0;
unsigned long lastTelemetryMs = 0;
unsigned long lastServoStepMs = 0;

void setTargetAngle(int angle) {
  targetAngle = constrain(angle, 0, 180);
}

void updateAutomaticTarget() {
  if (manualMode) return;

  if (filteredDistanceCm < 0) {
    setTargetAngle(0);
    return;
  }

  if (filteredDistanceCm <= CLOSEST_DISTANCE_CM) {
    setTargetAngle(180);
  } else if (filteredDistanceCm >= thresholdCm || thresholdCm <= CLOSEST_DISTANCE_CM) {
    setTargetAngle(0);
  } else {
    float fraction = (thresholdCm - filteredDistanceCm) /
                     (float)(thresholdCm - CLOSEST_DISTANCE_CM);
    setTargetAngle((int)(fraction * 180.0f + 0.5f));
  }
}

void moveServoGradually(unsigned long now) {
  if (now - lastServoStepMs < SERVO_STEP_INTERVAL_MS) return;
  lastServoStepMs = now;

  if (servoAngle < targetAngle) {
    int difference = targetAngle - servoAngle;
    servoAngle += difference < SERVO_STEP_DEGREES ? difference : SERVO_STEP_DEGREES;
    barrierServo.write(servoAngle);
  } else if (servoAngle > targetAngle) {
    int difference = servoAngle - targetAngle;
    servoAngle -= difference < SERVO_STEP_DEGREES ? difference : SERVO_STEP_DEGREES;
    barrierServo.write(servoAngle);
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
    if (missedEchoCount < 255) missedEchoCount++;
    // Ignora ecos perdidos aislados para evitar que el servo tiemble o se cierre.
    if (missedEchoCount >= MISSED_ECHOES_TO_CLOSE) {
      filteredDistanceCm = -1.0f;
      updateAutomaticTarget();
    }
    return;
  }

  distanceCm = (int)((echoDuration + 29UL) / 58UL);
  missedEchoCount = 0;
  if (filteredDistanceCm < 0) {
    filteredDistanceCm = distanceCm;
  } else {
    filteredDistanceCm = (filteredDistanceCm * 0.65f) + (distanceCm * 0.35f);
  }
  updateAutomaticTarget();
}

void handleSerialLine(char *line) {
  // Orange Pi -> Arduino: A,<umbral_cm> o M,<ángulo_0_a_180>,<umbral_cm>\n
  if (line[0] == 'A' && line[1] == ',') {
    int requestedThreshold = atoi(line + 2);
    if (requestedThreshold >= MIN_THRESHOLD_CM && requestedThreshold <= MAX_THRESHOLD_CM) {
      thresholdCm = requestedThreshold;
      manualMode = false;
      updateAutomaticTarget();
    }
    return;
  }

  if (line[0] == 'M' && line[1] == ',') {
    char *separator = strchr(line + 2, ',');
    if (separator == NULL) return;
    *separator = '\0';
    int requestedAngle = atoi(line + 2);
    int requestedThreshold = atoi(separator + 1);
    if (requestedAngle >= 0 && requestedAngle <= 180 &&
        requestedThreshold >= MIN_THRESHOLD_CM && requestedThreshold <= MAX_THRESHOLD_CM) {
      thresholdCm = requestedThreshold;
      manualMode = true;
      setTargetAngle(requestedAngle);
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
  barrierServo.write(servoAngle);
  Serial.begin(115200);
}

void loop() {
  readSerialCommands();

  unsigned long now = millis();
  if (now - lastMeasureMs >= MEASURE_INTERVAL_MS) {
    lastMeasureMs = now;
    measureDistance();
  }
  moveServoGradually(now);
  if (now - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryMs = now;
    sendTelemetry();
  }
}
