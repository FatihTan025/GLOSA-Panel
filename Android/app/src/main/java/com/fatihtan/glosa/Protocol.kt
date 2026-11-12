package com.fatihtan.glosa

/*
 * GLOSA protokolu — PC/glosa_config.py ile birebir ayni.
 *
 * PC -> Panel (F401):   PANCFG / E22CFG / VEHICL / E22RED / RTCRED / E22RSS (16 byte sabit)
 * PC -> Verici (F051):  KAVCFG / MESAFE / CFGRED / RTCSET / RTCRED / E22CFG / E22RED / RSSRED / LOGRED
 * STM32 -> PC:          'D' + 18 byte canli veri, cevaplar (E22RED, RTCRED, CFGRED, E22RSS, RSSRED, LOGRED)
 * ESP32 kopru -> PC:    "RSSIRP" + 10 byte (ESP-NOW arac beacon RSSI raporu)
 * PC <-> ESP32 kopru:   "RSSIES" + [6] esik (mutlak deger) — ESP-NOW RSSI esigi; kopru NVS'ye
 *                       kaydeder, ayni 16 byte'i onay olarak geri yollar (STM32'ye gitmez)
 */
object P {
    const val PACKET_SIZE = 16
    const val CMD_LEN = 6

    const val CMD_PANEL = "PANCFG"
    const val CMD_E22 = "E22CFG"
    const val CMD_VEHICLE = "VEHICL"
    const val CMD_READ = "E22RED"
    const val CMD_RTCREAD = "RTCRED"
    const val CMD_KAVCFG = "KAVCFG"
    const val CMD_MESAFE = "MESAFE"
    const val CMD_CFGRED = "CFGRED"
    const val CMD_RTCSET = "RTCSET"
    const val CMD_PRSSI = "E22RSS"
    const val CMD_VRSSI = "RSSRED"
    const val CMD_LOGRED = "LOGRED"
    const val CMD_RSSIRP = "RSSIRP"
    const val CMD_RSSIES = "RSSIES"

    val RSSI_VALUES = listOf("-95", "-90", "-85", "-80", "-75", "-70")   // secilebilir esikler (dBm)
    const val RSSI_DEFAULT = "-95"

    const val SEQ_GAP_MS = 250L          // verici: ardisik paketler arasi bekleme (flash yazimi)
    const val LOG_TIMEOUT_MS = 2000L     // kayit parcasi bekleme

    const val LIVE_SIZE = 18             // 17 byte GLOSA paketi + 1 byte RSSI
    const val LIVE_FRAME = LIVE_SIZE + 1 // 'D' dahil

    // Cevap kelimesi -> toplam uzunluk (kelime dahil)
    val REPLY_LEN = linkedMapOf(
        CMD_READ to CMD_LEN + 9,
        CMD_RTCREAD to CMD_LEN + 7,
        CMD_CFGRED to CMD_LEN + 17,
        CMD_PRSSI to CMD_LEN + 4,
        CMD_VRSSI to CMD_LEN + 9,
        CMD_LOGRED to CMD_LEN + 18,
        CMD_RSSIRP to CMD_LEN + 10,
        CMD_RSSIES to CMD_LEN + 10,
    )

    // ---- E22 register bit haritalari ----
    val UART_BAUD = linkedMapOf("1200" to 0, "2400" to 1, "4800" to 2, "9600" to 3,
        "19200" to 4, "38400" to 5, "57600" to 6, "115200" to 7)
    val AIR_RATE = linkedMapOf("0.3k" to 0, "1.2k" to 1, "2.4k" to 2, "4.8k" to 3,
        "9.6k" to 4, "19.2k" to 5, "38.4k" to 6, "62.5k" to 7)
    val PARITY = linkedMapOf("8N1" to 0, "8O1" to 1, "8E1" to 2)
    val SUB_PACKET = linkedMapOf("240" to 0, "128" to 1, "64" to 2, "32" to 3)
    val TX_POWER = linkedMapOf("22dBm" to 0, "17dBm" to 1, "13dBm" to 2, "10dBm" to 3)
    val WOR_ROLE = linkedMapOf("Alici" to 0, "Verici" to 1)
    val WOR_CYCLE = linkedMapOf("500ms" to 0, "1000ms" to 1, "1500ms" to 2, "2000ms" to 3,
        "2500ms" to 4, "3000ms" to 5, "3500ms" to 6, "4000ms" to 7)
    val COLORS = linkedMapOf("BLACK" to 0, "RED" to 1, "GREEN" to 2, "YELLOW" to 3,
        "BLUE" to 4, "MAGENTA" to 5, "CYAN" to 6, "WHITE" to 7)
    val VEHICLE_TYPES = linkedMapOf("Ambulans" to 1, "Polis" to 2, "Askeri" to 3,
        "Burokrat" to 4, "Itfaiye" to 5)
    val DATA_SELECT = linkedMapOf("Kol 1" to 0, "Kol 2" to 1, "Kol 3" to 2, "Kol 4" to 3,
        "Kol 5" to 4, "Kol 6" to 5, "Saniye" to 6, "Period" to 7)

    // PANCFG [14] bayraklari — STM32 GLOSA_Config.h ile ayni
    const val FLAG_TABLE = 0x01
    const val FLAG_ROUND = 0x02
    const val FLAG_MIN = 0x04

    // Canli monitor sutunlari
    val LIVE_LABELS = listOf("Cmd", "K1", "K2", "K3", "K4", "K5", "K6", "Sn", "Per",
        "Onay", "Max", "Min", "Role", "R1", "R2", "R3", "CS", "RSSI")
    val COL_WIDTHS = LIVE_LABELS.mapIndexed { i, l ->
        if (i == LIVE_LABELS.size - 1) 5 else maxOf(l.length, 3) + 1
    }
    const val NO_WIDTH = 5

    fun <K, V> keyOf(m: Map<K, V>, v: V): K? = m.entries.firstOrNull { it.value == v }?.key

    fun buildReg0(baud: String, par: String, air: String) =
        (UART_BAUD[baud]!! shl 5) or (PARITY[par]!! shl 3) or AIR_RATE[air]!!

    fun buildReg1(sub: String, noise: Boolean, pow: String) =
        (SUB_PACKET[sub]!! shl 6) or ((if (noise) 1 else 0) shl 5) or TX_POWER[pow]!!

    // REG3: bit7 RSSI byte, bit6 fixed, bit5 repeater, bit4 LBT, bit3 WOR rolu, bit2-0 WOR periyodu
    fun buildReg3(rssi: Boolean, fixed: Boolean, rep: Boolean, lbt: Boolean, role: String, cyc: String) =
        ((if (rssi) 1 else 0) shl 7) or ((if (fixed) 1 else 0) shl 6) or ((if (rep) 1 else 0) shl 5) or
            ((if (lbt) 1 else 0) shl 4) or ((WOR_ROLE[role]!! and 1) shl 3) or (WOR_CYCLE[cyc]!! and 7)

    /** 6 harfli komut + icerik, 16 byte'a sifirla tamamlanir. */
    fun packet(cmd: String, content: List<Int> = emptyList()): ByteArray {
        val p = ByteArray(PACKET_SIZE)
        cmd.toByteArray(Charsets.US_ASCII).copyInto(p, 0, 0, CMD_LEN)
        content.take(PACKET_SIZE - CMD_LEN).forEachIndexed { i, v -> p[CMD_LEN + i] = v.toByte() }
        return p
    }

    /** E22 RSSI byte'i -> dBm metni (0 = olcum yok). */
    fun dbm(v: Int): String = if (v == 0) "-" else (-(256 - v)).toString()

    /** Isaretli byte -> int (ESP-NOW RSSI, dBm). */
    fun s8(v: Int): Int = if (v > 127) v - 256 else v
}

/** Gelen mesajlar (parse sonucu). */
sealed class Msg {
    class E22Read(val r: IntArray) : Msg()
    class Rtc(val t: IntArray) : Msg()
    class Cfg(val c: IntArray) : Msg()
    class PRssi(val r: IntArray) : Msg()
    class VRssi(val r: IntArray) : Msg()
    class LogChunk(val total: Int, val idx: Int, val data: IntArray) : Msg()
    class RssiRep(val r: IntArray) : Msg()
    class RssiAck(val esik: Int) : Msg()
    class Live(val pkt: IntArray) : Msg()
    object Bad : Msg()
}

/**
 * Gelen veri tamponu ve ayiklayici — glosa_config.py parse_buf ile ayni mantik.
 * Sadece okuma thread'i kullanir.
 */
class Parser(private val onMsg: (Msg) -> Unit) {
    private var buf = ByteArray(0)

    fun feed(data: ByteArray, n: Int) {
        buf += data.copyOf(n)
        if (buf.size > 4096) buf = buf.copyOfRange(buf.size - 256, buf.size)
        parse()
    }

    /** Hat sustu: tamponda kalan yarim veri tamamlanmayacak, bir byte at ve tekrar dene. */
    fun idle() {
        if (buf.isNotEmpty()) {
            buf = buf.copyOfRange(1, buf.size)
            parse()
        }
    }

    private fun startsWith(w: String): Boolean {
        if (buf.size < w.length) return false
        for (i in w.indices) if (buf[i] != w[i].code.toByte()) return false
        return true
    }

    private fun u(i: Int) = buf[i].toInt() and 0xFF

    private fun take(from: Int, to: Int) = IntArray(to - from) { u(from + it) }

    private fun drop(n: Int) { buf = buf.copyOfRange(n, buf.size) }

    private fun parse() {
        while (buf.isNotEmpty()) {
            // --- Cevaplar ---
            var handled = false
            var wait = false
            for ((w, len) in P.REPLY_LEN) {
                if (!startsWith(w)) continue
                if (buf.size < len) { wait = true; break }
                val r = take(w.length, len)
                when (w) {
                    P.CMD_RSSIRP -> {
                        if ((r.take(9).sum() and 0xFF) != r[9]) { drop(1); handled = true; break }
                        onMsg(Msg.RssiRep(r))
                    }
                    P.CMD_READ -> onMsg(Msg.E22Read(r))
                    P.CMD_RTCREAD -> onMsg(Msg.Rtc(r))
                    P.CMD_CFGRED -> onMsg(Msg.Cfg(r))
                    P.CMD_PRSSI -> onMsg(Msg.PRssi(r))
                    P.CMD_VRSSI -> onMsg(Msg.VRssi(r))
                    P.CMD_LOGRED -> onMsg(Msg.LogChunk(r[0], r[1], r.copyOfRange(2, 18)))
                    P.CMD_RSSIES -> onMsg(Msg.RssiAck(-r[0]))
                }
                drop(len)
                handled = true
                break
            }
            if (wait) return
            if (handled) continue

            // Cevap kelimesinin basi gelmis ama tamami gelmemis olabilir: bekle
            if (buf.size < P.CMD_LEN && P.REPLY_LEN.keys.any { w ->
                    buf.indices.all { buf[it] == w[it].code.toByte() } }) return

            // --- Canli veri ---
            if (buf[0] == 'D'.code.toByte()) {
                if (buf.size < P.LIVE_FRAME) return
                val pkt = take(1, P.LIVE_FRAME)
                if ((pkt.take(16).sum() and 0xFF) == pkt[16]) {   // RSSI checksum disi
                    drop(P.LIVE_FRAME)
                    onMsg(Msg.Live(pkt))
                    continue
                }
                onMsg(Msg.Bad)                                    // checksum tutmadi
            }
            drop(1)                                               // tanimsiz byte at
        }
    }
}
