# GLOSA Panel

GLOSA panel (STM32F401) ve verici (STM32F051) icin ayar / izleme araclari.

| Klasor | Icerik |
|---|---|
| `Android/` | Android uygulamasi (Kotlin + Compose), `PC/glosa_config.py`'nin telefon surumu |
| `PC/` | PC ayar ve canli izleme araci (Python, tkinter) |
| `ESP32/esp32_wroom_kopru/` | Panel tarafi ESP32 kopru: WiFi/TCP <-> UART, ESP-NOW arac istegi, RSSI |
| `ESP32/esp32_arac_verici/` | Arac verici (ESP32 / ESP32-S3 / ESP32-C3), ESP-NOW |

STM32 firmware'i bu depoda degil, PC'de (STM32CubeIDE) tutuluyor.

**Android APK:** `Android/` degisince GitHub otomatik derler; **Releases > son-apk > GLOSA.apk**.
Telefon, kopru ESP32'nin WiFi agina (varsayilan `GLOSA_Panel` / `glosa1234`) baglanir;
uygulama 192.168.4.1:8080'e TCP ile baglanir.
