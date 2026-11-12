package com.fatihtan.glosa

import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.util.Calendar
import java.util.Locale

/**
 * Uygulama durumu ve islemler — glosa_config.py ConfigApp'in karsiligi.
 * Butun durum degisiklikleri ana thread'de yapilir.
 */
class Glosa(ctx: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val link = Link(ctx.applicationContext,
        onMsg = { m -> main.post { onMessage(m) } },
        onState = { c, t -> main.post { connected = c; log(t) } })

    // ---- Baglanti ----
    var ip by mutableStateOf("192.168.4.1")
    var port by mutableStateOf("8080")
    var connected by mutableStateOf(false)
    var connecting by mutableStateOf(false)

    // ---- Uyari (Toast) ----
    var toast by mutableStateOf<String?>(null)

    // ---- Log ----
    val logLines = mutableStateListOf<String>()
    fun log(m: String) {
        connecting = false
        logLines.add(m)
        if (logLines.size > 300) logLines.removeRange(0, logLines.size - 300)
    }

    // ---- E22 ----
    var addr by mutableStateOf("1")
    var netid by mutableStateOf("0")
    var ch by mutableStateOf("18")
    var key by mutableStateOf("0")
    var uart by mutableStateOf("9600")
    var parity by mutableStateOf("8N1")
    var air by mutableStateOf("2.4k")
    var power by mutableStateOf("22dBm")
    var sub by mutableStateOf("240")
    var wrole by mutableStateOf("Alici")
    var wcyc by mutableStateOf("2000ms")
    var rssiByte by mutableStateOf(false)
    var fixed by mutableStateOf(true)
    var lbt by mutableStateOf(false)
    var rep by mutableStateOf(false)
    var noise by mutableStateOf(false)

    fun freqText(): String = ch.toIntOrNull()?.let {
        if (it in 0..80) String.format(Locale.US, "%.3f MHz", 850.125 + it) else "gecersiz"
    } ?: "—"

    // ---- Panel ----
    var dataSel by mutableStateOf("Kol 4")
    var color by mutableStateOf("GREEN")
    var live by mutableStateOf(true)
    var table by mutableStateOf(true)
    var round by mutableStateOf(false)
    var minLogo by mutableStateOf(false)

    // ---- Verici ----
    var kol by mutableStateOf("4")
    var hmax by mutableStateOf("50")
    var hmin by mutableStateOf("30")
    var lead by mutableStateOf("10")
    var pulse by mutableStateOf("1")
    val mesafe = mutableStateListOf("500", "500", "500", "500", "500", "500")
    var seqBusy by mutableStateOf(false)

    // ---- Vehicle ----
    var vtype by mutableStateOf("Ambulans")
    var vid by mutableStateOf("0")
    val rssiRows = mutableStateListOf<List<String>>()
    var rssiSummary by mutableStateOf("")

    // ---- ESP32 ----
    var ssid by mutableStateOf("GLOSA_Panel")
    var wpass by mutableStateOf("glosa1234")
    var rssiEsik by mutableStateOf(P.RSSI_DEFAULT)

    // ---- Canli monitor ----
    val liveRows = mutableStateListOf<String>()
    var pktIndex by mutableIntStateOf(0)
    var badCnt by mutableIntStateOf(0)

    // ---- Verici kayitlari ----
    private val logRecs = mutableListOf<IntArray>()
    var logRows by mutableStateOf<List<List<String>>?>(null)   // null = pencere kapali
    private val logTimeout = Runnable {
        log("Kayit okuma zaman asimi (${logRecs.size} kayit alindi).")
        if (logRecs.isNotEmpty()) showLogs()
    }

    private var rtcSrc = "Panel"

    // ================= BAGLANTI =================
    fun toggleConnect() {
        if (connected) { link.disconnect(); return }
        val p = port.toIntOrNull() ?: run { toast = "Port sayi olmali."; return }
        connecting = true
        link.connect(ip.trim(), p)
    }

    private fun send(cmd: String, content: List<Int> = emptyList()): Boolean {
        if (!connected) { toast = "Once baglanin."; return false }
        return link.send(P.packet(cmd, content))
    }

    // ================= GONDERME =================
    fun sendE22() {
        val a = addr.toIntOrNull(); val n = netid.toIntOrNull()
        val c = ch.toIntOrNull(); val k = key.toIntOrNull()
        if (a == null || n == null || c == null || k == null ||
            a !in 0..65535 || n !in 0..255 || c !in 0..80 || k !in 0..65535) {
            toast = "Gecersiz E22 degeri."; return
        }
        val regs = listOf(
            (a shr 8) and 0xFF, a and 0xFF, n and 0xFF,
            P.buildReg0(uart, parity, air),
            P.buildReg1(sub, noise, power),
            c and 0x7F,
            P.buildReg3(rssiByte, fixed, rep, lbt, wrole, wcyc),
            (k shr 8) and 0xFF, k and 0xFF)
        if (send(P.CMD_E22, regs)) log("E22 gonderildi: kanal=$c")
    }

    fun readE22() { if (send(P.CMD_READ)) log("E22 oku istegi gonderildi (E22RED)") }

    private fun now(): Calendar = Calendar.getInstance()
    private fun isoWeekday(c: Calendar) = (c.get(Calendar.DAY_OF_WEEK) + 5) % 7 + 1   // 1=Pzt .. 7=Paz

    fun sendPanel() {
        val sel = P.DATA_SELECT[dataSel]!!
        val col = P.COLORS[color]!!
        val pbyte = (sel and 7) or ((col and 7) shl 3) or ((if (live) 1 else 0) shl 7)   // bit6 rezerve
        val n = now()
        val flags = (if (table) P.FLAG_TABLE else 0) or (if (round) P.FLAG_ROUND else 0) or
            (if (minLogo) P.FLAG_MIN else 0)
        // [6]panel [7..13]RTC [14]bayraklar
        val content = listOf(pbyte, n.get(Calendar.HOUR_OF_DAY), n.get(Calendar.MINUTE), n.get(Calendar.SECOND),
            n.get(Calendar.DAY_OF_MONTH), n.get(Calendar.MONTH) + 1, n.get(Calendar.YEAR) % 100, isoWeekday(n), flags)
        if (send(P.CMD_PANEL, content)) log("Panel gonderildi: $dataSel $color")
    }

    fun readPanelRtc() { rtcSrc = "Panel"; if (send(P.CMD_RTCREAD)) log("Panel saati istendi (RTCRED)") }
    fun readPanelRssi() { if (send(P.CMD_PRSSI)) log("Panel RSSI istendi (E22RSS)") }

    fun sendVerici() {
        if (!connected) { toast = "Once baglanin."; return }
        if (seqBusy) return
        val k = kol.toIntOrNull() ?: 4
        val mx = hmax.toIntOrNull(); val mn = hmin.toIntOrNull()
        val ld = lead.toIntOrNull(); val pl = pulse.toIntOrNull()
        if (mx == null || mn == null || ld == null || pl == null) { toast = "Sayi girin."; return }
        val err = when {
            mx !in 1..130 -> "Max hiz 1-130 olmali."
            mn !in 1..mx -> "Min hiz 1 ile Max hiz arasinda olmali."
            ld !in 0..60 -> "Role on sure 0-60 sn olmali."
            pl !in 1..3 -> "Role darbe 1-3 sn olmali."
            else -> null
        }
        if (err != null) { toast = err; return }
        val mes = mutableListOf<Int>()
        for (i in 0 until k) {
            val v = mesafe[i].toIntOrNull()
            if (v == null || v !in 1..3000) { toast = "Kol ${i + 1} mesafesi 1-3000 m olmali."; return }
            mes.add(v)
        }
        // KAVCFG + kol basina MESAFE. Verici her paketi isleyip flash'a yazar;
        // isleme bitmeden gelen paket atilir, bu yuzden aralikli gonderilir.
        val seq = mutableListOf(P.CMD_KAVCFG to listOf(k, mx, ld, pl, mn))
        mes.forEachIndexed { i, v -> seq.add(P.CMD_MESAFE to listOf(i, (v shr 8) and 0xFF, v and 0xFF)) }
        seqBusy = true
        sendSeq(seq, 0)
    }

    private fun sendSeq(seq: List<Pair<String, List<Int>>>, i: Int) {
        if (i >= seq.size || !connected) {
            seqBusy = false
            if (i >= seq.size) log("Verici ayari gonderildi (${seq.size} paket). Kontrol icin 'Oku'.")
            return
        }
        val (cmd, content) = seq[i]
        if (!send(cmd, content)) { seqBusy = false; return }
        main.postDelayed({ sendSeq(seq, i + 1) }, P.SEQ_GAP_MS)
    }

    fun readVerici() { if (send(P.CMD_CFGRED)) log("Verici ayari istendi (CFGRED)") }

    fun setVericiRtc() {
        val n = now()
        val content = listOf(n.get(Calendar.HOUR_OF_DAY), n.get(Calendar.MINUTE), n.get(Calendar.SECOND),
            n.get(Calendar.DAY_OF_MONTH), n.get(Calendar.MONTH) + 1, n.get(Calendar.YEAR) % 100, isoWeekday(n))
        if (send(P.CMD_RTCSET, content)) log(String.format(Locale.US, "Verici saati gonderildi: %02d.%02d.%04d %02d:%02d:%02d",
            n.get(Calendar.DAY_OF_MONTH), n.get(Calendar.MONTH) + 1, n.get(Calendar.YEAR),
            n.get(Calendar.HOUR_OF_DAY), n.get(Calendar.MINUTE), n.get(Calendar.SECOND)))
    }

    fun readVericiRtc() { rtcSrc = "Verici"; if (send(P.CMD_RTCREAD)) log("Verici saati istendi (RTCRED)") }
    fun readVericiRssi() { if (send(P.CMD_VRSSI)) log("Verici RSSI istendi (RSSRED)") }

    fun readLogs() {
        logRecs.clear()
        if (logRequest(0)) log("Verici kayitlari okunuyor (LOGRED)...")
    }

    private fun logRequest(idx: Int): Boolean {
        main.removeCallbacks(logTimeout)
        main.postDelayed(logTimeout, P.LOG_TIMEOUT_MS)
        val ok = send(P.CMD_LOGRED, listOf(idx and 0xFF))
        if (!ok) main.removeCallbacks(logTimeout)
        return ok
    }

    fun sendVehicle() {
        val id = vid.toIntOrNull()
        if (id == null || id !in 0..65535) { toast = "ID 0-65535."; return }
        val t = P.VEHICLE_TYPES[vtype]!!
        if (send(P.CMD_VEHICLE, listOf(t, (id shr 8) and 0xFF, id and 0xFF))) log("Vehicle: $vtype ID=$id")
    }

    fun sendWifi() {
        val s = ssid.trim(); val p = wpass.trim()
        val err = when {
            s.isEmpty() -> "SSID bos olamaz."
            p.length < 8 -> "Sifre en az 8 karakter (WPA2)."
            ':' in s || ':' in p -> "SSID/sifre ':' iceremez (ayrac)."
            !connected -> "Once baglanin."
            else -> null
        }
        if (err != null) { toast = err; return }
        // Ayrakli format: "WIFICF:ssid:sifre\n" — ESP32'de kalir, STM32'ye gitmez
        link.send("WIFICF:$s:$p\n".toByteArray())
        log("WiFi ayari gonderildi: SSID=$s")
        toast = "Ayar gonderildi. ESP32 yeniden baslayacak; yeni SSID/sifre ile tekrar baglanin."
    }

    /** ESP-NOW RSSI esigi: "RSSIES" + [6] mutlak deger (95 -> -95 dBm). ESP32'de kalir. */
    fun sendRssiEsik() {
        val v = rssiEsik.toIntOrNull()
        if (v == null || v !in -100..-30) { toast = "Gecersiz RSSI esigi."; return }
        if (send(P.CMD_RSSIES, listOf(-v))) log("RSSI esigi gonderildi: $v dBm")
    }

    // ================= ALMA =================
    private fun onMessage(m: Msg) {
        when (m) {
            is Msg.Live -> showLive(m.pkt)
            is Msg.Bad -> badCnt++
            is Msg.E22Read -> showRead(m.r)
            is Msg.Rtc -> showRtc(m.t)
            is Msg.Cfg -> showCfg(m.c)
            is Msg.PRssi -> showPRssi(m.r)
            is Msg.VRssi -> showVRssi(m.r)
            is Msg.LogChunk -> onLogChunk(m.total, m.idx, m.data)
            is Msg.RssiRep -> showRssiRep(m.r)
            is Msg.RssiAck -> { rssiEsik = m.esik.toString(); log("ESP32 RSSI esigi onaylandi ve kaydedildi: ${m.esik} dBm") }
        }
    }

    fun liveHeader(): String = "No".padStart(P.NO_WIDTH) +
        P.LIVE_LABELS.zip(P.COL_WIDTHS).joinToString("") { (l, w) -> l.padStart(w) }

    private fun showLive(pkt: IntArray) {
        pktIndex++
        val cmd = if (pkt[0] in 32..126) pkt[0].toChar().toString() else pkt[0].toString()
        val vals = listOf(cmd) + (1..16).map { pkt[it].toString() } + P.dbm(pkt[17])
        val line = pktIndex.toString().padStart(P.NO_WIDTH) +
            vals.zip(P.COL_WIDTHS).joinToString("") { (v, w) -> v.padStart(w) }
        liveRows.add(line)
        if (liveRows.size > 500) liveRows.removeRange(0, liveRows.size - 500)
    }

    fun clearLive() { liveRows.clear(); pktIndex = 0; badCnt = 0 }

    private fun showRtc(t: IntArray) {
        val (hh, mm, ss) = Triple(t[0], t[1], t[2])
        if (hh == 0xFF) { log("$rtcSrc saati: RTC calismiyor"); return }
        val gun = listOf("?", "Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz").getOrElse(t[6]) { "?" }
        log(String.format(Locale.US, "%s saati: %02d.%02d.20%02d %s %02d:%02d:%02d",
            rtcSrc, t[3], t[4], t[5], gun, hh, mm, ss))
    }

    private fun showPRssi(r: IntArray) {
        log("Panel RSSI: gurultu=${P.dbm(r[0])} son paket=${P.dbm(r[1])} " +
            "GLOSA paketi=${P.dbm(r[2])} dBm (RSSI byte ${if (r[3] != 0) "acik" else "kapali"})")
        if (r[0] == 0 && r[1] == 0) log("  Gurultu olcumu yok: E22 'Ortam gurultusu' kapali olabilir.")
    }

    private fun showVRssi(r: IntArray) {
        log("Verici RSSI: gurultu=${P.dbm(r[0])} son paket=${P.dbm(r[1])} dBm " +
            "(RSSI byte ${if (r[8] != 0) "acik" else "kapali"})")
        log("  Kol istek RSSI: " + (0 until 6).joinToString("  ") { "K${it + 1}=${P.dbm(r[2 + it])}" })
        if (r[0] == 0 && r[1] == 0) log("  Gurultu olcumu yok: E22 'Ortam gurultusu' kapali olabilir.")
    }

    private fun showCfg(c: IntArray) {
        // c = KOL Max Lead Pulse 6x(H,L) Min
        val k = c[0]
        if (k in 1..6) kol = k.toString()
        hmax = c[1].toString(); lead = c[2].toString(); pulse = c[3].toString(); hmin = c[16].toString()
        val mes = (0 until 6).map { (c[4 + it * 2] shl 8) or c[5 + it * 2] }
        mes.forEachIndexed { i, v -> mesafe[i] = v.toString() }
        log("Verici OKUNDU: kol=$k max=${c[1]} min=${c[16]} on=${c[2]}sn darbe=${c[3]}sn " +
            "mesafe=${mes.take(k.coerceIn(1, 6)).joinToString("/")}")
    }

    private fun showRead(r: IntArray) {
        val a = (r[0] shl 8) or r[1]
        val reg0 = r[3]; val reg1 = r[4]; val reg3 = r[6]
        addr = a.toString(); netid = r[2].toString(); ch = (r[5] and 0x7F).toString()
        P.keyOf(P.UART_BAUD, (reg0 shr 5) and 7)?.let { uart = it }
        P.keyOf(P.PARITY, (reg0 shr 3) and 3)?.let { parity = it }
        P.keyOf(P.AIR_RATE, reg0 and 7)?.let { air = it }
        P.keyOf(P.SUB_PACKET, (reg1 shr 6) and 3)?.let { sub = it }
        P.keyOf(P.TX_POWER, reg1 and 3)?.let { power = it }
        rssiByte = (reg3 shr 7) and 1 == 1; fixed = (reg3 shr 6) and 1 == 1
        rep = (reg3 shr 5) and 1 == 1; lbt = (reg3 shr 4) and 1 == 1
        noise = (reg1 shr 5) and 1 == 1
        P.keyOf(P.WOR_ROLE, (reg3 shr 3) and 1)?.let { wrole = it }
        P.keyOf(P.WOR_CYCLE, reg3 and 7)?.let { wcyc = it }
        log("E22 OKUNDU: adr=$a net=${r[2]} kanal=${r[5] and 0x7F} LBT=${if ((reg3 shr 4) and 1 == 1) "acik" else "kapali"}")
    }

    private fun onLogChunk(total: Int, idx: Int, data: IntArray) {
        main.removeCallbacks(logTimeout)
        if (total == 0) { log("Kayit yok (kayit kapali ya da hic istek gelmedi)."); return }
        for (i in 0 until 2) {
            val rec = data.copyOfRange(i * 8, (i + 1) * 8)
            if (idx + i < total && !rec.all { it == 0xFF }) logRecs.add(rec)
        }
        if (idx + 2 < total) logRequest(idx + 2)
        else { log("Verici kayitlari okundu: ${logRecs.size} kayit"); showLogs() }
    }

    private fun decodeLog(rec: IntArray): List<String> {
        val t = rec[0].toLong() or (rec[1].toLong() shl 8) or (rec[2].toLong() shl 16) or (rec[3].toLong() shl 24)
        val (tarih, saat) = if (t == 0L) "RTC yok" to "-" else
            String.format(Locale.US, "%02d.%02d.20%02d", (t shr 17) and 31, (t shr 22) and 15, (t shr 26) and 63) to
            String.format(Locale.US, "%02d:%02d:%02d", (t shr 12) and 31, (t shr 6) and 63, t and 63)
        val tip = P.keyOf(P.VEHICLE_TYPES, rec[4]) ?: rec[4].toString()
        return listOf(tarih, saat, tip, ((rec[5] shl 8) or rec[6]).toString(), (rec[7] + 1).toString())
    }

    private fun showLogs() {
        logRows = logRecs.mapIndexed { n, r -> listOf((n + 1).toString()) + decodeLog(r) }
    }

    fun logsCsv(): String = buildString {
        append("No;Tarih;Saat;Arac tipi;Arac ID;Kol\n")
        logRows?.forEach { append(it.joinToString(";")).append('\n') }
    }

    private fun showRssiRep(r: IntArray) {
        // r = tip idH idL oturum rssi min max adet kabul (CS kontrol edildi)
        val tip = P.keyOf(P.VEHICLE_TYPES, r[0]) ?: r[0].toString()
        val id = (r[1] shl 8) or r[2]
        val rs = P.s8(r[4]); val mn = P.s8(r[5]); val mx = P.s8(r[6])
        val ok = if (r[8] != 0) "KABUL" else "-"
        val c = now()
        val saat = String.format(Locale.US, "%02d:%02d:%02d",
            c.get(Calendar.HOUR_OF_DAY), c.get(Calendar.MINUTE), c.get(Calendar.SECOND))
        rssiRows.add(listOf(saat, tip, id.toString(), r[3].toString(), rs.toString(),
            mn.toString(), mx.toString(), r[7].toString(), ok))
        if (rssiRows.size > 300) rssiRows.removeRange(0, rssiRows.size - 300)
        rssiSummary = "Son: $rs dBm (oturum $mn..$mx), ${r[7]}. paket, $ok"
        log("ESP-NOW RSSI: $tip ID=$id oturum=${r[3]} rssi=$rs min=$mn max=$mx paket=${r[7]} -> $ok")
    }

    fun rssiCsv(): String = buildString {
        append("Saat;Tip;ID;Oturum;RSSI;Min;Max;Adet;Sonuc\n")
        rssiRows.forEach { append(it.joinToString(";")).append('\n') }
    }

    fun close() { link.disconnect() }
}
