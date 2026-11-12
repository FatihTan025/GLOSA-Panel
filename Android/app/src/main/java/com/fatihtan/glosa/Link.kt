package com.fatihtan.glosa

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.Executors

/**
 * ESP32 kopruye TCP baglantisi.
 *
 * Kopru WiFi'sinde internet yok; Android bu durumda trafigi mobil veriden
 * gondermeye calisabilir. Soket, WiFi agina ait socketFactory ile acilir,
 * boylece baglanti her zaman kopru agi uzerinden kurulur.
 *
 * Geri cagirmalar okuma/gonderme thread'lerinden gelir; arayuz tarafi
 * ana thread'e kendisi tasir.
 */
class Link(
    private val ctx: Context,
    private val onMsg: (Msg) -> Unit,
    private val onState: (connected: Boolean, text: String) -> Unit,
) {
    @Volatile private var sock: Socket? = null
    private val sendExec = Executors.newSingleThreadExecutor()

    val isConnected: Boolean get() = sock != null

    private fun wifiNetwork(): Network? {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        @Suppress("DEPRECATION")
        return cm.allNetworks.firstOrNull {
            cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }
    }

    fun connect(ip: String, port: Int) {
        Thread {
            try {
                val net = wifiNetwork()
                val s = net?.socketFactory?.createSocket() ?: Socket()
                s.connect(InetSocketAddress(ip, port), 5000)
                s.soTimeout = 1000
                s.tcpNoDelay = true
                sock = s
                onState(true, "WiFi baglandi: $ip:$port" + if (net == null) " (WiFi agi bulunamadi)" else "")
                readLoop(s)
            } catch (e: Exception) {
                onState(false, "Baglanti hatasi: ${e.message}")
            }
        }.start()
    }

    private fun readLoop(s: Socket) {
        val parser = Parser(onMsg)
        val inp = s.getInputStream()
        val buf = ByteArray(256)
        while (sock === s) {
            try {
                val n = inp.read(buf)
                if (n < 0) break                    // karsi taraf kapatti
                if (n > 0) parser.feed(buf, n)
            } catch (e: SocketTimeoutException) {
                parser.idle()                       // veri yok, kopma degil
            } catch (e: IOException) {
                break
            }
        }
        if (sock === s) {
            close()
            onState(false, "Baglanti koptu.")
        }
    }

    /** Gonderir; bagli degilse false. Yazma ayri thread'de yapilir. */
    fun send(data: ByteArray): Boolean {
        val s = sock ?: return false
        sendExec.execute {
            try {
                s.getOutputStream().apply { write(data); flush() }
            } catch (e: IOException) {
                if (sock === s) {
                    close()
                    onState(false, "Gonderme hatasi: ${e.message}")
                }
            }
        }
        return true
    }

    private fun close() {
        val s = sock
        sock = null
        try { s?.close() } catch (_: IOException) { }
    }

    fun disconnect() {
        if (sock == null) return
        close()
        onState(false, "Baglanti kapatildi.")
    }
}
