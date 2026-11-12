/*
================================================================================
  GLOSA Kopru — ESP32 WROOM-32D
  WiFi (AP + TCP) <-> UART2 <-> STM32   +   WiFi ayar (SSID/sifre) degistirme
================================================================================
  BAGLANTI:
    STM32 PB6 (TX)  -> ESP32 GPIO25 (RX2)
    STM32 PB7 (RX)  -> ESP32 GPIO27 (TX2)
    STM32 PA15      -> ESP32 EN (reset, STM32 open-drain)
    GND <-> GND

  WIFI AYARI (SSID/sifre degistirme):
    PC'den USB Serial (kablo) ya da WiFi/TCP ile su satir gelir:
        WIFICF:yeniSSID:yeniSifre\n
    ESP32 bunu NVS'ye kaydeder, yeniden baslar. Yeni ayarla AP kurar.
    Bu komut STM32'ye GITMEZ (ESP32'de kalir).
    Varsayilan: SSID=GLOSA_Panel, sifre=glosa1234.

  LED'ler (hepsi duz mantik, direncle GND'ye):
    IO12  veri / arac : STM32'den veri geldikce kisa yanar; arac istegi
                        kabul edilince VEH_LED_MS (2 sn) surekli yanar.
    IO14  WiFi        : PC bagliyken yanik.
    GPIO2 hazir modul : WiFi bagliyken yanik, veri gecince kisa soner.
    Acilista IO12, IO14 ve GPIO2 300 ms aralikla 3 kez yanip soner.

  KOPRU KONTROLU:
    STM32 "PINGES" + 10 x 0 gonderir; kopru PC'ye iletmez, "PONGES" + 10 x 0
    ile cevaplar. Cevap alamayan STM32, ESP32'yi EN pininden resetler.
    Ayrica loop() WDT_TIMEOUT_S icinde donmezse task watchdog resetler.

  ESP-NOW (oncelikli arac, RSSI degerlendirme):
    Arac ESP32-C3 butona basinca saniyede 4 'B' beacon yollar; onay gelince
    durur, gelmezse tanimli sure (varsayilan 2 sn) sonunda durur.
    Kopru her beacon'i aninda degerlendirir:
      beacon RSSI >= rssiEsik    -> KABUL (oturumda ilk kez)
    KABUL: STM32'ye 16 byte VEHICL ("VEHICL" + tip + ID H + ID L + 7 x 0)
           bir kez, C3'e unicast 'A' onay paketi. Kabul edilmis oturumdan
           gelen sonraki beacon'lara sadece onay tekrarlanir (C3 kacirdiysa).
    Her beacon esik ayari icin PC'ye raporlanir:
      "RSSIRP" tip idH idL oturum rssi min max adet kabul CS  (16 byte)
      rssi = bu beacon, min/max/adet = oturum boyunca. int8 dBm,
      CS = byte 6..14 toplami.
    ESP-NOW kanali = AP kanali (AP_CHANNEL).

  RSSI ESIGI (arayuzden ayarlanir):
    PC -> ESP32 (WiFi/TCP, 16 byte): "RSSIES" + [6] esik (mutlak deger, 30-100)
    Ornek: [6]=95 -> esik -95 dBm. Bu paket ESP32'de kalir (STM32'ye gitmez).
    Deger ESP32'nin NVS'sine kaydedilir (reset'te kaybolmaz). NVS'de kayit yoksa
    RSSI_ESIK (varsayilan -95) kullanilir. ESP32 ayni 16 byte'lik paketi
    PC'ye geri yollar (onay).
    RSSI icin Arduino ESP32 core 3.x gerekir (2.x'te RSSI 0 gelir).

  ARDUINO IDE: Board "ESP32 Dev Module", Upload
================================================================================
*/

#include <WiFi.h>
#include <Preferences.h>     // NVS — kalici SSID/sifre
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_arduino_version.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

// 0 = ESP-NOW kapali (sadece WiFi kopru), 1 = acik
#define ESPNOW_ENABLE  1

// ---- RSSI degerlendirme (sahada ayarlanacak) ----
#define RSSI_ESIK      (-95)   // Varsayilan esik (NVS'de kayit yoksa); arayuzden degistirilir (dBm)
#define PANEL_KOL      0xFF    // Onaydaki kol numarasi (0xFF = bilinmiyor, ileride panelden)

// Tipler en ustte: Arduino IDE fonksiyon prototiplerini ilk fonksiyondan
// once ekler; tipler ondan once tanimli olmazsa derleme hatasi verir.
// ---- ESP-NOW paketleri (C3 ile ayni yapi) ----
#define PKT_VER        2

typedef struct __attribute__((packed)) {   // C3 -> kopru, broadcast
  char     magic[4];   // "GLSA"
  uint8_t  ver;        // PKT_VER
  uint8_t  cmd;        // 'B' = beacon
  uint8_t  tip;        // arac tipi
  uint8_t  idH;        // arac ID yuksek
  uint8_t  idL;        // arac ID dusuk
  uint8_t  session;    // oturum no
  uint16_t count;      // oturum icindeki beacon sirasi
  int8_t   txPower;    // C3 gonderim gucu (dBm)
  uint8_t  cs;         // onceki baytlarin toplami
} BeaconPkt;

typedef struct __attribute__((packed)) {   // kopru -> C3, unicast
  char     magic[4];   // "GLSA"
  uint8_t  ver;        // PKT_VER
  uint8_t  cmd;        // 'A' = onay
  uint8_t  idH;
  uint8_t  idL;
  uint8_t  session;
  uint8_t  kol;
  uint8_t  status;     // 1 = alindi
  uint8_t  cs;
} AckPkt;

// ---- Alim kuyrugu (callback WiFi gorevinde: sadece kopyala) ----
typedef struct {
  uint8_t   mac[6];
  int8_t    rssi;
  BeaconPkt pkt;
} RxItem;

Preferences prefs;
int8_t rssiEsik = RSSI_ESIK;   // calisma degeri (acilista NVS'den yuklenir)

// ---- WiFi AP (varsayilan, NVS bos ise) ----
String apSSID = "GLOSA_Panel";
String apPASS = "glosa1234";
const int AP_CHANNEL = 1;
const int TCP_PORT   = 8080;

// ---- UART2 (STM32) ----
#define STM_RX_PIN 25
#define STM_TX_PIN 27
#define STM_BAUD   115200
HardwareSerial STM(2);

// ---- LED'ler (duz mantik) ----
#define LED_PIN       2       // hazir modul LED'i (WiFi + veri)
#define LED_DATA_PIN  12      // PCB: veri / arac istegi
#define LED_WIFI_PIN  14      // PCB: WiFi bagli
#define LED_ON()      digitalWrite(LED_PIN, HIGH)
#define LED_OFF()     digitalWrite(LED_PIN, LOW)
#define DATA_LED_MS   100     // veri geldikce IO12 yanma suresi (ms)
#define VEH_LED_MS    2000    // arac istegi kabulunde IO12 yanma suresi (ms)
#define BOOT_BLINK_MS 300     // acilis blink yarim periyodu (ms)

// ---- Watchdog ----
#define WDT_TIMEOUT_S 8       // loop() bu sure donmezse ESP32 resetlenir

WiFiServer server(TCP_PORT);
WiFiClient client;

bool          wifiConnected = false;
unsigned long blinkUntil    = 0;
const unsigned long BLINK_MS = 100;
inline void triggerBlink() { blinkUntil = millis() + BLINK_MS; }
unsigned long dataLedUntil = 0;     // IO12 veri yanmasi bitis
unsigned long vehLedUntil  = 0;     // IO12 arac istegi yanmasi bitis
inline bool ledActive(unsigned long until) { return (long)(until - millis()) > 0; }

// PC -> STM32 icin gelen veriyi satir/paket ayirma
// WiFi ayar komutu "WIFICF:...\n" metin; digerleri 16 byte ikili paket.
String serialLine = "";       // USB Serial WIFICF komut biriktirme

#define RXQ_SIZE 16
RxItem            rxq[RXQ_SIZE];
volatile uint8_t  rxHead = 0, rxTail = 0;
portMUX_TYPE      nowMux = portMUX_INITIALIZER_UNLOCKED;

void onNowData(const uint8_t* mac, int8_t rssi, const uint8_t* data, int len) {
  if (len != sizeof(BeaconPkt)) return;
  portENTER_CRITICAL(&nowMux);
  uint8_t next = (rxHead + 1) % RXQ_SIZE;
  if (next != rxTail) {                       // kuyruk doluysa paket atilir
    memcpy(rxq[rxHead].mac, mac, 6);
    rxq[rxHead].rssi = rssi;
    memcpy(&rxq[rxHead].pkt, data, sizeof(BeaconPkt));
    rxHead = next;
  }
  portEXIT_CRITICAL(&nowMux);
}

bool popRx(RxItem& it) {
  bool ok = false;
  portENTER_CRITICAL(&nowMux);
  if (rxTail != rxHead) {
    it = rxq[rxTail];
    rxTail = (rxTail + 1) % RXQ_SIZE;
    ok = true;
  }
  portEXIT_CRITICAL(&nowMux);
  return ok;
}

// ---- Oturum (bir arac, bir basis) ----
struct {
  bool     valid;
  uint16_t id;
  uint8_t  session;
  uint8_t  n;          // alinan beacon
  int8_t   mn, mx;     // oturumdaki en dusuk / en yuksek RSSI
  bool     accepted;   // VEHICL gonderildi
  uint32_t lastMs;     // son beacon zamani
} ses = {};
#define SES_GAP_MS 3000   // bu kadar beacon gelmezse ayni no ile gelen yeni oturum sayilir (C3 resetlenirse)

// PC'ye gidecek RSSI raporlari (STM32 verisinin arasina bolmeden girsin)
#define REP_SIZE 8
uint8_t  repQ[REP_SIZE][16];
uint8_t  repHead = 0, repTail = 0;
uint32_t lastStmByteMs = 0;

uint8_t sum8(const uint8_t* p, int n) {
  uint8_t s = 0;
  for (int i = 0; i < n; i++) s += p[i];
  return s;
}

void queueReport(uint8_t tip, uint16_t id, uint8_t session, int8_t rssi) {
  uint8_t next = (repHead + 1) % REP_SIZE;
  if (next == repTail) return;                 // dolu: rapor atilir (karar etkilenmez)
  uint8_t* r = repQ[repHead];
  memcpy(r, "RSSIRP", 6);
  r[6]  = tip;
  r[7]  = (uint8_t)(id >> 8);
  r[8]  = (uint8_t)(id & 0xFF);
  r[9]  = session;
  r[10] = (uint8_t)rssi;
  r[11] = (uint8_t)ses.mn;
  r[12] = (uint8_t)ses.mx;
  r[13] = ses.n;
  r[14] = ses.accepted ? 1 : 0;
  r[15] = sum8(&r[6], 9);
  repHead = next;
}

void startEspNow() {
  if (esp_now_init() != ESP_OK) { Serial.println("[ESPNOW] init hatasi"); return; }
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_now_register_recv_cb([](const esp_now_recv_info_t* info, const uint8_t* d, int l) {
    onNowData(info->src_addr, (int8_t)info->rx_ctrl->rssi, d, l);
  });
#else
  esp_now_register_recv_cb([](const uint8_t* mac, const uint8_t* d, int l) {
    onNowData(mac, 0, d, l);                  // core 2.x: RSSI yok
  });
#endif
  Serial.print("[ESPNOW] Hazir. Kanal: "); Serial.println(AP_CHANNEL);
}

// C3'e onay: gonderen MAC peer olarak eklenir, unicast yollanir
void sendAck(const uint8_t* mac, uint16_t id, uint8_t session, uint8_t status) {
  if (!esp_now_is_peer_exist(mac)) {
    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, mac, 6);
    peer.channel = AP_CHANNEL;
    peer.ifidx   = WIFI_IF_AP;
    peer.encrypt = false;
    esp_now_add_peer(&peer);
  }
  AckPkt a;
  memcpy(a.magic, "GLSA", 4);
  a.ver     = PKT_VER;
  a.cmd     = 'A';
  a.idH     = (uint8_t)(id >> 8);
  a.idL     = (uint8_t)(id & 0xFF);
  a.session = session;
  a.kol     = PANEL_KOL;
  a.status  = status;
  a.cs      = sum8((uint8_t*)&a, sizeof(a) - 1);
  esp_now_send(mac, (uint8_t*)&a, sizeof(a));
}

// Her beacon aninda: esik gecildiyse VEHICL + onay, PC'ye rapor
void handleBeacon(const RxItem& it) {
  const BeaconPkt& p = it.pkt;
  if (memcmp(p.magic, "GLSA", 4) != 0 || p.ver != PKT_VER || p.cmd != 'B') return;
  if (sum8((const uint8_t*)&p, sizeof(p) - 1) != p.cs) return;

  uint16_t id = ((uint16_t)p.idH << 8) | p.idL;
  if (id == 0) return;                                        // ID 0 gecersiz

  if (!ses.valid || id != ses.id || p.session != ses.session ||
      (millis() - ses.lastMs) >= SES_GAP_MS) {                      // yeni oturum
    ses.valid = true; ses.id = id; ses.session = p.session;
    ses.n = 0; ses.mn = 127; ses.mx = -128; ses.accepted = false;
  }
  ses.lastMs = millis();
  if (ses.n < 255) ses.n++;
  if (it.rssi < ses.mn) ses.mn = it.rssi;
  if (it.rssi > ses.mx) ses.mx = it.rssi;

  if (!ses.accepted && it.rssi >= rssiEsik) {
    ses.accepted = true;
    uint8_t out[16] = {'V','E','H','I','C','L', p.tip,
                       (uint8_t)(id >> 8), (uint8_t)(id & 0xFF), 0,0,0,0,0,0,0};
    STM.write(out, sizeof(out));
    triggerBlink();
    vehLedUntil = millis() + VEH_LED_MS;
    Serial.printf("[RSSI] id=%u tip=%u oturum=%u  rssi=%d  -> KABUL\n", id, p.tip, p.session, it.rssi);
  } else {
    Serial.printf("[RSSI] id=%u tip=%u oturum=%u  rssi=%d  %s\n", id, p.tip, p.session, it.rssi,
                  ses.accepted ? "(kabul edilmis)" : "RED");
  }
  if (ses.accepted) sendAck(it.mac, id, p.session, 1);       // C3 onayi kacirdiysa tekrar

  queueReport(p.tip, id, p.session, it.rssi);
}

// loop() icinden: kuyrugu bosalt, raporlari PC'ye yolla
void serviceEspNow() {
  RxItem it;
  while (popRx(it)) handleBeacon(it);

  // Rapor STM32 paketlerinin arasina bolmeden girsin: STM 5 ms sessizse yolla
  while (repTail != repHead && !STM.available() && (millis() - lastStmByteMs) >= 5) {
    if (client && client.connected()) {
      client.write(repQ[repTail], 16);
      client.flush();
    }
    repTail = (repTail + 1) % REP_SIZE;
  }
}

void updateLed() {
  // GPIO2 (hazir modul): WiFi bagliyken yanik, veri gecince kisa soner
  if (wifiConnected && !ledActive(blinkUntil)) LED_ON(); else LED_OFF();
  // IO14: WiFi bagli
  digitalWrite(LED_WIFI_PIN, wifiConnected ? HIGH : LOW);
  // IO12: arac istegi (2 sn surekli) ya da STM32 verisi (kisa)
  digitalWrite(LED_DATA_PIN, (ledActive(vehLedUntil) || ledActive(dataLedUntil)) ? HIGH : LOW);
}

// Acilis: PCB LED'leri ve modul LED'i 3 kez yanip soner
void bootBlink() {
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_DATA_PIN, HIGH); digitalWrite(LED_WIFI_PIN, HIGH); LED_ON();
    delay(BOOT_BLINK_MS);
    digitalWrite(LED_DATA_PIN, LOW);  digitalWrite(LED_WIFI_PIN, LOW);  LED_OFF();
    delay(BOOT_BLINK_MS);
  }
}

// STM32'den gelen veride "PINGES" paketi varsa cikarir ve "PONGES" ile cevaplar.
// buf en az n + 16 byte yer icermeli (yarim gelen paketin kalani okunur).
// Donus: PC'ye iletilecek kalan byte sayisi.
int handlePing(uint8_t* buf, int n) {
  static const uint8_t pong[16] = {'P','O','N','G','E','S',0,0,0,0,0,0,0,0,0,0};
  for (int i = 0; i + 6 <= n; ) {
    if (memcmp(&buf[i], "PINGES", 6) != 0) { i++; continue; }
    if (i + 16 > n) {                                   // paketin kalani gelmedi
      STM.setTimeout(5);
      n += STM.readBytes(&buf[n], i + 16 - n);
      if (i + 16 > n) break;                            // gelmedi: dokunma
    }
    memmove(&buf[i], &buf[i + 16], n - i - 16);         // pingi cikar
    n -= 16;
    STM.write(pong, sizeof(pong));
  }
  return n;
}

// NVS'den SSID/sifre yukle (yoksa varsayilan kalir)
void loadWifiConfig() {
  prefs.begin("glosa", true);            // salt-okunur
  String s = prefs.getString("ssid", "");
  String p = prefs.getString("pass", "");
  rssiEsik = prefs.getChar("rssi", RSSI_ESIK);
  if (rssiEsik < -100 || rssiEsik > -30) rssiEsik = RSSI_ESIK;   // gecersiz kayit
  prefs.end();
  if (s.length() > 0) apSSID = s;
  if (p.length() >= 8) apPASS = p;
}

// NVS'ye kaydet + yeniden baslat
void saveWifiConfig(const String& ssid, const String& pass) {
  prefs.begin("glosa", false);           // yazilabilir
  prefs.putString("ssid", ssid);
  prefs.putString("pass", pass);
  prefs.end();
  Serial.println("[WIFICF] Kaydedildi, yeniden baslaniyor...");
  delay(300);
  ESP.restart();
}

// Gelen metin satirini isle — WIFICF komutu mu?
// Format: WIFICF:ssid:pass
// Dönüs: true = komut islendi (STM32'ye gitmesin)
bool handleTextCommand(const String& line) {
  if (line.startsWith("WIFICF:")) {
    int p1 = line.indexOf(':');
    int p2 = line.indexOf(':', p1 + 1);
    if (p1 > 0 && p2 > p1) {
      String ssid = line.substring(p1 + 1, p2);
      String pass = line.substring(p2 + 1);
      pass.trim();
      if (ssid.length() > 0 && pass.length() >= 8) {
        Serial.print("[WIFICF] Yeni SSID: "); Serial.println(ssid);
        saveWifiConfig(ssid, pass);      // kaydet + restart (geri donmez)
      }
    }
    return true;                          // WIFICF idi (islendi)
  }
  return false;
}


// WiFi olaylari — PC baglanma / kopma seri monitorde gorunur
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_AP_START:           Serial.println("[AP] Basladi"); break;
    case ARDUINO_EVENT_WIFI_AP_STOP:            Serial.println("[AP] DURDU"); break;
    case ARDUINO_EVENT_WIFI_AP_STACONNECTED:    Serial.println("[AP] PC baglandi"); break;
    case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED: Serial.println("[AP] PC koptu"); break;
    case ARDUINO_EVENT_WIFI_AP_STAIPASSIGNED:   Serial.println("[AP] PC'ye IP verildi"); break;
    default: break;
  }
}

void startAP() {
  // Calisan WiFi test programindaki ayarlar (ESP32_WiFi_Test.ino)
  WiFi.persistent(false);                 // WiFi ayarini NVS'e yazma
  WiFi.disconnect(true, true);            // flash'ta kalan eski WiFi ayarini temizle
  WiFi.mode(WIFI_AP);
  WiFi.onEvent(onWiFiEvent);
  WiFi.softAPConfig(IPAddress(192,168,4,1), IPAddress(192,168,4,1), IPAddress(255,255,255,0));
  int tries = 0;
  while (!WiFi.softAP(apSSID.c_str(), apPASS.c_str(), AP_CHANNEL, 0, 4) && tries < 5) {
    Serial.println("[AP] softAP basarisiz, tekrar...");
    delay(300); tries++;
  }
  WiFi.setSleep(false);                   // modem uykusu kapali
  Serial.print("[AP] SSID: "); Serial.println(apSSID);
  Serial.print("[AP] IP  : "); Serial.println(WiFi.softAPIP());
}

void setup() {
  pinMode(LED_PIN, OUTPUT);      LED_OFF();
  pinMode(LED_DATA_PIN, OUTPUT); digitalWrite(LED_DATA_PIN, LOW);
  pinMode(LED_WIFI_PIN, OUTPUT); digitalWrite(LED_WIFI_PIN, LOW);   // IO14 pull-up ile yanmasin

  Serial.begin(115200);
  delay(200);
  Serial.println("\n[ESP32-WROOM] GLOSA Kopru basliyor...");
  Serial.printf("Reset nedeni: %d (1=PowerOn 4=Panic 6=TaskWDT 9=Brownout)\n", esp_reset_reason());

  STM.begin(STM_BAUD, SERIAL_8N1, STM_RX_PIN, STM_TX_PIN);

  bootBlink();           // 3 x 300 ms (STM32 verisi bu arada UART tamponunda bekler)

  loadWifiConfig();      // NVS'den SSID/sifre
  Serial.printf("[RSSI] esik=%d dBm\n", rssiEsik);
  startAP();
#if ESPNOW_ENABLE
  startEspNow();         // AP kanalinda arac isteklerini dinle
#endif

  server.begin();
  server.setNoDelay(true);

  // Task watchdog: loop() WDT_TIMEOUT_S icinde donmezse reset
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t wdt = { .timeout_ms = WDT_TIMEOUT_S * 1000, .idle_core_mask = 0, .trigger_panic = true };
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
#endif
  esp_task_wdt_add(NULL);
  Serial.print("[TCP] Port: "); Serial.println(TCP_PORT);
}


// ---- PC (TCP) -> ESP32 / STM32 ----
#define PC_FRAME 16        // PC paketleri 16 byte sabit

// "RSSIES" + [6] esik (mutlak deger): ESP32'de kalir, NVS'ye kaydedilir, PC'ye geri yollanir
void handleRssiCmd(uint8_t absVal) {
  int v = -(int)absVal;
  if (v < -100 || v > -30) {
    Serial.printf("[RSSIES] gecersiz deger: %d\n", v);
    return;
  }
  rssiEsik = (int8_t)v;
  prefs.begin("glosa", false);
  prefs.putChar("rssi", rssiEsik);
  prefs.end();
  Serial.printf("[RSSIES] esik=%d dBm (kaydedildi)\n", v);

  uint8_t echo[PC_FRAME] = {'R','S','S','I','E','S', absVal};   // kalan byte'lar 0
  if (client && client.connected()) { client.write(echo, PC_FRAME); client.flush(); }
}

// PC (TCP) -> STM32: 16 byte'lik cerceveler. "RSSIES" ESP32'de kalir, digerleri
// aynen STM32'ye gecer. Yarim/cop veri 20 ms sonra aynen STM32'ye iletilir.
// "WIFICF:ssid:sifre\n" metin satiri da ESP32'de kalir (WiFi ayari).
void servicePcLink() {
  static uint8_t  pc[PC_FRAME];
  static uint8_t  pcN = 0;
  static uint32_t pcLast = 0;
  static bool     textMode = false;     // WIFICF satiri okunuyor
  static String   textLine;

  while (client && client.connected() && client.available()) {
    uint8_t b = (uint8_t)client.read();
    if (textMode) {                                   // satir sonuna kadar topla
      if (b == '\n') { textMode = false; handleTextCommand(textLine); textLine = ""; }
      else if (b != '\r') {
        textLine += (char)b;
        if (textLine.length() > 120) { textMode = false; textLine = ""; }
      }
      continue;
    }
    pc[pcN++] = b;
    pcLast = millis();
    if (pcN == 7 && memcmp(pc, "WIFICF:", 7) == 0) {  // ikili cerceve degil, metin
      textMode = true; textLine = "WIFICF:"; pcN = 0;
      continue;
    }
    if (pcN == PC_FRAME) {
      if (memcmp(pc, "RSSIES", 6) == 0) handleRssiCmd(pc[6]);
      else                               STM.write(pc, PC_FRAME);
      pcN = 0;
    }
    triggerBlink();
  }
  if (pcN && (millis() - pcLast) >= 20) {
    STM.write(pc, pcN);
    pcN = 0;
  }
}

void loop() {
  esp_task_wdt_reset();

  // WiFi bagli cihaz sayisi
  bool nowConnected = (WiFi.softAPgetStationNum() > 0);
  if (nowConnected != wifiConnected) {
    wifiConnected = nowConnected;
    Serial.println(wifiConnected ? "[WiFi] Cihaz baglandi." : "[WiFi] Cihaz ayrildi.");
  }

  // Yeni TCP istemci
  if (!client || !client.connected()) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    WiFiClient nc = server.accept();        // core 3.x: yeni baglantiyi kabul et
#else
    WiFiClient nc = server.available();
#endif
    if (nc) { client = nc; client.setNoDelay(true); Serial.println("[TCP] Istemci baglandi."); }
  }

  // PC (TCP) -> STM32 (16 byte cerceve; RSSIES ESP32'de kalir)
  servicePcLink();

#if ESPNOW_ENABLE
  // ESP-NOW arac beacon'lari -> RSSI degerlendirme -> STM32 / C3 / PC
  serviceEspNow();
#endif

  // USB Serial -> WIFICF ayar (kablo ile de olur)
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleTextCommand(serialLine); serialLine = ""; }
    else if (c != '\r') { serialLine += c; if (serialLine.length() > 120) serialLine = ""; }
  }

  // STM32 -> PC
  // STM32 -> PC (toplu oku-yaz + flush — aninda gonder, biriktirme yok)
  if (STM.available()) {
    uint8_t tmp[64 + 16]; int n = 0;              // +16: yarim gelen ping icin yer
    while (STM.available() && n < 64) tmp[n++] = STM.read();
    lastStmByteMs = millis();
    n = handlePing(tmp, n);                       // PINGES -> PONGES, PC'ye gitmez
    if (n > 0) {
      if (client && client.connected()) {
        client.write(tmp, n);
        client.flush();         // TCP buffer'i hemen bosalt — 5sn birikme olmasin
      }
      triggerBlink();
      dataLedUntil = millis() + DATA_LED_MS;
    }
  }

  updateLed();
}
