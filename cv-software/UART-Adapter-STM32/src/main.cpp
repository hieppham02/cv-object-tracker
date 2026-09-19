#include <Arduino.h>
#include <WebServer.h>
#include <WiFi.h>

HardwareSerial STM32Serial(1);

#define STM32_RX 16
#define STM32_TX 17

WebServer webServer(80);
int currentAngle = 90;

const char *AP_NAME = "ESP32-Servo";
const char *AP_PASSWORD = "12345678";

void sendAngleToSTM32(int angle)
{
  currentAngle = constrain(angle, 0, 180);
  STM32Serial.println(currentAngle);
  Serial.print("WiFi/PC -> STM32 angle: ");
  Serial.println(currentAngle);
}

void handleRoot()
{
  const String page = R"HTML(
    <!doctype html>
    <html lang="en">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ESP32 Servo</title>
    <style>
      body { font-family: Arial, sans-serif; max-width: 480px; margin: 40px auto; padding: 0 20px; }
      input { width: 100%; }
      output { display: block; text-align: center; font-size: 2rem; margin: 20px; }
      button { display: block; margin: 0 auto; padding: 10px 24px; font-size: 1rem; }
    </style>
    <h2>Servo angle</h2>
    <input id="angle" type="range" min="0" max="180" value=")HTML" + String(currentAngle) + R"HTML(" oninput="update(this.value)">
    <output id="value">)HTML" + String(currentAngle) + R"HTML(°</output>
    <button onclick="goHome()">Home (90°)</button>
    <script>
    function update(value) {
      document.getElementById('value').textContent = value + '°';
      fetch('/set?angle=' + value);
    }
    function goHome() {
      document.getElementById('angle').value = 90;
      update(90);
    }
    </script>
    </html>
    )HTML";
  webServer.send(200, "text/html", page);
}

void handleSetAngle()
{
  if (!webServer.hasArg("angle"))
  {
    webServer.send(400, "text/plain", "Missing angle");
    return;
  }

  sendAngleToSTM32(webServer.arg("angle").toInt());
  webServer.send(200, "text/plain", String(currentAngle));
}

void handleHome()
{
  sendAngleToSTM32(90);
  webServer.send(200, "text/plain", "90");
}

void setupWebServer()
{
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_NAME, AP_PASSWORD);
  Serial.print("WiFi AP: ");
  Serial.println(AP_NAME);
  Serial.print("Open http://");
  Serial.print(WiFi.softAPIP());
  Serial.println(" in a browser");

  webServer.on("/", HTTP_GET, handleRoot);
  webServer.on("/set", HTTP_GET, handleSetAngle);
  webServer.on("/home", HTTP_GET, handleHome);
  webServer.begin();
}

void setup()
{
  Serial.begin(115200);

  unsigned long start = millis();
  while (!Serial && millis() - start < 5000)
  {
    delay(10);
  }

  Serial.println("USB CDC OK");

  STM32Serial.begin(115200, SERIAL_8N1, STM32_RX, STM32_TX);
  setupWebServer();
}

void loop()
{
  webServer.handleClient();

  // static unsigned long lastPrint = 0;

  // if (millis() - lastPrint >= 1000)
  // {
  //   lastPrint = millis();
  //   Serial.println("ESP32 alive");
  // }

  if (Serial.available())
  {
    String data = Serial.readStringUntil('\n');
    data.trim();

    if (data.length() > 0)
    {
      Serial.print("PC -> ESP32: ");
      Serial.println(data);

      sendAngleToSTM32(data.toInt());
    }
  }
  if (STM32Serial.available())
  {
    uint8_t angle = STM32Serial.read();
    
    Serial.print("Angle = ");
    Serial.println(angle);
  }
}
