package com.fatihtan.glosa

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter

class MainActivity : ComponentActivity() {
    private lateinit var app: Glosa

    override fun onCreate(savedInstanceState: Bundle?) {
        installCrashHandler()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        app = Glosa(this)
        val crash = readCrash()
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize().safeDrawingPadding()) {
                    Screen(app)
                    var report by remember { mutableStateOf(crash) }
                    report?.let { CrashDialog(it) { report = null } }
                }
            }
        }
    }

    override fun onDestroy() {
        if (isFinishing) app.close()
        super.onDestroy()
    }

    // ---- Cokme raporu: uygulama beklenmedik kapanirsa bir sonraki acilista gosterilir ----
    private fun crashFile() = File(filesDir, "crash.txt")

    private fun installCrashHandler() {
        val prev = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try {
                val sw = StringWriter()
                e.printStackTrace(PrintWriter(sw))
                crashFile().writeText("Cihaz: ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE}\n" +
                    "Thread: ${t.name}\n\n$sw")
            } catch (_: Exception) { }
            prev?.uncaughtException(t, e)
        }
    }

    private fun readCrash(): String? {
        val f = crashFile()
        if (!f.exists()) return null
        val s = f.readText()
        f.delete()
        return s
    }
}

// ============================ ORTAK PARCALAR ============================

private val Mono = FontFamily.Monospace

fun copyText(ctx: Context, label: String, text: String) {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
    Toast.makeText(ctx, "Kopyalandi", Toast.LENGTH_SHORT).show()
}

fun shareText(ctx: Context, title: String, text: String) {
    val i = Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(Intent.EXTRA_SUBJECT, title)
        putExtra(Intent.EXTRA_TEXT, text)
    }
    ctx.startActivity(Intent.createChooser(i, title))
}

@Composable
fun CrashDialog(text: String, onClose: () -> Unit) {
    val ctx = LocalContext.current
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Hata raporu") },
        text = {
            Text(text, fontFamily = Mono, fontSize = 10.sp,
                modifier = Modifier.heightIn(max = 400.dp).verticalScroll(rememberScrollState()))
        },
        confirmButton = { TextButton({ copyText(ctx, "Hata raporu", text) }) { Text("Kopyala") } },
        dismissButton = {
            Row {
                TextButton({ shareText(ctx, "GLOSA hata raporu", text) }) { Text("Paylas") }
                TextButton(onClose) { Text("Kapat") }
            }
        },
    )
}

@Composable
fun Field(label: String, value: String, onChange: (String) -> Unit, modifier: Modifier = Modifier,
          number: Boolean = true, enabled: Boolean = true) {
    OutlinedTextField(
        value = value, onValueChange = onChange, label = { Text(label) }, singleLine = true,
        enabled = enabled, modifier = modifier,
        keyboardOptions = KeyboardOptions(keyboardType = if (number) KeyboardType.Number else KeyboardType.Text),
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Choice(label: String, options: List<String>, value: String, onChange: (String) -> Unit,
           modifier: Modifier = Modifier) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(expanded = open, onExpandedChange = { open = it }, modifier = modifier) {
        OutlinedTextField(
            value = value, onValueChange = {}, readOnly = true, singleLine = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(open) },
            modifier = Modifier.menuAnchor().fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            options.forEach { o ->
                DropdownMenuItem(text = { Text(o) }, onClick = { onChange(o); open = false })
            }
        }
    }
}

@Composable
fun Check(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Checkbox(checked, onChange)
        Text(label)
    }
}

@Composable
fun Pair2(content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), content = content)
}

@Composable
fun Buttons(vararg b: Pair<String, () -> Unit>) {
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        b.forEach { (t, f) -> Button(f, Modifier.weight(1f)) { Text(t, maxLines = 1) } }
    }
}

@Composable
fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp, bottom = 4.dp))
    Column(verticalArrangement = Arrangement.spacedBy(4.dp), content = content)
}

// ============================ EKRAN ============================

private val TABS = listOf("E22", "Panel", "Verici", "Arac", "ESP32", "Canli", "Log")

@Composable
fun Screen(app: Glosa) {
    val ctx = LocalContext.current
    var tab by rememberSaveable { mutableIntStateOf(0) }

    app.toast?.let {
        LaunchedEffect(it) {
            Toast.makeText(ctx, it, Toast.LENGTH_LONG).show()
            app.toast = null
        }
    }

    Column(Modifier.fillMaxSize()) {
        // ---- Baglanti ----
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Field("IP", app.ip, { app.ip = it }, Modifier.weight(1.4f), number = false, enabled = !app.connected)
            Field("Port", app.port, { app.port = it }, Modifier.weight(0.8f), enabled = !app.connected)
            Button({ app.toggleConnect() }, enabled = !app.connecting) {
                Text(if (app.connected) "Kapat" else if (app.connecting) "..." else "Baglan")
            }
            Box(Modifier.size(14.dp).background(if (app.connected) Color(0xFF2E7D32) else Color(0xFFC62828), CircleShape))
        }

        ScrollableTabRow(selectedTabIndex = tab, edgePadding = 4.dp) {
            TABS.forEachIndexed { i, t -> Tab(selected = tab == i, onClick = { tab = i }, text = { Text(t) }) }
        }

        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                5 -> LiveTab(app)
                6 -> LogTab(app)
                else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp)) {
                    when (tab) {
                        0 -> E22Tab(app)
                        1 -> PanelTab(app)
                        2 -> VericiTab(app)
                        3 -> VehicleTab(app)
                        4 -> Esp32Tab(app)
                    }
                }
            }
        }

        // Son log satiri her sekmede gorunur
        Text(app.logLines.lastOrNull() ?: "", fontSize = 12.sp, maxLines = 2,
            color = Color(0xFF0066CC), modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp))
    }

    app.logRows?.let { LogsDialog(app, it) }
}

@Composable
fun E22Tab(app: Glosa) {
    Pair2 {
        Field("Adres", app.addr, { app.addr = it }, Modifier.weight(1f))
        Field("NetID", app.netid, { app.netid = it }, Modifier.weight(1f))
    }
    Pair2 {
        Field("Kanal", app.ch, { app.ch = it }, Modifier.weight(1f))
        Text(app.freqText(), color = Color(0xFF0066CC), modifier = Modifier.weight(1f).align(Alignment.CenterVertically))
    }
    Pair2 {
        Choice("UART", P.UART_BAUD.keys.toList(), app.uart, { app.uart = it }, Modifier.weight(1f))
        Choice("Parity", P.PARITY.keys.toList(), app.parity, { app.parity = it }, Modifier.weight(1f))
    }
    Pair2 {
        Choice("Air Rate", P.AIR_RATE.keys.toList(), app.air, { app.air = it }, Modifier.weight(1f))
        Choice("Guc", P.TX_POWER.keys.toList(), app.power, { app.power = it }, Modifier.weight(1f))
    }
    Pair2 {
        Choice("Sub-pkt", P.SUB_PACKET.keys.toList(), app.sub, { app.sub = it }, Modifier.weight(1f))
        Field("Key", app.key, { app.key = it }, Modifier.weight(1f))
    }
    Check("RSSI byte", app.rssiByte) { app.rssiByte = it }
    Check("Fixed mode", app.fixed) { app.fixed = it }
    Check("LBT (gondermeden dinle)", app.lbt) { app.lbt = it }
    Check("Repeater", app.rep) { app.rep = it }
    Check("Ortam gurultusu (RSSI)", app.noise) { app.noise = it }
    Pair2 {
        Choice("WOR rol", P.WOR_ROLE.keys.toList(), app.wrole, { app.wrole = it }, Modifier.weight(1f))
        Choice("WOR sure", P.WOR_CYCLE.keys.toList(), app.wcyc, { app.wcyc = it }, Modifier.weight(1f))
    }
    Buttons("Gonder" to app::sendE22, "Oku" to app::readE22)
}

@Composable
fun PanelTab(app: Glosa) {
    Pair2 {
        Choice("Gosterilecek veri", P.DATA_SELECT.keys.toList(), app.dataSel, { app.dataSel = it }, Modifier.weight(1f))
        Choice("Renk", P.COLORS.keys.toList(), app.color, { app.color = it }, Modifier.weight(1f))
    }
    Section("Veri") {
        Check("Canli veri gonder (her saniye)", app.live) { app.live = it }
        Check("Veri kesilince tablodan oku", app.table) { app.table = it }
    }
    Section("Gosterim") {
        Check("Hizi 5'e yuvarla (20, 25, 30 ...)", app.round) { app.round = it }
        Check("Min hiz aktif (altinda logo goster)", app.minLogo) { app.minLogo = it }
    }
    Buttons("Gonder" to app::sendPanel)
    Buttons("Saati oku" to app::readPanelRtc, "RSSI oku" to app::readPanelRssi)
}

@Composable
fun VericiTab(app: Glosa) {
    Section("Kavsak") {
        Pair2 {
            Choice("Kol sayisi", listOf("1", "2", "3", "4", "5", "6"), app.kol, { app.kol = it }, Modifier.weight(1f))
            Field("Max hiz (km/h)", app.hmax, { app.hmax = it }, Modifier.weight(1f))
        }
        Pair2 {
            Field("Min hiz (km/h)", app.hmin, { app.hmin = it }, Modifier.weight(1f))
            Field("Role on sure (0-60 sn)", app.lead, { app.lead = it }, Modifier.weight(1f))
        }
        Pair2 {
            Field("Role darbe (1-3 sn)", app.pulse, { app.pulse = it }, Modifier.weight(1f))
            Spacer(Modifier.weight(1f))
        }
    }
    Section("Kol mesafesi (m)") {
        val n = app.kol.toIntOrNull() ?: 6
        for (r in 0 until 3) {
            Pair2 {
                for (k in listOf(r * 2, r * 2 + 1)) {
                    Field("Kol ${k + 1}", app.mesafe[k], { app.mesafe[k] = it }, Modifier.weight(1f), enabled = k < n)
                }
            }
        }
    }
    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(app::sendVerici, Modifier.weight(1f), enabled = !app.seqBusy) { Text("Gonder") }
        Button(app::readVerici, Modifier.weight(1f)) { Text("Oku") }
    }
    Buttons("Saati ayarla" to app::setVericiRtc, "Saati oku" to app::readVericiRtc)
    Buttons("RSSI oku" to app::readVericiRssi, "Kayitlari oku" to app::readLogs)
}

@Composable
fun VehicleTab(app: Glosa) {
    val ctx = LocalContext.current
    Pair2 {
        Choice("Arac tipi", P.VEHICLE_TYPES.keys.toList(), app.vtype, { app.vtype = it }, Modifier.weight(1f))
        Field("Arac ID", app.vid, { app.vid = it }, Modifier.weight(1f))
    }
    Buttons("Gonder" to app::sendVehicle)

    // ESP-NOW arac oturumlari: kopru her beacon icin RSSI raporu yollar.
    // Esik (RSSI_ESIK) kopru kodundadir.
    Section("ESP-NOW RSSI (esik ayari icin)") {
        if (app.rssiSummary.isNotEmpty()) Text(app.rssiSummary, color = Color(0xFF0066CC))
        val cols = listOf("Saat", "Tip", "ID", "Otr", "RSSI", "Min", "Max", "Adet", "Sonuc")
        val w = listOf(9, 9, 6, 4, 5, 5, 5, 5, 6)
        val fmt = { row: List<String> -> row.zip(w).joinToString("") { (v, n) -> v.take(n - 1).padEnd(n) } }
        Column(Modifier.horizontalScroll(rememberScrollState())) {
            Text(fmt(cols), fontFamily = Mono, fontSize = 11.sp, color = Color(0xFF0066CC))
            app.rssiRows.takeLast(50).asReversed().forEach { Text(fmt(it), fontFamily = Mono, fontSize = 11.sp) }
        }
        Buttons("Temizle" to { app.rssiRows.clear(); app.rssiSummary = "" },
            "Paylas (CSV)" to { shareText(ctx, "espnow_rssi.csv", app.rssiCsv()) })
    }
}

@Composable
fun Esp32Tab(app: Glosa) {
    Field("WiFi SSID", app.ssid, { app.ssid = it }, Modifier.fillMaxWidth(), number = false)
    Field("Sifre", app.wpass, { app.wpass = it }, Modifier.fillMaxWidth(), number = false)
    Text("Not: Gonderince ESP32 yeni ayarla yeniden baslar. Baglanti kopar, yeni SSID/sifre ile tekrar baglanin.",
        color = Color.Gray, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
    Buttons("Gonder" to app::sendWifi)
}

@Composable
fun LiveTab(app: Glosa) {
    val list = rememberLazyListState()
    LaunchedEffect(app.liveRows.size) {
        if (app.liveRows.isNotEmpty()) list.scrollToItem(app.liveRows.size - 1)
    }
    Column(Modifier.fillMaxSize().padding(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Button({ app.clearLive() }) { Text("Temizle") }
            Text("Paket: ${app.pktIndex}   Hatali: ${app.badCnt}", color = Color(0xFF0066CC),
                modifier = Modifier.padding(start = 12.dp))
        }
        Box(Modifier.fillMaxSize().padding(top = 6.dp).horizontalScroll(rememberScrollState())) {
            Column(Modifier.width(560.dp).fillMaxHeight()) {
                Text(app.liveHeader(), fontFamily = Mono, fontSize = 11.sp, color = Color(0xFF0066CC))
                LazyColumn(Modifier.fillMaxSize(), state = list) {
                    items(app.liveRows) { Text(it, fontFamily = Mono, fontSize = 11.sp) }
                }
            }
        }
    }
}

@Composable
fun LogTab(app: Glosa) {
    val ctx = LocalContext.current
    val list = rememberLazyListState()
    LaunchedEffect(app.logLines.size) {
        if (app.logLines.isNotEmpty()) list.scrollToItem(app.logLines.size - 1)
    }
    Column(Modifier.fillMaxSize().padding(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button({ app.logLines.clear() }) { Text("Temizle") }
            Button({ copyText(ctx, "GLOSA log", app.logLines.joinToString("\n")) }) { Text("Kopyala") }
        }
        LazyColumn(Modifier.fillMaxSize().padding(top = 6.dp), state = list) {
            items(app.logLines) { Text(it, fontFamily = Mono, fontSize = 12.sp) }
        }
    }
}

@Composable
fun LogsDialog(app: Glosa, rows: List<List<String>>) {
    val ctx = LocalContext.current
    val w = listOf(4, 11, 9, 9, 7, 4)
    val fmt = { row: List<String> -> row.zip(w).joinToString("") { (v, n) -> v.take(n - 1).padEnd(n) } }
    AlertDialog(
        onDismissRequest = { app.logRows = null },
        title = { Text("Oncelik kayitlari (${rows.size})") },
        text = {
            Column(Modifier.horizontalScroll(rememberScrollState())) {
                Text(fmt(listOf("No", "Tarih", "Saat", "Tip", "ID", "Kol")), fontFamily = Mono, fontSize = 11.sp,
                    color = Color(0xFF0066CC))
                Column(Modifier.heightIn(max = 400.dp).verticalScroll(rememberScrollState())) {
                    rows.forEach { Text(fmt(it), fontFamily = Mono, fontSize = 11.sp) }
                }
            }
        },
        confirmButton = { TextButton({ shareText(ctx, "oncelik_kayitlari.csv", app.logsCsv()) }) { Text("Paylas (CSV)") } },
        dismissButton = { TextButton({ app.logRows = null }) { Text("Kapat") } },
    )
}
