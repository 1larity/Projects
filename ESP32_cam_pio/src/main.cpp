/*********
  ESP32-CAM with OTA and Wifi-repeater functions
  Richard Beech based on the work of  Rui Santos
  Original project details at https://RandomNerdTutorials.com/esp32-cam-video-streaming-web-server-camera-home-assistant/

  IMPORTANT!!!
   - Select Board "AI Thinker ESP32-CAM"
   - GPIO 0 must be connected to GND to upload a sketch
   - After connecting GPIO 0 to GND, press the ESP32-CAM on-board RESET button to put your board in flashing mode

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files.

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
*********/

#include "esp_camera.h"
#include <WiFi.h>
#include "esp_timer.h"
#include "img_converters.h"
#include "Arduino.h"
#include "fb_gfx.h"
#include "soc/soc.h"          //disable brownout problems
#include "soc/rtc_cntl_reg.h" //disable brownout problems
#include "esp_http_server.h"
#include <ArduinoOTA.h> // **Add for OTA**

// Replace with your network credentials

#define STA_SSID "rubidium2g_RPT"
#define STA_PASS "Echomonkeyfuture859"

#define AP_SSID "rubidium2g_RPT"
#define AP_PASS "Echomonkeyfuture859"

// IPAddress local_IP(192, 168, 1, 252);
IPAddress gateway(192, 168, 1, 1);
IPAddress subnet(255, 255, 0, 0);

IPAddress ap_ip(192, 168, 4, 1);
IPAddress ap_mask(255, 255, 255, 0);
IPAddress ap_leaseStart(192, 168, 4, 2);
IPAddress ap_dns(8, 8, 4, 4);
#define PART_BOUNDARY "123456789000000000000987654321"

// Camera model definition - Tested with AI Thinker Model

#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

static const char *_STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *_STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char *_STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";
// Handle for the HTTP stream server
httpd_handle_t stream_httpd = NULL;
// Function to handle HTTP requests for the video stream
camera_config_t config;
static esp_err_t stream_handler(httpd_req_t *req)
{
  camera_fb_t *fb = NULL; // Frame buffer
  esp_err_t res = ESP_OK;
  size_t _jpg_buf_len = 0;  // JPEG buffer length
  uint8_t *_jpg_buf = NULL; // JPEG buffer pointer
  char *part_buf[64];       // Part buffer for HTTP response chunks

  res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
  if (res != ESP_OK)
  {
    return res;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s == NULL)
  {
    Serial.println("Failed to get sensor pointer");
  }

  while (true)
  {
    fb = esp_camera_fb_get(); // Get a frame from the camera
    if (!fb)
    {
  Serial.println("Camera capture failed");
  esp_camera_deinit();  // **Deinit before reinit**
  esp_err_t cam_err = esp_camera_init(&config);
  Serial.printf("Reinit camera: err=0x%x\n", cam_err);
  if (cam_err != ESP_OK) {
    Serial.println("Camera reinit failed");
  }
  res = ESP_FAIL;
    }
    else
    {
      if (fb->width > 400)
      { // If width greater than 400 pixels
        if (fb->format != PIXFORMAT_JPEG)
        {                                                                    // If not already JPEG format
          bool jpeg_converted = frame2jpg(fb, 80, &_jpg_buf, &_jpg_buf_len); // Convert frame to JPEG
          esp_camera_fb_return(fb);                                          // Return frame buffer
          fb = NULL;
          if (!jpeg_converted)
          {
            Serial.println("JPEG compression failed");
            res = ESP_FAIL;
          }
        }
        else
        {
          _jpg_buf_len = fb->len; // Use the length of the current JPEG buffer
          _jpg_buf = fb->buf;     // Use the current JPEG buffer
        }
      }
    }
    if (res == ESP_OK)
    {
      size_t hlen = snprintf((char *)part_buf, 64, _STREAM_PART, _jpg_buf_len); // Prepare HTTP header
      res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);           // Send header chunk
    }
    if (res == ESP_OK)
    {
      res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len); // Send image chunk
    }
    if (res == ESP_OK)
    {
      res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY)); // Send boundary marker
    }
    if (fb)
    {
      esp_camera_fb_return(fb); // Return frame buffer to stack
      fb = NULL;
      _jpg_buf = NULL;
    }
    else if (_jpg_buf)
    {
      free(_jpg_buf); // Free JPEG buffer if allocated
      _jpg_buf = NULL;
    }
    if (res != ESP_OK)
    {
      break; // Exit loop if error occurred
    }
  }
  return res;
}

void onEvent(arduino_event_id_t event, arduino_event_info_t info)
{
  switch (event)
  {
  case ARDUINO_EVENT_WIFI_STA_START:
    Serial.println("STA Started");
    break;
  case ARDUINO_EVENT_WIFI_STA_CONNECTED:
    Serial.println("STA Connected");
    break;
  case ARDUINO_EVENT_WIFI_STA_GOT_IP:
    Serial.println("STA Got IP");
    Serial.println(WiFi.STA);
    WiFi.AP.enableNAPT(true); // Enable Network Address Port Translation
    break;
  case ARDUINO_EVENT_WIFI_STA_LOST_IP:
    Serial.println("STA Lost IP");
    WiFi.AP.enableNAPT(false); // Disable NAPT
    break;
  case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
    Serial.println("STA Disconnected");
    WiFi.AP.enableNAPT(false); // Disable NAPT
    break;
  case ARDUINO_EVENT_WIFI_STA_STOP:
    Serial.println("STA Stopped");
    break;

  case ARDUINO_EVENT_WIFI_AP_START:
    Serial.println("AP Started");
    Serial.println(WiFi.AP);
    break;
  case ARDUINO_EVENT_WIFI_AP_STACONNECTED:
    Serial.println("AP STA Connected");
    break;
  case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED:
    Serial.println("AP STA Disconnected");
    break;
  case ARDUINO_EVENT_WIFI_AP_STAIPASSIGNED:
    Serial.print("AP STA IP Assigned: ");
    Serial.println(IPAddress(info.wifi_ap_staipassigned.ip.addr));
    break;
  case ARDUINO_EVENT_WIFI_AP_PROBEREQRECVED:
    Serial.println("AP Probe Request Received");
    break;
  case ARDUINO_EVENT_WIFI_AP_STOP:
    Serial.println("AP Stopped");
    break;

  default:
    break;
  }
}

void startCameraServer()
{
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;

  httpd_uri_t index_uri = {
      .uri = "/",
      .method = HTTP_GET,
      .handler = stream_handler,
      .user_ctx = NULL};

  // Serial.printf("Starting web server on port: '%d'\n", config.server_port);
  if (httpd_start(&stream_httpd, &config) == ESP_OK)
  {
    httpd_register_uri_handler(stream_httpd, &index_uri);
  }
}

// OTA Setup function
void setupOTA()
{
  ArduinoOTA.onStart([]()
                     {
    String type;
    if (ArduinoOTA.getCommand() == U_FLASH) {
      type = "sketch";
    } else { // U_SPIFFS
      type = "filesystem";
    }
    Serial.println("Start updating " + type); });
  ArduinoOTA.onEnd([]()
                   { Serial.println("\nEnd"); });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total)
                        { Serial.printf("Progress: %u%%\r", (progress / (total / 100))); });
  ArduinoOTA.onError([](ota_error_t error)
                     {
    Serial.printf("Error[%u]: ", error);
    if (error == OTA_AUTH_ERROR) {
      Serial.println("Auth Failed");
    } else if (error == OTA_BEGIN_ERROR) {
      Serial.println("Begin Failed");
    } else if (error == OTA_CONNECT_ERROR) {
      Serial.println("Connect Failed");
    } else if (error == OTA_RECEIVE_ERROR) {
      Serial.println("Receive Failed");
    } else if (error == OTA_END_ERROR) {
      Serial.println("End Failed");
    } });
  ArduinoOTA.begin(); // **Start OTA**
}

// Function to restart the ESP32 to reset the camera
void restartCamera(void *arg)
{
  Serial.println("Restarting camera...");
  ESP.restart(); // Restart the ESP32
}

void setup()
{
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0); // disable brownout detector

  Serial.begin(115200);
  Serial.setDebugOutput(false);


  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
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

  if (psramFound())
  {
    config.frame_size = FRAMESIZE_QVGA; // FRAMESIZE_UXGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }
  else
  {
    config.frame_size = FRAMESIZE_QVGA; // FRAMESIZE_SVGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }

  // Camera init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK)
  {
    Serial.printf("Camera init failed with error 0x%x", err);
    while (true)
      delay(1000); // Halt
  }
  sensor_t *s = esp_camera_sensor_get(); // Get the camera sensor settings
  if (s != NULL)
  {
    Serial.printf("Sensor PID: 0x%02x\n", s->id.PID);
    s->set_vflip(s, 0); // Set vertical flip (1 for enabled, 0 for disabled)
  }

  // Wi-Fi connection
  Network.onEvent(onEvent);

  WiFi.AP.begin();
  WiFi.AP.config(ap_ip, ap_ip, ap_mask, ap_leaseStart, ap_dns);
  WiFi.AP.create(AP_SSID, AP_PASS);
  if (!WiFi.AP.waitStatusBits(ESP_NETIF_STARTED_BIT, 1000))
  {
    Serial.println("Failed to start AP!");
    return;
  }

  // WiFi.config(local_IP,gateway,subnet);
  WiFi.begin(STA_SSID, STA_PASS);
  while (WiFi.status() != WL_CONNECTED)
  {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.println("WiFi connected");

  Serial.print("Camera Stream Ready! Go to: http://");
  Serial.print(WiFi.localIP());

  // Start streaming web server
  startCameraServer();
  // Setup OTA updates
  setupOTA(); // **Setup OTA**
              // Timer for camera restart every hour (3600 seconds)

  esp_timer_create_args_t restart_timer_args = {
      .callback = &restartCamera,
      .name = "restart_timer"};
  esp_timer_handle_t restart_timer;
  esp_timer_create(&restart_timer_args, &restart_timer);
  esp_timer_start_periodic(restart_timer, 1800000000); // Restart every hour (3600 seconds)
}

void loop()
{
  ArduinoOTA.handle(); // **Handle OTA requests**
  // delay(1);
}
