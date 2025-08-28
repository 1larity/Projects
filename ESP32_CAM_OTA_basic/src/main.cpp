#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoOTA.h>
#include "esp_camera.h"
#include "esp_timer.h"
#include "img_converters.h"
#include "esp_http_server.h"
#include <driver/rtc_io.h>  // Example header for ESP32 (adjust based on your framework)

// ====== WiFi config ======
#define WIFI_SSID     "rubidium2g_RPT"
#define WIFI_PASSWORD "Echomonkeyfuture859"

// ====== AI Thinker pin map ======
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ====== HTTP stream constants ======
static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=frame";
static const char* STREAM_BOUNDARY     = "\r\n--frame\r\n";
static const char* STREAM_PART         = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

// ====== Globals ======
httpd_handle_t stream_httpd = nullptr;
httpd_handle_t ctrl_httpd   = nullptr;
volatile bool ota_in_progress = false;

static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t *fb = nullptr;
  esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      // Try once more
      fb = esp_camera_fb_get();
      if (!fb) return ESP_FAIL;
    }

    if (fb->format != PIXFORMAT_JPEG) {
      uint8_t *jpg_buf = nullptr;
      size_t jpg_len = 0;
      bool ok = frame2jpg(fb, 80, &jpg_buf, &jpg_len);
      esp_camera_fb_return(fb);
      if (!ok) return ESP_FAIL;

      char part[64];
      size_t hlen = snprintf(part, sizeof(part), STREAM_PART, jpg_len);
      if (httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY)) != ESP_OK ||
          httpd_resp_send_chunk(req, part, hlen) != ESP_OK ||
          httpd_resp_send_chunk(req, (const char*)jpg_buf, jpg_len) != ESP_OK) {
        free(jpg_buf);
        return ESP_FAIL;
      }
      free(jpg_buf);
    } else {
      char part[64];
      size_t hlen = snprintf(part, sizeof(part), STREAM_PART, fb->len);
      if (httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY)) != ESP_OK ||
          httpd_resp_send_chunk(req, part, hlen) != ESP_OK ||
          httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len) != ESP_OK) {
        esp_camera_fb_return(fb);
        return ESP_FAIL;
      }
      esp_camera_fb_return(fb);
    }
    // Yield to WiFi/OTA
    delay(1);
  }
  return ESP_OK;
}

static esp_err_t index_handler(httpd_req_t *req) {
  static const char PROGMEM html[] =
    "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>ESP32-CAM</title><style>body{margin:0;background:#111;color:#eee;font-family:sans-serif}"
    "#c{display:block;margin:0 auto;max-width:100vw;height:auto}</style></head>"
    "<body><img id='c' src='/stream'></body></html>";
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, html, strlen(html));
}

void start_webserver() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;
  config.max_uri_handlers = 8;

  if (httpd_start(&ctrl_httpd, &config) == ESP_OK) {
    httpd_uri_t index_uri = { .uri="/", .method=HTTP_GET, .handler=index_handler, .user_ctx=nullptr };
    httpd_register_uri_handler(ctrl_httpd, &index_uri);

    // **Register the stream handler on the same server**
    httpd_uri_t stream_uri = { .uri="/stream", .method=HTTP_GET, .handler=stream_handler, .user_ctx=nullptr };
    httpd_register_uri_handler(ctrl_httpd, &stream_uri);
  }

}

bool init_camera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;


  if (psramFound()) {
    config.frame_size = FRAMESIZE_SVGA; // good default
    config.jpeg_quality = 12;
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    return false;
  }

  sensor_t * s = esp_camera_sensor_get();
  if (s) {
    s->set_framesize(s, config.frame_size);
    s->set_brightness(s, 0);
    s->set_contrast(s, 0);
    s->set_saturation(s, 0);
    s->set_vflip(s, 0);
    s->set_hmirror(s, 0);
    s->set_special_effect(s, 0);
  }
  return true;
}

void deinit_camera() {    // safe deinit helper
  esp_camera_deinit();
}

void setup_wifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("WiFi: connecting to %s\n", WIFI_SSID);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi: connected. IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("WiFi: failed to connect");
  }
}

void setup_ota() {
  ArduinoOTA.setHostname("esp32cam");
  // Optional password:
  // ArduinoOTA.setPassword("set_a_password");

  ArduinoOTA.onStart([]() {
    ota_in_progress = true;
    Serial.println("OTA start: stopping servers and camera");
    // **Stop HTTP servers to free sockets and RAM**
    if (ctrl_httpd)  { httpd_stop(ctrl_httpd);  ctrl_httpd  = nullptr; }
    if (stream_httpd){ httpd_stop(stream_httpd);stream_httpd= nullptr; }

    // **Turn off camera to release PSRAM/heap**
    deinit_camera();
  });
  ArduinoOTA.onEnd([]() {
    Serial.println("OTA end");
  });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    static uint8_t last = 0;
    uint8_t pct = (progress * 100) / total;
    if (pct != last) {
      last = pct;
      Serial.printf("OTA %u%%\n", pct);
      
    }
  });
  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA error [%u]\n", error);
    ota_in_progress = false;
  });
  ArduinoOTA.begin();
}

void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // prevent brownout reset on weak USB
  Serial.begin(115200);
  Serial.setDebugOutput(false);
  delay(200);

  if (!init_camera()) {
    // Block if camera failed. Still keep OTA alive if WiFi connects.
    Serial.println("Camera init failed. Continuing for OTA.");
  }

  setup_wifi();
  setup_ota();

  if (WiFi.status() == WL_CONNECTED) {
    start_webserver();
    Serial.println("HTTP: index http://<ip>/  stream http://<ip>:81/stream");
  } else {
    Serial.println("No web server without WiFi.");
  }
}

void loop() {
  ArduinoOTA.handle(); // keep OTA responsive
   if (!ota_in_progress) {
  delay(2);
   }
}
