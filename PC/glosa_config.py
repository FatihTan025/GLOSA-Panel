#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
  GLOSA Konfigurasyon + Canli Monitor — WiFi/TCP (ESP32-C3 kopru)
================================================================================
  PC/Telefon --WiFi--> ESP32-C3 (AP) --UART--> STM32

  PC -> Panel (F401):    PANCFG / E22CFG / VEHICL / E22RED / RTCRED (16 byte sabit)
  PC -> Verici (F051):   KAVCFG / MESAFE / CFGRED / RTCSET / RTCRED / E22CFG / E22RED
  STM32 -> PC:           'D' + 17 byte  (canli E22/GLOSA verisi)
                         "E22RED" + 9 register (E22 oku cevabi)
                         "RTCRED" + 7 byte (saat oku cevabi, panel ya da verici)
                         "CFGRED" + 17 byte (verici ayar oku cevabi)
                         "E22RSS" + 4 byte (panel RSSI/gurultu cevabi)
                         "RSSRED" + 9 byte (verici RSSI/gurultu cevabi)
                         "LOGRED" + 18 byte (verici oncelik kaydi, 2 kayit)
  ESP32 kopru -> PC:     "RSSIRP" + 10 byte (ESP-NOW arac beacon RSSI raporu)
                         tip idH idL oturum rssi min max adet kabul CS
                         (rssi = bu beacon; min/max/adet = oturum boyunca)
                         RSSI'lar isaretli byte (dBm), CS = 9 byte toplami
  PC <-> ESP32 kopru:    RSSIES [6]esik (mutlak deger, 30-100) -> ESP-NOW RSSI esigi;
                         kopru NVS'ye kaydeder, ayni 16 byte'i onay olarak geri yollar
                         (STM32'ye gitmez)

  Verici komutlari (F051 Define.h ile ayni):
    KAVCFG [6]Kol sayisi(1-6) [7]Max hiz(1-130) [8]Role on sure(0-60 sn)
           [9]Role darbe(1-3 sn) [10]Min hiz(1-Max)
    MESAFE [6]Kol(0-5) [7]Mesafe H [8]Mesafe L (1-3000 m) — kol basina 1 paket
    CFGRED cevap: KOL Max Lead Pulse 6x(Mesafe H,L) Min
    RTCSET [6]saat [7]dk [8]sn [9]gun [10]ay [11]yil [12]haftagunu(1=Pzt)

  RSSI degerleri: dBm = -(256 - deger), 0 = olcum yok.
  Canli veri (18 byte): [0]Command [1..6]Kol1-6 [7]Sec [8]Period
                        [9]Onay maskesi (bit0=Kol 1 ... bit5=Kol 6)
                        [10]Max hiz [11]Min hiz
                        [12]DEBUG role kalan sn (0=yok, 255=cekili)
                        [13..15]rezerve [16]checksum (byte 0-15 toplami)
                        [17]RSSI (panel ekler; 0 = RSSI byte kapali)
  Checksum tutmayan canli veri gosterilmez, "Hatali" sayacina yazilir.

  GEREKSINIM: sadece standart kutuphaneler (socket, threading, tkinter)
================================================================================
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog
import csv
import socket
import threading
try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False
import datetime

# ---- E22 register bit haritalari ----
UART_BAUD = {"1200":0,"2400":1,"4800":2,"9600":3,"19200":4,"38400":5,"57600":6,"115200":7}
AIR_RATE  = {"0.3k":0,"1.2k":1,"2.4k":2,"4.8k":3,"9.6k":4,"19.2k":5,"38.4k":6,"62.5k":7}
PARITY    = {"8N1":0,"8O1":1,"8E1":2}
SUB_PACKET= {"240":0,"128":1,"64":2,"32":3}
TX_POWER  = {"22dBm":0,"17dBm":1,"13dBm":2,"10dBm":3}
WOR_ROLE  = {"Alici":0,"Verici":1}
WOR_CYCLE = {"500ms":0,"1000ms":1,"1500ms":2,"2000ms":3,"2500ms":4,"3000ms":5,"3500ms":6,"4000ms":7}
COLORS    = {"BLACK":0,"RED":1,"GREEN":2,"YELLOW":3,"BLUE":4,"MAGENTA":5,"CYAN":6,"WHITE":7}
VEHICLE_TYPES = {"Ambulans":1,"Polis":2,"Askeri":3,"Burokrat":4,"Itfaiye":5}

# ---- 6 harfli komut kelimeleri (cöp korumasi) — STM32 ile ayni ----
CMD_PANEL   = b"PANCFG"
CMD_E22     = b"E22CFG"
CMD_VEHICLE = b"VEHICL"
CMD_READ    = b"E22RED"
CMD_RTCREAD = b"RTCRED"
CMD_KAVCFG  = b"KAVCFG"     # verici: kavsak ayari yaz
CMD_MESAFE  = b"MESAFE"     # verici: kol mesafesi yaz
CMD_CFGRED  = b"CFGRED"     # verici: ayar oku
CMD_RTCSET  = b"RTCSET"     # verici: saat ayarla
CMD_PRSSI   = b"E22RSS"     # panel: RSSI / gurultu oku
CMD_VRSSI   = b"RSSRED"     # verici: RSSI / gurultu oku
CMD_LOGRED  = b"LOGRED"     # verici: oncelik istegi kaydi oku ([6] = sira)
CMD_RSSIRP  = b"RSSIRP"     # ESP32 kopru: arac oturumu RSSI raporu
CMD_RSSIES  = b"RSSIES"     # ESP32 kopru: ESP-NOW RSSI esigi yaz (STM32'ye gitmez)
RSSI_VALUES = ["-95","-90","-85","-80","-75","-70"]   # secilebilir esikler (dBm)
RSSI_DEFAULT = "-95"
PACKET_SIZE = 16
SEQ_GAP_MS  = 250           # verici: ardisik paketler arasi bekleme (flash yazimi)
# Canli veri: 'D' + LIVE_SIZE byte. Verici/F401 paket boyu degisirse
# sadece burayi degistir.
LIVE_SIZE   = 18                     # 17 byte GLOSA paketi + 1 byte RSSI
LIVE_FRAME  = LIVE_SIZE + 1          # 'D' dahil
READ_REPLY  = len(CMD_READ) + 9      # "E22RED" + 9 register = 15
RTC_REPLY   = len(CMD_RTCREAD) + 7   # "RTCRED" + saat dk sn gun ay yil hg = 13
CFG_REPLY   = len(CMD_CFGRED) + 17   # "CFGRED" + KOL Max Lead Pulse 12 mesafe Min = 23
PRSSI_REPLY = len(CMD_PRSSI) + 4     # gurultu, son paket, son GLOSA paketi, RSSI acik
VRSSI_REPLY = len(CMD_VRSSI) + 9     # gurultu, son paket, 6 kol istek RSSI, RSSI acik
LOG_REPLY   = len(CMD_LOGRED) + 18   # toplam, sira, 2 x 8 byte kayit
LOG_TIMEOUT = 2000                   # kayit parcasi bekleme (ms)
RSSIRP_REPLY= len(CMD_RSSIRP) + 10   # tip idH idL oturum rssi min max adet kabul CS = 16
REPLY_CMDS  = (CMD_READ, CMD_RTCREAD, CMD_CFGRED, CMD_PRSSI, CMD_VRSSI, CMD_LOGRED, CMD_RSSIRP, CMD_RSSIES)

# Canli monitor sutun etiketleri (LIVE_SIZE adet, paket sirasiyla).
# Rezerve bir byte kullanilmaya baslaninca sadece adini degistir.
LIVE_LABELS = ["Cmd", "K1", "K2", "K3", "K4", "K5", "K6", "Sn", "Per",
               "Onay", "Max", "Min", "Role", "R1", "R2", "R3", "CS", "RSSI"]
assert len(LIVE_LABELS) == LIVE_SIZE
MON_FONT    = ("Consolas", 10)
COL_GAP     = 2                      # sutunlar arasi ek bosluk
NO_WIDTH    = 6                      # "No" sutunu genisligi
# Her sutun: etiket ya da 3 haneli deger (255), hangisi uzunsa + COL_GAP
COL_WIDTHS  = [max(len(l), 3) + COL_GAP for l in LIVE_LABELS]
COL_WIDTHS[-1] = 4 + COL_GAP         # RSSI: "-120" (dBm)

def s8(v):
    # isaretli byte -> int (ESP-NOW RSSI, dBm)
    return v - 256 if v > 127 else v

def dbm(v):
    # E22 RSSI byte'i -> dBm metni (0 = olcum yok)
    return "-" if v == 0 else str(-(256 - v))
# PANCFG [14] bayraklari — STM32 GLOSA_Config.h ile ayni
FLAG_TABLE  = 0x01   # veri kesilince tablodan oku
FLAG_ROUND  = 0x02   # hizi 5'in katina yuvarla
FLAG_MIN    = 0x04   # hiz min hizdan kucukse logo (ebb) goster
DATA_SELECT = {"Kol 1":0,"Kol 2":1,"Kol 3":2,"Kol 4":3,"Kol 5":4,"Kol 6":5,"Saniye":6,"Period":7}

UART_BAUD_R={v:k for k,v in UART_BAUD.items()}; AIR_RATE_R={v:k for k,v in AIR_RATE.items()}
PARITY_R={v:k for k,v in PARITY.items()}; SUB_PACKET_R={v:k for k,v in SUB_PACKET.items()}
TX_POWER_R={v:k for k,v in TX_POWER.items()}
WOR_ROLE_R={v:k for k,v in WOR_ROLE.items()}; WOR_CYCLE_R={v:k for k,v in WOR_CYCLE.items()}

def build_reg0(b,p,a): return (UART_BAUD[b]<<5)|(PARITY[p]<<3)|AIR_RATE[a]
def build_reg1(s,r,p): return (SUB_PACKET[s]<<6)|((1 if r else 0)<<5)|TX_POWER[p]
# REG3: bit7 RSSI byte, bit6 fixed, bit5 repeater, bit4 LBT, bit3 WOR rolu, bit2-0 WOR periyodu
def build_reg3(rssi,fixed,rep,lbt,wor_role,wor_cyc):
    return (((1 if rssi else 0)<<7)|((1 if fixed else 0)<<6)|((1 if rep else 0)<<5)|
            ((1 if lbt else 0)<<4)|((WOR_ROLE[wor_role]&1)<<3)|(WOR_CYCLE[wor_cyc]&7))


class ConfigApp:
    def __init__(self, root):
        self.root = root
        self.sock = None
        self.ser = None
        self.conn = None      # aktif baglanti (sock ya da ser)
        self.rx_thread = None
        self.running = False
        self.pkt_index = 0
        self.bad_cnt = 0        # checksum tutmayan canli veri sayisi
        self.buf = b""          # gelen veri tamponu (sadece rx thread kullanir)
        self.seq_busy = False   # verici paket dizisi gonderiliyor
        self.rtc_src = "Panel"  # saat cevabi kime ait (log icin)
        self.log_recs = []      # verici oncelik kayitlari (okunan)
        self.log_timer = None   # kayit okuma zaman asimi
        root.title("GLOSA Konfigurasyon (WiFi)")
        root.geometry("560x680")

        # ===== UST: Baglanti (WiFi / UART secimi) =====
        top = ttk.Frame(root, padding=(10,8)); top.pack(fill="x")

        # Baglanti tipi secici
        self.conn_type = tk.StringVar(value="WiFi")
        ttk.Radiobutton(top, text="WiFi", variable=self.conn_type, value="WiFi",
                        command=self._upd_conn_fields).pack(side="left")
        ttk.Radiobutton(top, text="UART", variable=self.conn_type, value="UART",
                        command=self._upd_conn_fields).pack(side="left", padx=(0,8))

        # --- WiFi alanlari ---
        self.wifi_frame = ttk.Frame(top)
        ttk.Label(self.wifi_frame, text="IP:").pack(side="left")
        self.ent_ip = ttk.Entry(self.wifi_frame, width=13); self.ent_ip.insert(0,"192.168.4.1")
        self.ent_ip.pack(side="left", padx=4)
        ttk.Label(self.wifi_frame, text="Port:").pack(side="left")
        self.ent_port = ttk.Entry(self.wifi_frame, width=6); self.ent_port.insert(0,"8080")
        self.ent_port.pack(side="left", padx=4)

        # --- UART alanlari ---
        self.uart_frame = ttk.Frame(top)
        ttk.Label(self.uart_frame, text="COM:").pack(side="left")
        self.cbo_com = ttk.Combobox(self.uart_frame, width=10, state="readonly")
        self.cbo_com.pack(side="left", padx=4)
        ttk.Button(self.uart_frame, text="\u21BB", width=2, command=self._refresh_com).pack(side="left")
        ttk.Label(self.uart_frame, text="Baud:").pack(side="left", padx=(6,0))
        self.cbo_baud = ttk.Combobox(self.uart_frame, width=8, state="readonly",
                                     values=["9600","115200"]); self.cbo_baud.set("115200")
        self.cbo_baud.pack(side="left", padx=4)

        self.btn_conn = ttk.Button(top, text="Baglan", width=8, command=self.toggle_connect)
        self.btn_conn.pack(side="left", padx=6)
        self.lbl_status = ttk.Label(top, text="\u25CF", foreground="red")
        self.lbl_status.pack(side="left")

        self._upd_conn_fields()   # baslangicta WiFi goster

        # ===== SEKMELER =====
        nb = ttk.Notebook(root); nb.pack(fill="x", padx=10, pady=6)
        te=ttk.Frame(nb,padding=12); tp=ttk.Frame(nb,padding=12); tv=ttk.Frame(nb,padding=12)
        tesp=ttk.Frame(nb,padding=12); ttx=ttk.Frame(nb,padding=12)
        nb.add(te,text="E22"); nb.add(tp,text="Panel"); nb.add(ttx,text="Verici")
        nb.add(tv,text="Vehicle"); nb.add(tesp,text="ESP32")
        self._build_e22(te); self._build_panel(tp); self._build_verici(ttx)
        self._build_vehicle(tv); self._build_esp32(tesp)

        # ===== CANLI MONITOR =====
        mon = ttk.LabelFrame(root, text="Canli Veri (STM32 <- E22)", padding=5)
        mon.pack(fill="both", expand=True, padx=10, pady=(0,4))
        bar = ttk.Frame(mon); bar.pack(fill="x")
        ttk.Button(bar, text="Temizle", command=self.clear_mon).pack(side="left")
        self.lbl_cnt = ttk.Label(bar, text="Paket: 0", foreground="#0066cc")
        self.lbl_cnt.pack(side="left", padx=10)
        # Sabit baslik satiri: kaydirmada kaybolmaz, sutunlar tam altinda.
        # Text'in kenar + ic boslugu ile ayni sol bosluk -> hizali.
        self.lbl_head = tk.Label(mon, text=self._live_header(), font=MON_FONT,
                                 anchor="w", justify="left", padx=3, bd=0,
                                 foreground="#0066cc")
        self.lbl_head.pack(fill="x", pady=(4,0))
        mon_wrap = ttk.Frame(mon); mon_wrap.pack(fill="both", expand=True)
        mon_scroll = ttk.Scrollbar(mon_wrap)
        mon_scroll.pack(side="right", fill="y")
        self.mon = tk.Text(mon_wrap, height=12, font=MON_FONT, wrap="none",
                           bd=1, highlightthickness=0, padx=2,
                           exportselection=True, yscrollcommand=mon_scroll.set)
        self.mon.pack(side="left", fill="both", expand=True)
        mon_scroll.config(command=self.mon.yview)
        # Kopyalama: Ctrl+C, Ctrl+Insert ve sag tik menu
        self.mon.bind("<Control-c>", self._copy_selection)
        self.mon.bind("<Control-C>", self._copy_selection)
        self.mon.bind("<Control-Insert>", self._copy_selection)
        self._mon_menu = tk.Menu(self.mon, tearoff=0)
        self._mon_menu.add_command(label="Kopyala", command=self._copy_selection)
        self._mon_menu.add_command(label="Tumunu Sec", command=self._select_all_mon)
        self.mon.bind("<Button-3>", self._show_mon_menu)

        # ===== ALT: Log =====
        low = ttk.LabelFrame(root, text="Log", padding=4); low.pack(fill="x", padx=10, pady=(0,8))
        self.txt = tk.Text(low, height=3, font=("Consolas", 9)); self.txt.pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Pencere genisligi: monitor satiri tam sigsin (yazi tipi olcusune gore)
        char_w = tkfont.Font(font=MON_FONT).measure("0")
        line_w = char_w * (NO_WIDTH + sum(COL_WIDTHS) + 2)
        win_w  = max(560, line_w + 60)          # kenar bosluklari + kaydirma cubugu
        root.geometry(f"{win_w}x680")
        root.minsize(560, 500)

    # ---------------- SEKMELER ----------------
    def _build_e22(self, f):
        g=ttk.Frame(f); g.pack(fill="x")
        ttk.Label(g,text="Adres:").grid(row=0,column=0,sticky="w",pady=3)
        self.ent_addr=ttk.Entry(g,width=10); self.ent_addr.insert(0,"1"); self.ent_addr.grid(row=0,column=1,padx=5)
        ttk.Label(g,text="NetID:").grid(row=0,column=2,sticky="w",padx=(12,0))
        self.ent_netid=ttk.Entry(g,width=6); self.ent_netid.insert(0,"0"); self.ent_netid.grid(row=0,column=3,padx=5)
        ttk.Label(g,text="Kanal:").grid(row=1,column=0,sticky="w",pady=3)
        self.ent_ch=ttk.Entry(g,width=10); self.ent_ch.insert(0,"18"); self.ent_ch.grid(row=1,column=1,padx=5)
        self.ent_ch.bind("<KeyRelease>", self.upd_freq)
        self.lbl_freq=ttk.Label(g,text="868.125 MHz",foreground="#0066cc"); self.lbl_freq.grid(row=1,column=2,columnspan=2,sticky="w")
        ttk.Label(g,text="UART:").grid(row=2,column=0,sticky="w",pady=3)
        self.cbo_uart=ttk.Combobox(g,width=8,state="readonly",values=list(UART_BAUD)); self.cbo_uart.set("9600"); self.cbo_uart.grid(row=2,column=1,padx=5)
        ttk.Label(g,text="Parity:").grid(row=2,column=2,sticky="w",padx=(12,0))
        self.cbo_par=ttk.Combobox(g,width=6,state="readonly",values=list(PARITY)); self.cbo_par.set("8N1"); self.cbo_par.grid(row=2,column=3,padx=5)
        ttk.Label(g,text="Air Rate:").grid(row=3,column=0,sticky="w",pady=3)
        self.cbo_air=ttk.Combobox(g,width=8,state="readonly",values=list(AIR_RATE)); self.cbo_air.set("2.4k"); self.cbo_air.grid(row=3,column=1,padx=5)
        ttk.Label(g,text="Guc:").grid(row=3,column=2,sticky="w",padx=(12,0))
        self.cbo_pow=ttk.Combobox(g,width=6,state="readonly",values=list(TX_POWER)); self.cbo_pow.set("22dBm"); self.cbo_pow.grid(row=3,column=3,padx=5)
        ttk.Label(g,text="Sub-pkt:").grid(row=4,column=0,sticky="w",pady=3)
        self.cbo_sub=ttk.Combobox(g,width=8,state="readonly",values=list(SUB_PACKET)); self.cbo_sub.set("240"); self.cbo_sub.grid(row=4,column=1,padx=5)
        ttk.Label(g,text="Key:").grid(row=4,column=2,sticky="w",padx=(12,0))
        self.ent_key=ttk.Entry(g,width=6); self.ent_key.insert(0,"0"); self.ent_key.grid(row=4,column=3,padx=5)
        self.var_rssi=tk.BooleanVar(); ttk.Checkbutton(g,text="RSSI byte",variable=self.var_rssi).grid(row=5,column=0,columnspan=2,sticky="w",pady=2)
        self.var_fixed=tk.BooleanVar(value=True); ttk.Checkbutton(g,text="Fixed mode",variable=self.var_fixed).grid(row=5,column=2,columnspan=2,sticky="w")
        self.var_lbt=tk.BooleanVar(); ttk.Checkbutton(g,text="LBT (gondermeden dinle)",variable=self.var_lbt).grid(row=6,column=0,columnspan=2,sticky="w",pady=2)
        self.var_rep=tk.BooleanVar(); ttk.Checkbutton(g,text="Repeater",variable=self.var_rep).grid(row=6,column=2,columnspan=2,sticky="w")
        self.var_noise=tk.BooleanVar(); ttk.Checkbutton(g,text="Ortam gurultusu (RSSI)",variable=self.var_noise).grid(row=7,column=0,columnspan=2,sticky="w",pady=2)
        ttk.Label(g,text="WOR rol:").grid(row=8,column=0,sticky="w",pady=3)
        self.cbo_wrole=ttk.Combobox(g,width=8,state="readonly",values=list(WOR_ROLE)); self.cbo_wrole.set("Alici"); self.cbo_wrole.grid(row=8,column=1,padx=5)
        ttk.Label(g,text="WOR sure:").grid(row=8,column=2,sticky="w",padx=(12,0))
        self.cbo_wcyc=ttk.Combobox(g,width=7,state="readonly",values=list(WOR_CYCLE)); self.cbo_wcyc.set("2000ms"); self.cbo_wcyc.grid(row=8,column=3,padx=5)
        b=ttk.Frame(f); b.pack(fill="x",pady=(12,0))
        ttk.Button(b,text="Gonder",width=12,command=self.send_e22).pack(side="left",padx=6)
        ttk.Button(b,text="Oku",width=12,command=self.read_e22).pack(side="left",padx=6)

    def _build_panel(self, f):
        g=ttk.Frame(f); g.pack(fill="x")
        ttk.Label(g,text="Gosterilecek veri:").grid(row=0,column=0,sticky="w",pady=4)
        self.cbo_data=ttk.Combobox(g,width=12,state="readonly",values=list(DATA_SELECT)); self.cbo_data.set("Kol 4"); self.cbo_data.grid(row=0,column=1,padx=6)
        ttk.Label(g,text="Renk:").grid(row=1,column=0,sticky="w",pady=4)
        self.cbo_color=ttk.Combobox(g,width=12,state="readonly",values=list(COLORS)); self.cbo_color.set("GREEN"); self.cbo_color.grid(row=1,column=1,padx=6)
        # Secenekler iki grup halinde yan yana (pencere uzamasin)
        opt=ttk.Frame(f); opt.pack(fill="x",pady=(8,0))
        g1=ttk.LabelFrame(opt,text="Veri",padding=(8,4)); g1.pack(side="left",fill="y",padx=(0,8))
        g2=ttk.LabelFrame(opt,text="Gosterim",padding=(8,4)); g2.pack(side="left",fill="y")
        self.var_live=tk.BooleanVar(value=True); ttk.Checkbutton(g1,text="Canli veri gonder (her saniye)",variable=self.var_live).pack(anchor="w",pady=2)
        self.var_table=tk.BooleanVar(value=True); ttk.Checkbutton(g1,text="Veri kesilince tablodan oku",variable=self.var_table).pack(anchor="w",pady=2)
        self.var_round=tk.BooleanVar(value=False); ttk.Checkbutton(g2,text="Hizi 5'e yuvarla (20, 25, 30 ...)",variable=self.var_round).pack(anchor="w",pady=2)
        self.var_min=tk.BooleanVar(value=False); ttk.Checkbutton(g2,text="Min hiz aktif (altinda logo goster)",variable=self.var_min).pack(anchor="w",pady=2)
        pb=ttk.Frame(f); pb.pack(anchor="w",pady=(12,0))
        ttk.Button(pb,text="Gonder",width=12,command=self.send_panel).pack(side="left")
        ttk.Button(pb,text="Saati oku",width=12,command=self.read_rtc).pack(side="left",padx=(12,0))
        ttk.Button(pb,text="RSSI oku",width=12,command=self.read_panel_rssi).pack(side="left",padx=(12,0))

    def _build_verici(self, f):
        # Kavsak ayarlari (KAVCFG) ve kol mesafeleri (MESAFE) yan yana
        top=ttk.Frame(f); top.pack(fill="x")
        g=ttk.LabelFrame(top,text="Kavsak",padding=(8,4)); g.pack(side="left",fill="y",padx=(0,8))
        def ent(row,text,val,unit):
            ttk.Label(g,text=text).grid(row=row,column=0,sticky="w",pady=2)
            e=ttk.Entry(g,width=6); e.insert(0,val); e.grid(row=row,column=1,padx=4)
            ttk.Label(g,text=unit,foreground="#888").grid(row=row,column=2,sticky="w")
            return e
        ttk.Label(g,text="Kol sayisi:").grid(row=0,column=0,sticky="w",pady=2)
        self.cbo_kol=ttk.Combobox(g,width=4,state="readonly",values=["1","2","3","4","5","6"])
        self.cbo_kol.set("4"); self.cbo_kol.grid(row=0,column=1,padx=4)
        self.cbo_kol.bind("<<ComboboxSelected>>", self._upd_kol)
        self.ent_hmax =ent(1,"Max hiz:","50","km/h")
        self.ent_hmin =ent(2,"Min hiz:","30","km/h")
        self.ent_lead =ent(3,"Role on sure:","10","sn (0-60)")
        self.ent_pulse=ent(4,"Role darbe:","1","sn (1-3)")

        m=ttk.LabelFrame(top,text="Kol mesafesi (m)",padding=(8,4)); m.pack(side="left",fill="y")
        self.ent_mes=[]
        for k in range(6):
            r,c=k%3,(k//3)*2
            ttk.Label(m,text=f"Kol {k+1}:").grid(row=r,column=c,sticky="w",pady=2,padx=(0 if c==0 else 10,0))
            e=ttk.Entry(m,width=6); e.insert(0,"500"); e.grid(row=r,column=c+1,padx=4)
            self.ent_mes.append(e)
        self._upd_kol()

        b=ttk.Frame(f); b.pack(anchor="w",pady=(12,0))
        self.btn_kav=ttk.Button(b,text="Gonder",width=12,command=self.send_verici)
        self.btn_kav.pack(side="left")
        ttk.Button(b,text="Oku",width=12,command=self.read_verici).pack(side="left",padx=(12,0))
        ttk.Button(b,text="Saati ayarla",width=12,command=self.set_verici_rtc).pack(side="left",padx=(12,0))
        ttk.Button(b,text="Saati oku",width=12,command=self.read_verici_rtc).pack(side="left",padx=(12,0))
        b2=ttk.Frame(f); b2.pack(anchor="w",pady=(6,0))
        ttk.Button(b2,text="RSSI oku",width=12,command=self.read_verici_rssi).pack(side="left")
        ttk.Button(b2,text="Kayitlari oku",width=12,command=self.read_logs).pack(side="left",padx=(12,0))

    def _upd_kol(self, e=None):
        # Kol sayisi disindaki mesafe kutulari pasif
        n=int(self.cbo_kol.get())
        for k,ent in enumerate(self.ent_mes):
            ent.config(state="normal" if k<n else "disabled")

    def _build_vehicle(self, f):
        g=ttk.Frame(f); g.pack(fill="x")
        ttk.Label(g,text="Arac Tipi:").grid(row=0,column=0,sticky="w",pady=4)
        self.cbo_vtype=ttk.Combobox(g,width=12,state="readonly",values=list(VEHICLE_TYPES)); self.cbo_vtype.set("Ambulans"); self.cbo_vtype.grid(row=0,column=1,padx=6)
        ttk.Label(g,text="Arac ID:").grid(row=1,column=0,sticky="w",pady=4)
        self.ent_vid=ttk.Entry(g,width=10); self.ent_vid.insert(0,"0"); self.ent_vid.grid(row=1,column=1,padx=6)
        ttk.Button(f,text="Gonder",width=12,command=self.send_vehicle).pack(anchor="w",pady=(12,0))

        # ESP-NOW arac oturumlari: kopru her oturum sonunda RSSI raporu yollar.
        # Esik (RSSI_ESIK) ve en az paket (MIN_PAKET) kopru kodundadir.
        rf=ttk.LabelFrame(f,text="ESP-NOW RSSI (esik ayari icin)",padding=(6,4))
        rf.pack(fill="both",expand=True,pady=(12,0))
        cols=("Saat","Tip","ID","Oturum","RSSI","Min","Max","Adet","Sonuc")
        self.tv_rssi=ttk.Treeview(rf,columns=cols,show="headings",height=6)
        for c,wd in zip(cols,(70,75,45,55,60,45,45,45,60)):
            self.tv_rssi.heading(c,text=c); self.tv_rssi.column(c,width=wd,anchor="center")
        sb=ttk.Scrollbar(rf,command=self.tv_rssi.yview); self.tv_rssi.configure(yscrollcommand=sb.set)
        self.tv_rssi.pack(side="left",fill="both",expand=True); sb.pack(side="right",fill="y")
        rb=ttk.Frame(f); rb.pack(anchor="w",pady=(4,0))
        ttk.Button(rb,text="Temizle",width=10,
                   command=lambda: self.tv_rssi.delete(*self.tv_rssi.get_children())).pack(side="left")
        ttk.Button(rb,text="CSV kaydet",width=10,command=self.save_rssi).pack(side="left",padx=(8,0))
        self.lbl_rssi_sum=ttk.Label(rb,text="",foreground="#0066cc"); self.lbl_rssi_sum.pack(side="left",padx=10)

    def _build_esp32(self, f):
        row=ttk.Frame(f); row.pack(fill="x")
        # --- WiFi (AP) ---
        w=ttk.LabelFrame(row,text="WiFi (AP)",padding=(8,4)); w.pack(side="left",fill="y",padx=(0,8))
        ttk.Label(w,text="SSID:").grid(row=0,column=0,sticky="w",pady=3)
        self.ent_ssid=ttk.Entry(w,width=20); self.ent_ssid.insert(0,"GLOSA_Panel"); self.ent_ssid.grid(row=0,column=1,padx=6)
        ttk.Label(w,text="Sifre:").grid(row=1,column=0,sticky="w",pady=3)
        self.ent_pass=ttk.Entry(w,width=20); self.ent_pass.insert(0,"glosa1234"); self.ent_pass.grid(row=1,column=1,padx=6)
        ttk.Label(w,text="Gonderince ESP32 yeniden baslar,\nbaglanti kopar.",
                  foreground="#888").grid(row=2,column=0,columnspan=2,sticky="w",pady=(4,0))
        ttk.Button(w,text="Gonder",width=12,command=self.send_wifi).grid(row=3,column=0,columnspan=2,sticky="w",pady=(6,0))
        # --- ESP-NOW ---
        n=ttk.LabelFrame(row,text="ESP-NOW",padding=(8,4)); n.pack(side="left",fill="y")
        ttk.Label(n,text="RSSI esik (dBm):").grid(row=0,column=0,sticky="w",pady=3)
        self.cbo_rssi=ttk.Combobox(n,width=6,state="readonly",values=RSSI_VALUES)
        self.cbo_rssi.set(RSSI_DEFAULT); self.cbo_rssi.grid(row=0,column=1,padx=6)
        ttk.Label(n,text="-95 = en uzaktan kabul\n-70 = en yakindan kabul",
                  foreground="#888").grid(row=1,column=0,columnspan=2,sticky="w",pady=(4,0))
        ttk.Button(n,text="Gonder",width=12,command=self.send_rssi).grid(row=2,column=0,columnspan=2,sticky="w",pady=(6,0))
        self.lbl_rssi=ttk.Label(f,text="Son beacon: -",foreground="#0066cc")
        self.lbl_rssi.pack(anchor="w",pady=(8,0))

    # ---------------- BAGLANTI ----------------
    def _upd_conn_fields(self):
        # secime gore WiFi ya da UART alanlarini goster
        if self.conn_type.get()=="WiFi":
            self.uart_frame.pack_forget()
            self.wifi_frame.pack(side="left", before=self.btn_conn)
        else:
            self.wifi_frame.pack_forget()
            self.uart_frame.pack(side="left", before=self.btn_conn)
            self._refresh_com()

    def _refresh_com(self):
        if not HAS_SERIAL: return
        ports=[p.device for p in serial.tools.list_ports.comports()]
        self.cbo_com["values"]=ports
        if ports and not self.cbo_com.get(): self.cbo_com.set(ports[0])

    def log(self,m): self.txt.insert("end",m+"\n"); self.txt.see("end")

    def upd_freq(self,e=None):
        try:
            c=int(self.ent_ch.get()); self.lbl_freq.config(text=f"{850.125+c:.3f} MHz" if 0<=c<=80 else "gecersiz")
        except ValueError: self.lbl_freq.config(text="\u2014")

    def toggle_connect(self):
        if self.conn:
            self.disconnect(); return

        if self.conn_type.get()=="WiFi":
            # --- WiFi (TCP) ---
            ip=self.ent_ip.get().strip()
            try: port=int(self.ent_port.get())
            except ValueError: messagebox.showerror("Hata","Port sayi olmali."); return
            try:
                s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5); s.connect((ip,port)); s.settimeout(1.0)
                self.sock=s; self.conn=s
                self.log(f"WiFi baglandi: {ip}:{port}")
            except Exception as e:
                messagebox.showerror("Baglanti Hatasi", str(e)); return
        else:
            # --- UART ---
            if not HAS_SERIAL:
                messagebox.showerror("Hata","pyserial kurulu degil.\npip install pyserial"); return
            port=self.cbo_com.get()
            if not port: messagebox.showwarning("Uyari","COM secin."); return
            try:
                self.ser=serial.Serial(port, int(self.cbo_baud.get()), timeout=0.1)
                self.conn=self.ser
                self.log(f"UART baglandi: {port} @ {self.cbo_baud.get()}")
            except Exception as e:
                messagebox.showerror("Baglanti Hatasi", str(e)); return

        self.running=True
        self.rx_thread=threading.Thread(target=self.rx_loop, daemon=True); self.rx_thread.start()
        self.btn_conn.config(text="Kapat"); self.lbl_status.config(foreground="green")

    def disconnect(self):
        self.running=False
        try:
            if self.sock: self.sock.close()
        except: pass
        try:
            if self.ser: self.ser.close()
        except: pass
        self.sock=None; self.ser=None; self.conn=None
        self.btn_conn.config(text="Baglan"); self.lbl_status.config(foreground="red")
        self.log("Baglanti kapatildi.")

    def on_close(self):
        self.running=False
        try:
            if self.sock: self.sock.close()
        except: pass
        try:
            if self.ser: self.ser.close()
        except: pass
        self.root.destroy()

    # ---------------- GONDERME ----------------
    def send_packet(self, cmd, content=None):
        # cmd = 6 harflik komut (bytes), content = icerik (list/bytes)
        if not self.conn:
            messagebox.showwarning("Uyari","Once baglanin."); return False
        pkt = bytearray(cmd)                 # ilk 6 byte komut
        if content: pkt += bytes(content)    # icerik
        while len(pkt) < PACKET_SIZE: pkt.append(0)   # 16'ya tamamla
        pkt = pkt[:PACKET_SIZE]
        try:
            if self.sock: self.sock.sendall(bytes(pkt))   # WiFi
            else: self.ser.write(bytes(pkt))              # UART
            return True
        except Exception as e:
            messagebox.showerror("Gonderme Hatasi",str(e)); self.disconnect(); return False

    def send_e22(self):
        try:
            addr=int(self.ent_addr.get()); netid=int(self.ent_netid.get())
            ch=int(self.ent_ch.get()); key=int(self.ent_key.get())
            assert 0<=addr<=65535 and 0<=netid<=255 and 0<=ch<=80 and 0<=key<=65535
        except (ValueError,AssertionError):
            messagebox.showerror("Hata","Gecersiz E22 degeri."); return
        reg0=build_reg0(self.cbo_uart.get(),self.cbo_par.get(),self.cbo_air.get())
        reg1=build_reg1(self.cbo_sub.get(),self.var_noise.get(),self.cbo_pow.get())
        reg2=ch&0x7F
        reg3=build_reg3(self.var_rssi.get(),self.var_fixed.get(),self.var_rep.get(),
                        self.var_lbt.get(),self.cbo_wrole.get(),self.cbo_wcyc.get())
        regs=[(addr>>8)&0xFF,addr&0xFF,netid&0xFF,reg0,reg1,reg2,reg3,(key>>8)&0xFF,key&0xFF]
        if self.send_packet(CMD_E22, regs): self.log(f"E22 gonderildi: kanal={ch}")

    def read_e22(self):
        if not self.conn: messagebox.showwarning("Uyari","Once baglanin."); return
        # Tampona sadece rx thread dokunur; temizlemeyi ona birak (yaris olmasin)
        if self.send_packet(CMD_READ): self.log("E22 oku istegi gonderildi (E22RED)")

    def read_rtc(self):
        self.rtc_src="Panel"
        if self.send_packet(CMD_RTCREAD): self.log("Panel saati istendi (RTCRED)")

    # ---- Verici (F051) ----
    def send_verici(self):
        if not self.conn: messagebox.showwarning("Uyari","Once baglanin."); return
        if self.seq_busy: return
        try:
            kol=int(self.cbo_kol.get())
            hmax=int(self.ent_hmax.get()); hmin=int(self.ent_hmin.get())
            lead=int(self.ent_lead.get()); pulse=int(self.ent_pulse.get())
            assert 1<=hmax<=130, "Max hiz 1-130 olmali."
            assert 1<=hmin<=hmax, "Min hiz 1 ile Max hiz arasinda olmali."
            assert 0<=lead<=60, "Role on sure 0-60 sn olmali."
            assert 1<=pulse<=3, "Role darbe 1-3 sn olmali."
            mes=[]
            for k in range(kol):
                v=int(self.ent_mes[k].get())
                assert 1<=v<=3000, f"Kol {k+1} mesafesi 1-3000 m olmali."
                mes.append(v)
        except ValueError:
            messagebox.showerror("Hata","Sayi girin."); return
        except AssertionError as e:
            messagebox.showerror("Hata",str(e)); return
        # KAVCFG + kol basina MESAFE. Verici her paketi isleyip flash'a yazar;
        # isleme bitmeden gelen paket atilir, bu yuzden aralikli gonderilir.
        seq=[(CMD_KAVCFG,[kol,hmax,lead,pulse,hmin])]
        seq+=[(CMD_MESAFE,[k,(v>>8)&0xFF,v&0xFF]) for k,v in enumerate(mes)]
        self.seq_busy=True; self.btn_kav.config(state="disabled")
        self._send_seq(seq, 0)

    def _send_seq(self, seq, i):
        if i>=len(seq) or not self.conn:
            self.seq_busy=False; self.btn_kav.config(state="normal")
            if i>=len(seq): self.log(f"Verici ayari gonderildi ({len(seq)} paket). Kontrol icin 'Oku'.")
            return
        cmd,content=seq[i]
        if not self.send_packet(cmd, content):
            self.seq_busy=False; self.btn_kav.config(state="normal"); return
        self.root.after(SEQ_GAP_MS, self._send_seq, seq, i+1)

    def read_verici(self):
        if self.send_packet(CMD_CFGRED): self.log("Verici ayari istendi (CFGRED)")

    def set_verici_rtc(self):
        n=datetime.datetime.now()
        content=[n.hour,n.minute,n.second,n.day,n.month,n.year%100,n.isoweekday()]
        if self.send_packet(CMD_RTCSET, content):
            self.log(f"Verici saati gonderildi: {n:%d.%m.%Y %H:%M:%S}")

    def read_panel_rssi(self):
        if self.send_packet(CMD_PRSSI): self.log("Panel RSSI istendi (E22RSS)")

    def read_verici_rssi(self):
        if self.send_packet(CMD_VRSSI): self.log("Verici RSSI istendi (RSSRED)")

    # ---- Verici oncelik kaydi ----
    def read_logs(self):
        self.log_recs = []
        if self._log_request(0): self.log("Verici kayitlari okunuyor (LOGRED)...")

    def _log_request(self, idx):
        if self.log_timer: self.root.after_cancel(self.log_timer)
        self.log_timer = self.root.after(LOG_TIMEOUT, self._log_timeout)
        return self.send_packet(CMD_LOGRED, [idx & 0xFF])

    def _log_timeout(self):
        self.log_timer = None
        self.log(f"Kayit okuma zaman asimi ({len(self.log_recs)} kayit alindi).")
        if self.log_recs: self.show_logs()

    def on_log_chunk(self, total, idx, data):
        if self.log_timer: self.root.after_cancel(self.log_timer); self.log_timer = None
        if total == 0:
            self.log("Kayit yok (kayit kapali ya da hic istek gelmedi)."); return
        for i in range(2):
            rec = data[i*8:(i+1)*8]
            if idx + i < total and rec != [0xFF]*8:
                self.log_recs.append(rec)
        if idx + 2 < total:
            self._log_request(idx + 2)
        else:
            self.log(f"Verici kayitlari okundu: {len(self.log_recs)} kayit")
            self.show_logs()

    @staticmethod
    def decode_log(rec):
        t = rec[0] | (rec[1] << 8) | (rec[2] << 16) | (rec[3] << 24)
        if t == 0:
            tarih, saat = "RTC yok", "-"
        else:
            tarih = f"{(t>>17)&31:02d}.{(t>>22)&15:02d}.20{(t>>26)&63:02d}"
            saat  = f"{(t>>12)&31:02d}:{(t>>6)&63:02d}:{t&63:02d}"
        tip = {v:k for k,v in VEHICLE_TYPES.items()}.get(rec[4], str(rec[4]))
        return tarih, saat, tip, (rec[5] << 8) | rec[6], rec[7] + 1

    def show_logs(self):
        w = tk.Toplevel(self.root); w.title(f"Oncelik kayitlari ({len(self.log_recs)})"); w.geometry("520x400")
        cols = ("No","Tarih","Saat","Arac tipi","Arac ID","Kol")
        fr = ttk.Frame(w); fr.pack(fill="both", expand=True, padx=8, pady=8)
        tv = ttk.Treeview(fr, columns=cols, show="headings")
        for c, wd in zip(cols, (45, 90, 75, 100, 70, 45)):
            tv.heading(c, text=c); tv.column(c, width=wd, anchor="center")
        sb = ttk.Scrollbar(fr, command=tv.yview); tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        rows = [(n+1,) + self.decode_log(r) for n, r in enumerate(self.log_recs)]
        for row in rows: tv.insert("", "end", values=row)
        def save():
            fn = filedialog.asksaveasfilename(parent=w, defaultextension=".csv",
                                              filetypes=[("CSV","*.csv")], initialfile="oncelik_kayitlari.csv")
            if not fn: return
            with open(fn, "w", newline="", encoding="utf-8-sig") as f:
                cw = csv.writer(f, delimiter=";"); cw.writerow(cols); cw.writerows(rows)
            self.log(f"Kayitlar kaydedildi: {fn}")
        ttk.Button(w, text="CSV kaydet", width=12, command=save).pack(pady=(0,8))

    def read_verici_rtc(self):
        self.rtc_src="Verici"
        if self.send_packet(CMD_RTCREAD): self.log("Verici saati istendi (RTCRED)")

    def send_panel(self):
        sel=DATA_SELECT[self.cbo_data.get()]; color=COLORS[self.cbo_color.get()]
        live=1 if self.var_live.get() else 0
        pbyte=(sel&7)|((color&7)<<3)|((live&1)<<7)     # bit6 rezerve
        n=datetime.datetime.now()
        flags=(FLAG_TABLE if self.var_table.get() else 0)|(FLAG_ROUND if self.var_round.get() else 0)|(FLAG_MIN if self.var_min.get() else 0)
        # [6]panel [7..13]RTC [14]bayraklar
        content=[pbyte,n.hour,n.minute,n.second,n.day,n.month,n.year%100,n.isoweekday(),flags]
        if self.send_packet(CMD_PANEL, content): self.log(f"Panel gonderildi: {self.cbo_data.get()} {self.cbo_color.get()}")

    def send_vehicle(self):
        try:
            vid=int(self.ent_vid.get()); assert 0<=vid<=65535
        except (ValueError,AssertionError):
            messagebox.showerror("Hata","ID 0-65535."); return
        vt=VEHICLE_TYPES[self.cbo_vtype.get()]
        if self.send_packet(CMD_VEHICLE, [vt,(vid>>8)&0xFF,vid&0xFF]): self.log(f"Vehicle: {self.cbo_vtype.get()} ID={vid}")

    def show_rssirp(self, r):
        # r = tip idH idL oturum rssi min max adet kabul (CS kontrol edildi)
        tip = {v:k for k,v in VEHICLE_TYPES.items()}.get(r[0], str(r[0]))
        vid = (r[1] << 8) | r[2]
        med, mn, mx = s8(r[4]), s8(r[5]), s8(r[6])
        ok = "KABUL" if r[8] else "-"
        row = (datetime.datetime.now().strftime("%H:%M:%S"), tip, vid, r[3], med, mn, mx, r[7], ok)
        self.tv_rssi.insert("", "end", values=row)
        self.tv_rssi.see(self.tv_rssi.get_children()[-1])
        self.lbl_rssi_sum.config(text=f"Son: {med} dBm (oturum {mn}..{mx}), {r[7]}. paket, {ok}")
        self.lbl_rssi.config(text=f"Son beacon: {med} dBm   oturum min/max: {mn}/{mx}   adet: {r[7]}   {ok}")
        self.log(f"ESP-NOW RSSI: {tip} ID={vid} oturum={r[3]} rssi={med} min={mn} max={mx} paket={r[7]} -> {ok}")

    def show_rssi_ack(self, v):
        self.cbo_rssi.set(str(v))
        self.log(f"ESP32 RSSI esigi onaylandi ve kaydedildi: {v} dBm")

    def save_rssi(self):
        rows=[self.tv_rssi.item(i,"values") for i in self.tv_rssi.get_children()]
        if not rows: self.log("Kaydedilecek RSSI verisi yok."); return
        fn=filedialog.asksaveasfilename(defaultextension=".csv",filetypes=[("CSV","*.csv")],
                                        initialfile="espnow_rssi.csv")
        if not fn: return
        with open(fn,"w",newline="",encoding="utf-8-sig") as f:
            cw=csv.writer(f,delimiter=";")
            cw.writerow(("Saat","Tip","ID","Oturum","RSSI","Min","Max","Adet","Sonuc")); cw.writerows(rows)
        self.log(f"RSSI verisi kaydedildi: {fn}")

    def send_wifi(self):
        # ESP32 WiFi ayari — ayrakli format: "WIFICF:ssid:sifre\n"
        # Bu paket ESP32'de kalir (STM32'ye gitmez).
        ssid=self.ent_ssid.get().strip()
        pwd=self.ent_pass.get().strip()
        if not ssid:
            messagebox.showerror("Hata","SSID bos olamaz."); return
        if len(pwd)<8:
            messagebox.showerror("Hata","Sifre en az 8 karakter (WPA2)."); return
        if ":" in ssid or ":" in pwd:
            messagebox.showerror("Hata","SSID/sifre ':' iceremez (ayrac)."); return
        msg=f"WIFICF:{ssid}:{pwd}\n".encode()
        if not self.conn:
            messagebox.showwarning("Uyari","Once baglanin."); return
        try:
            if self.sock: self.sock.sendall(msg)
            else: self.ser.write(msg)
            self.log(f"WiFi ayari gonderildi: SSID={ssid}")
            messagebox.showinfo("ESP32","Ayar gonderildi. ESP32 yeniden baslayacak.\n"
                                "Baglanti kopacak — yeni SSID/sifre ile tekrar baglanin.")
        except Exception as e:
            messagebox.showerror("Hata",str(e))

    def send_rssi(self):
        # ESP-NOW RSSI esigi: "RSSIES" + [6] esik mutlak degeri (95 -> -95 dBm)
        # ESP32 koprusu bu paketi kendi tutar; sadece WiFi (TCP) baglantisinda ESP32'ye ulasir.
        if not self.conn: messagebox.showwarning("Uyari","Once baglanin."); return
        if not self.sock:
            messagebox.showwarning("Uyari","RSSI esigi sadece WiFi baglantisiyla ESP32'ye gonderilir."); return
        try:
            v=int(self.cbo_rssi.get()); assert -100<=v<=-30
        except (ValueError,AssertionError):
            messagebox.showerror("Hata","Gecersiz RSSI esigi."); return
        if self.send_packet(CMD_RSSIES,[-v]): self.log(f"RSSI esigi gonderildi: {v} dBm")

    # ---------------- ALMA (arka plan thread) ----------------
    def rx_loop(self):
        while self.running and self.conn:
            try:
                if self.sock:
                    try:
                        data=self.sock.recv(256)      # WiFi
                    except socket.timeout:
                        data=b""                       # veri yok, kopma degil
                    else:
                        if not data:                   # baglanti koptu
                            self.root.after(0, self.disconnect); return
                else:
                    data=self.ser.read(256)           # UART (timeout 0.1 sn)
                if not data:
                    # Hat sustu: tamponda kalan yarim veri tamamlanmayacak
                    # (cop ya da kopuk paket). Bastan bir bayt atip tekrar dene;
                    # gecerli bir paket varsa yakalanir, yoksa tampon bosalir.
                    if self.buf:
                        self.buf = self.buf[1:]
                        self.parse_buf()
                    continue
                self.buf += data
                if len(self.buf) > 4096:              # asiri birikme korumasi
                    self.buf = self.buf[-256:]
                self.parse_buf()
            except (OSError, ConnectionResetError):
                self.root.after(0, self.disconnect); return

    def parse_buf(self):
        # Tampondaki mesajlari ayiklar. Iki mesaj tipi var:
        #   "E22RED" + 9 register     = READ_REPLY byte
        #   'D' + LIVE_SIZE byte      = LIVE_FRAME byte (son byte checksum)
        # Canli veri checksum ile dogrulanir: tutmazsa o 'D' gercek paket
        # basi degildir (ya da paket bozuktur); sadece 1 byte atilip
        # senkron yeniden aranir. Boylece veri icindeki 68 ('D') degeri
        # yanlis paket basi sanilmaz.
        while self.buf:
            # --- E22 oku cevabi ---
            if self.buf.startswith(CMD_READ):
                if len(self.buf) < READ_REPLY: return      # tam gelmedi
                regs = list(self.buf[len(CMD_READ):READ_REPLY])
                self.buf = self.buf[READ_REPLY:]
                self.root.after(0, self.show_read, regs)
                continue
            # --- Panel saati cevabi ---
            if self.buf.startswith(CMD_RTCREAD):
                if len(self.buf) < RTC_REPLY: return       # tam gelmedi
                t = list(self.buf[len(CMD_RTCREAD):RTC_REPLY])
                self.buf = self.buf[RTC_REPLY:]
                self.root.after(0, self.show_rtc, t)
                continue
            # --- Verici ayar cevabi ---
            if self.buf.startswith(CMD_CFGRED):
                if len(self.buf) < CFG_REPLY: return       # tam gelmedi
                c = list(self.buf[len(CMD_CFGRED):CFG_REPLY])
                self.buf = self.buf[CFG_REPLY:]
                self.root.after(0, self.show_cfg, c)
                continue
            # --- Panel RSSI cevabi ---
            if self.buf.startswith(CMD_PRSSI):
                if len(self.buf) < PRSSI_REPLY: return
                r = list(self.buf[len(CMD_PRSSI):PRSSI_REPLY])
                self.buf = self.buf[PRSSI_REPLY:]
                self.root.after(0, self.show_prssi, r)
                continue
            # --- Verici RSSI cevabi ---
            if self.buf.startswith(CMD_VRSSI):
                if len(self.buf) < VRSSI_REPLY: return
                r = list(self.buf[len(CMD_VRSSI):VRSSI_REPLY])
                self.buf = self.buf[VRSSI_REPLY:]
                self.root.after(0, self.show_vrssi, r)
                continue
            # --- Verici kayit parcasi ---
            if self.buf.startswith(CMD_LOGRED):
                if len(self.buf) < LOG_REPLY: return
                r = list(self.buf[len(CMD_LOGRED):LOG_REPLY])
                self.buf = self.buf[LOG_REPLY:]
                self.root.after(0, self.on_log_chunk, r[0], r[1], r[2:18])
                continue
            # --- ESP32 kopru: ESP-NOW arac oturumu RSSI raporu ---
            if self.buf.startswith(CMD_RSSIRP):
                if len(self.buf) < RSSIRP_REPLY: return
                r = list(self.buf[len(CMD_RSSIRP):RSSIRP_REPLY])
                if (sum(r[:9]) & 0xFF) == r[9]:
                    self.buf = self.buf[RSSIRP_REPLY:]
                    self.root.after(0, self.show_rssirp, r)
                    continue
                self.buf = self.buf[1:]                    # bozuk rapor: senkron ara
                continue
            # --- ESP32 RSSI esigi onayi (16 byte, [6] = mutlak esik) ---
            if self.buf.startswith(CMD_RSSIES):
                if len(self.buf) < PACKET_SIZE: return
                v = -self.buf[6]
                self.buf = self.buf[PACKET_SIZE:]
                self.root.after(0, self.show_rssi_ack, v)
                continue
            # Cevap kelimesinin basi gelmis ama tamami gelmemis olabilir: bekle
            if len(self.buf) < len(CMD_READ) and any(c.startswith(self.buf) for c in REPLY_CMDS):
                return

            # --- Canli veri ---
            if self.buf[0:1] == b'D':
                if len(self.buf) < LIVE_FRAME: return      # tam gelmedi
                pkt = list(self.buf[1:LIVE_FRAME])
                if (sum(pkt[:16]) & 0xFF) == pkt[16]:    # RSSI checksum disi
                    self.buf = self.buf[LIVE_FRAME:]
                    self.root.after(0, self.show_live, pkt)
                    continue
                # Checksum tutmadi: gercek paket basi degil ya da bozuk
                self.bad_cnt += 1
                self.root.after(0, self.show_bad)

            self.buf = self.buf[1:]                        # tanimsiz bayt at

    def _upd_cnt(self):
        self.lbl_cnt.config(text=f"Paket: {self.pkt_index}   Hatali: {self.bad_cnt}")

    def _live_header(self):
        return f"{'No':>{NO_WIDTH}}" + "".join(
            f"{l:>{w}}" for l, w in zip(LIVE_LABELS, COL_WIDTHS))

    def show_live(self, pkt):
        # pkt = LIVE_SIZE byte: [0]Command(karakter) [1..] decimal (checksum dahil)
        self.pkt_index += 1
        cmd = chr(pkt[0]) if 32<=pkt[0]<127 else str(pkt[0])
        vals = [cmd] + [str(b) for b in pkt[1:17]] + [dbm(pkt[17])]
        line = f"{self.pkt_index:>{NO_WIDTH}}" + "".join(
            f"{v:>{w}}" for v, w in zip(vals, COL_WIDTHS))
        self.mon.insert("end", line + "\n")
        self.mon.see("end")
        self._upd_cnt()

    def show_bad(self):
        self._upd_cnt()

    def show_rtc(self, t):
        hh, mm, ss, dd, mo, yy, wd = t
        gun = ["?","Pzt","Sal","Car","Per","Cum","Cmt","Paz"][wd] if wd <= 7 else "?"
        if hh == 0xFF:
            self.log(f"{self.rtc_src} saati: RTC calismiyor"); return
        self.log(f"{self.rtc_src} saati: {dd:02d}.{mo:02d}.20{yy:02d} {gun} {hh:02d}:{mm:02d}:{ss:02d}")

    def show_prssi(self, r):
        noise, last, pkt, en = r
        self.log(f"Panel RSSI: gurultu={dbm(noise)} son paket={dbm(last)} "
                 f"GLOSA paketi={dbm(pkt)} dBm (RSSI byte {'acik' if en else 'kapali'})")
        if noise == 0 and last == 0:
            self.log("  Gurultu olcumu yok: E22 'Ortam gurultusu' kapali olabilir.")

    def show_vrssi(self, r):
        noise, last = r[0], r[1]; kol = r[2:8]; en = r[8]
        self.log(f"Verici RSSI: gurultu={dbm(noise)} son paket={dbm(last)} dBm "
                 f"(RSSI byte {'acik' if en else 'kapali'})")
        self.log("  Kol istek RSSI: " + "  ".join(f"K{i+1}={dbm(v)}" for i, v in enumerate(kol)))
        if noise == 0 and last == 0:
            self.log("  Gurultu olcumu yok: E22 'Ortam gurultusu' kapali olabilir.")

    def show_cfg(self, c):
        # c = KOL Max Lead Pulse 6x(H,L) Min
        kol,hmax,lead,pulse=c[0:4]; hmin=c[16]
        mes=[(c[4+k*2]<<8)|c[5+k*2] for k in range(6)]
        if 1<=kol<=6: self.cbo_kol.set(str(kol))
        for e,v in ((self.ent_hmax,hmax),(self.ent_hmin,hmin),(self.ent_lead,lead),(self.ent_pulse,pulse)):
            e.delete(0,"end"); e.insert(0,str(v))
        for e,v in zip(self.ent_mes,mes):
            e.config(state="normal"); e.delete(0,"end"); e.insert(0,str(v))
        self._upd_kol()
        self.log(f"Verici OKUNDU: kol={kol} max={hmax} min={hmin} on={lead}sn darbe={pulse}sn "
                 f"mesafe={'/'.join(str(v) for v in mes[:max(1,min(kol,6))])}")

    def show_read(self, regs):
        self.log(f"[show_read] regs={regs}")   # DEBUG: gelen ham 9 register
        addh,addl,netid,reg0,reg1,reg2,reg3,ch_,cl_=regs
        addr=(addh<<8)|addl; ch=reg2&0x7F
        baud=(reg0>>5)&7; par=(reg0>>3)&3; air=reg0&7; sub=(reg1>>6)&3; pw=reg1&3
        self.ent_addr.delete(0,"end"); self.ent_addr.insert(0,str(addr))
        self.ent_netid.delete(0,"end"); self.ent_netid.insert(0,str(netid))
        self.ent_ch.delete(0,"end"); self.ent_ch.insert(0,str(ch)); self.upd_freq()
        if baud in UART_BAUD_R: self.cbo_uart.set(UART_BAUD_R[baud])
        if par in PARITY_R: self.cbo_par.set(PARITY_R[par])
        if air in AIR_RATE_R: self.cbo_air.set(AIR_RATE_R[air])
        if sub in SUB_PACKET_R: self.cbo_sub.set(SUB_PACKET_R[sub])
        if pw in TX_POWER_R: self.cbo_pow.set(TX_POWER_R[pw])
        self.var_rssi.set(bool((reg3>>7)&1)); self.var_fixed.set(bool((reg3>>6)&1))
        self.var_rep.set(bool((reg3>>5)&1)); self.var_lbt.set(bool((reg3>>4)&1))
        self.var_noise.set(bool((reg1>>5)&1))
        self.cbo_wrole.set(WOR_ROLE_R[(reg3>>3)&1]); self.cbo_wcyc.set(WOR_CYCLE_R[reg3&7])
        self.log(f"E22 OKUNDU: adr={addr} net={netid} kanal={ch} "
                 f"LBT={'acik' if (reg3>>4)&1 else 'kapali'}")

    def clear_mon(self):
        self.mon.delete("1.0","end"); self.pkt_index=0; self.bad_cnt=0; self._upd_cnt()

    def _copy_selection(self, event=None):
        try:
            sel = self.mon.get("sel.first", "sel.last")
        except tk.TclError:
            sel = self.mon.get("1.0", "end").strip()   # secim yoksa tumu
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(sel)
        return "break"

    def _select_all_mon(self):
        self.mon.tag_add("sel", "1.0", "end")

    def _show_mon_menu(self, event):
        try: self._mon_menu.tk_popup(event.x_root, event.y_root)
        finally: self._mon_menu.grab_release()


if __name__ == "__main__":
    root=tk.Tk(); ConfigApp(root); root.mainloop()
