/*
================================================================================
  GLOSA Arac Verici — ESP32 / ESP32-S / WROOM, ESP32-S3, ESP32-C3 (tek kod)
  Kart Arduino IDE'de secilince dogru pinler ve guc otomatik secilir.
  5 buton -> ESP-NOW beacon oturumu -> Panel kopru(ler)i (RSSI analizi)
================================================================================
  BUTONLAR (dahili pull-up; bosta HIGH, basinca GND'ye ceker = LOW):
                ESP32/ESP32-S/WROOM   ESP32-S3   ESP32-C3
    Ambulans         GPIO32            GPIO4      GPIO0
    Polis            GPIO33            GPIO5      GPIO1
    Askeri           GPIO25            GPIO6      GPIO2
    Burokrat         GPIO26            GPIO7      GPIO3
    Itfaiye          GPIO27            GPIO15     GPIO4
    ESP32: GPIO0-4 kullanilmaz (acilis pini / USB seri), GPIO34-39'da pull-up yok.
    S3:    GPIO0/3/45/46 acilis pini, 19/20 USB, 26-37 flash/PSRAM.
    C3:    GPIO2 acilis pini, acilista basili tutmayin.

  CALISMA:
    Butona basilinca bir OTURUM baslar: saniyede SEND_HZ (4) broadcast beacon
    gonderilir. Kopruden onay gelince gonderim hemen durur; gelmezse
    SEND_TIME_MS (varsayilan 2 sn) sonunda durur.
    Kopru her beacon'in RSSI'ini aninda esikle karsilastirir.
    Baska butona basilirsa yeni tip ile yeni oturum baslar.
    (Gercek aracta tek buton olacak; simdilik test icin 5 buton.)

  ONAY (alicidan ESP-NOW ile geri):
    Alici, beacon'i gonderen MAC'e unicast 'A' paketi yollar:
      kol numarasi + durum. Verici bunu alinca LED ACK_LED_MS (2 sn) yanar, sonra soner.

  KANAL: Tum panel kopruleri ve arac verici ayni kanalda olmali (AP_CHANNEL = 1).
  LED: oturum boyunca blink, onay gelince ACK_LED_MS (2 sn) yanar.
    ESP32: GPIO2   S3: kart LED'i (LED_BUILTIN)   C3 SuperMini: GPIO8 (ters)

  ARDUINO IDE: ESP32 / ESP32-S -> "ESP32 Dev Module" (ya da "NodeMCU-32S")
               ESP32-S3 -> "ESP32S3 Dev Module"   ESP32-C3 -> "ESP32C3 Dev Module"
================================================================================
*/

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_arduino_version.h>

// ---- Ayarlar ----
#define ARAC_ID        1        // Bu aracin kimligi (>= 1)
#define ESPNOW_CHANNEL 1        // Kopru AP kanali ile ayni
#define SEND_TIME_MS   2000UL   // Onay gelmezse en fazla gonderim suresi (ms)
#define SEND_HZ        4        // Saniyedeki beacon sayisi
#define BEACON_MS      (1000UL / SEND_HZ)   // Beacon araligi (ms)
#define DEBOUNCE_MS    30
#define ACK_LED_MS     2000

// ---- Arac tipleri (panel TX1_VEHICLE_xxx ile ayni) ----
#define TIP_AMBULANS   0x01
#define TIP_POLIS      0x02
#define TIP_ASKERI     0x03
#define TIP_BUROKRAT   0x04
#define TIP_ITFAIYE    0x05

// ---- LED ----
#if defined(CONFIG_IDF_TARGET_ESP32C3)
  #define LED_PIN      8
  #define LED_ON()     digitalWrite(LED_PIN, LOW)     // C3 SuperMini: ters mantik
  #define LED_OFF()    digitalWrite(LED_PIN, HIGH)
#elif defined(CONFIG_IDF_TARGET_ESP32S3) && defined(LED_BUILTIN)
  #define LED_PIN      LED_BUILTIN
  #define LED_ON()     digitalWrite(LED_PIN, HIGH)
  #define LED_OFF()    digitalWrite(LED_PIN, LOW)
#else
  #define LED_PIN      2
  #define LED_ON()     digitalWrite(LED_PIN, HIGH)
  #define LED_OFF()    digitalWrite(LED_PIN, LOW)
#endif

// ---- Paketler (kopru ile ayni yapi) ----
#define PKT_VER        2

typedef struct __attribute__((packed)) {   // arac -> kopru, broadcast
  char     magic[4];   // "GLSA"
  uint8_t  ver;        // PKT_VER
  uint8_t  cmd;        // 'B' = beacon (oncelikli arac oturumu)
  uint8_t  tip;        // arac tipi
  uint8_t  idH;        // arac ID yuksek
  uint8_t  idL;        // arac ID dusuk
  uint8_t  session;    // oturum no (her basista +1)
  uint16_t count;      // oturum icindeki beacon sirasi
  int8_t   txPower;    // gonderim gucu (dBm), RSSI yorumu icin
  uint8_t  cs;         // onceki baytlarin toplami
} BeaconPkt;

typedef struct __attribute__((packed)) {   // kopru -> arac, unicast
  char     magic[4];   // "GLSA"
  uint8_t  ver;        // PKT_VER
  uint8_t  cmd;        // 'A' = onay
  uint8_t  idH;
  uint8_t  idL;
  uint8_t  session;    // onaylanan oturum
  uint8_t  kol;        // istegin islendigi kol
  uint8_t  status;     // 1 = alindi, 2 = vericiye iletildi (panel onayi)
  uint8_t  cs;
} AckPkt;

static const uint8_t BCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
#if defined(CONFIG_IDF_TARGET_ESP32C3)
  // C3 SuperMini: yuksek gucte anten/regulator sorunu, 8.5 dBm en saglam
  #define TX_POWER_LEVEL WIFI_POWER_8_5dBm
  #define TX_POWER_DBM   8
#else
  #define TX_POWER_LEVEL WIFI_POWER_19_5dBm
  #define TX_POWER_DBM   19      // beacon icinde bildirilen guc (dBm)
#endif

// Buton pinleri (tip sirasiyla: Ambulans, Polis, Askeri, Burokrat, Itfaiye)
#if defined(CONFIG_IDF_TARGET_ESP32C3)
  #define PIN_AMB 0
  #define PIN_POL 1
  #define PIN_ASK 2
  #define PIN_BUR 3
  #define PIN_ITF 4
#elif defined(CONFIG_IDF_TARGET_ESP32S3)
  #define PIN_AMB 4
  #define PIN_POL 5
  #define PIN_ASK 6
  #define PIN_BUR 7
  #define PIN_ITF 15
#else                                         // ESP32 / ESP32-S / WROOM
  #define PIN_AMB 32
  #define PIN_POL 33
  #define PIN_ASK 25
  #define PIN_BUR 26
  #define PIN_ITF 27
#endif

// ---- Buton tablosu ----
typedef struct {
  uint8_t     pin;
  uint8_t     tip;
  const char* ad;
  bool        stable;
  bool        lastRead;
  uint32_t    changedAt;
} Buton;

Buton butonlar[] = {
  {PIN_AMB, TIP_AMBULANS, "Ambulans", true, true, 0},
  {PIN_POL, TIP_POLIS,    "Polis",    true, true, 0},
  {PIN_ASK, TIP_ASKERI,   "Askeri",   true, true, 0},
  {PIN_BUR, TIP_BUROKRAT, "Burokrat", true, true, 0},
  {PIN_ITF, TIP_ITFAIYE,  "Itfaiye",  true, true, 0},
};
const int BUTON_SAYISI = sizeof(butonlar) / sizeof(butonlar[0]);

// ---- Oturum durumu ----
bool     sessionActive = false;
uint8_t  sessionTip    = 0;
uint8_t  sessionNo     = 0;
uint16_t beaconCount   = 0;
uint32_t sessionStart  = 0;
uint32_t lastBeacon    = 0;

// ---- Onay (callback WiFi gorevinde: sadece kopyala) ----
volatile bool ackPending = false;
AckPkt        ackPkt;
portMUX_TYPE  ackMux = portMUX_INITIALIZER_UNLOCKED;
uint32_t      ackLedUntil = 0;

uint8_t checksum(const uint8_t* p, int n) {
  uint8_t s = 0;
  for (int i = 0; i < n; i++) s += p[i];
  return s;
}

void onNowData(const uint8_t* data, int len) {
  if (len != sizeof(AckPkt)) return;
  portENTER_CRITICAL(&ackMux);
  if (!ackPending) { memcpy(&ackPkt, data, sizeof(AckPkt)); ackPending = true; }
  portEXIT_CRITICAL(&ackMux);
}

void startSession(uint8_t tip, const char* ad) {
  sessionActive = true;
  sessionTip    = tip;
  sessionNo++;
  beaconCount   = 0;
  sessionStart  = millis();
  lastBeacon    = 0;
  Serial.printf("[OTURUM] %s  tip=%u  id=%u  oturum=%u\n", ad, tip, ARAC_ID, sessionNo);
}

void sendBeacon() {
  BeaconPkt pkt;
  memcpy(pkt.magic, "GLSA", 4);
  pkt.ver     = PKT_VER;
  pkt.cmd     = 'B';
  pkt.tip     = sessionTip;
  pkt.idH     = (uint8_t)(ARAC_ID >> 8);
  pkt.idL     = (uint8_t)(ARAC_ID & 0xFF);
  pkt.session = sessionNo;
  pkt.count   = beaconCount++;
  pkt.txPower = TX_POWER_DBM;
  pkt.cs      = checksum((uint8_t*)&pkt, sizeof(pkt) - 1);
  esp_now_send(BCAST, (uint8_t*)&pkt, sizeof(pkt));
}

void handleAck() {
  AckPkt a;
  portENTER_CRITICAL(&ackMux);
  memcpy(&a, &ackPkt, sizeof(a)); ackPending = false;
  portEXIT_CRITICAL(&ackMux);

  if (memcmp(a.magic, "GLSA", 4) != 0 || a.ver != PKT_VER || a.cmd != 'A') return;
  if (checksum((uint8_t*)&a, sizeof(a) - 1) != a.cs) return;
  if (((a.idH << 8) | a.idL) != ARAC_ID || a.session != sessionNo) return;

  ackLedUntil = millis() + ACK_LED_MS;
  if (sessionActive) {                          // onay geldi: gonderimi kes
    sessionActive = false;
    Serial.printf("[OTURUM] onay geldi, %u beacon sonra durdu\n", beaconCount);
  }
  Serial.printf("[ONAY] oturum=%u  kol=%u  durum=%u\n", a.session, a.kol, a.status);
}

void setup() {
  pinMode(LED_PIN, OUTPUT); LED_OFF();
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[ESP32] GLOSA Arac Verici basliyor...");

  for (int i = 0; i < BUTON_SAYISI; i++)
    pinMode(butonlar[i].pin, INPUT_PULLUP);   // bosta HIGH, basinca LOW

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);

  WiFi.setTxPower(TX_POWER_LEVEL);              // ESP-NOW init oncesi

  if (esp_now_init() != ESP_OK) {
    Serial.println("[ESPNOW] init hatasi, yeniden baslatiliyor");
    delay(1000);
    ESP.restart();
  }

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_now_register_recv_cb([](const esp_now_recv_info_t* info, const uint8_t* d, int l) { onNowData(d, l); });
#else
  esp_now_register_recv_cb([](const uint8_t* mac, const uint8_t* d, int l) { onNowData(d, l); });
#endif

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BCAST, 6);
  peer.channel = ESPNOW_CHANNEL;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

  Serial.printf("[ESPNOW] Hazir. Kanal=%d  MAC=%s  ID=%u\n",
                ESPNOW_CHANNEL, WiFi.macAddress().c_str(), ARAC_ID);
}

void loop() {
  uint32_t now = millis();

  // Butonlar
  for (int i = 0; i < BUTON_SAYISI; i++) {
    Buton& b = butonlar[i];
    bool r = digitalRead(b.pin);
    if (r != b.lastRead) { b.lastRead = r; b.changedAt = now; }
    if ((now - b.changedAt) >= DEBOUNCE_MS && r != b.stable) {
      b.stable = r;
      if (r == LOW) startSession(b.tip, b.ad);   // HIGH -> LOW: basildi
    }
  }

  // Beacon
  if (sessionActive) {
    if ((now - sessionStart) >= SEND_TIME_MS) {
      sessionActive = false;
      Serial.printf("[OTURUM] bitti, %u beacon\n", beaconCount);
    } else if (lastBeacon == 0 || (now - lastBeacon) >= BEACON_MS) {
      lastBeacon = now;
      sendBeacon();
    }
  }

  // Onay
  if (ackPending) handleAck();

  // LED: onayda surekli, oturumda blink, yoksa sonuk
  if (now < ackLedUntil)        LED_ON();
  else if (sessionActive)       ((now / 250) & 1) ? LED_ON() : LED_OFF();
  else                          LED_OFF();
}
