package com.example.lanlink

import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicLong

class MainActivity : AppCompatActivity() {

    private data class HttpResult(val statusCode: Int, val body: String)

    private lateinit var hostInput: EditText
    private lateinit var codeInput: EditText
    private lateinit var messageInput: EditText
    private lateinit var connectButton: Button
    private lateinit var disconnectButton: Button
    private lateinit var sendButton: Button
    private lateinit var clearButton: Button
    private lateinit var statusText: TextView
    private lateinit var emptyText: TextView
    private lateinit var messageScroll: ScrollView
    private lateinit var messagesContainer: LinearLayout

    private val networkExecutor: ExecutorService = Executors.newFixedThreadPool(2)
    private var pollFuture: Future<*>? = null
    private val connectionGeneration = AtomicLong(0L)

    @Volatile
    private var polling = false

    @Volatile
    private var connected = false

    @Volatile
    private var connecting = false

    private var baseUrl = ""
    private var sessionToken = ""
    private var serverName = "Laptop"
    private var lastMessageId = 0L
    private val messageLock = Any()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        hostInput = findViewById(R.id.hostInput)
        codeInput = findViewById(R.id.codeInput)
        messageInput = findViewById(R.id.messageInput)
        connectButton = findViewById(R.id.connectButton)
        disconnectButton = findViewById(R.id.disconnectButton)
        sendButton = findViewById(R.id.sendButton)
        clearButton = findViewById(R.id.clearButton)
        statusText = findViewById(R.id.statusText)
        emptyText = findViewById(R.id.emptyText)
        messageScroll = findViewById(R.id.messageScroll)
        messagesContainer = findViewById(R.id.messagesContainer)

        val preferences = getSharedPreferences(PREFERENCES_NAME, MODE_PRIVATE)
        hostInput.setText(preferences.getString(KEY_HOST, ""))
        codeInput.setText(preferences.getString(KEY_CODE, ""))

        connectButton.setOnClickListener { connectToLaptop() }
        disconnectButton.setOnClickListener { disconnect() }
        sendButton.setOnClickListener { sendMessage() }
        clearButton.setOnClickListener { clearLocalMessages() }

        updateButtons()
    }

    private fun connectToLaptop() {
        if (connected || connecting) return

        val host = normalizeBaseUrl(hostInput.text.toString())
        val code = codeInput.text.toString().trim()
        if (host.isEmpty()) {
            showToast(getString(R.string.host_required))
            return
        }
        if (code.length != 6) {
            showToast(getString(R.string.code_required))
            return
        }

        baseUrl = host
        val generation = connectionGeneration.incrementAndGet()
        connecting = true
        setStatus(getString(R.string.status_connecting))
        updateButtons()

        networkExecutor.execute connectTask@{
            try {
                val infoResult = request("GET", "$host/api/info")
                requireSuccess(infoResult)
                val info = JSONObject(infoResult.body)
                serverName = info.optString("name", "Laptop")

                val pairBody = JSONObject().put("code", code)
                val pairResult = request("POST", "$host/api/pair", pairBody)
                requireSuccess(pairResult)
                if (generation != connectionGeneration.get()) return@connectTask
                val pair = JSONObject(pairResult.body)
                sessionToken = pair.getString("token")
                serverName = pair.optString("name", serverName)
                connected = true
                connecting = false
                synchronized(messageLock) { lastMessageId = 0L }

                getSharedPreferences(PREFERENCES_NAME, MODE_PRIVATE)
                    .edit()
                    .putString(KEY_HOST, baseUrl)
                    .putString(KEY_CODE, code)
                    .apply()

                runOnUiThread {
                    setStatus("Đã kết nối với $serverName")
                    updateButtons()
                }
                startPolling(generation, host, sessionToken)
            } catch (error: Exception) {
                if (generation != connectionGeneration.get()) return@connectTask
                connected = false
                connecting = false
                sessionToken = ""
                runOnUiThread {
                    setStatus("Kết nối thất bại")
                    updateButtons()
                    showToast(messageFor(error))
                }
            }
        }
    }

    private fun startPolling(generation: Long, host: String, tokenValue: String) {
        polling = true
        pollFuture?.cancel(true)
        pollFuture = networkExecutor.submit {
            while (
                polling &&
                generation == connectionGeneration.get() &&
                !Thread.currentThread().isInterrupted
            ) {
                try {
                    val since = synchronized(messageLock) { lastMessageId }
                    val token = URLEncoder.encode(tokenValue, StandardCharsets.UTF_8.name())
                    val result = request(
                        "GET",
                        "$host/api/messages?token=$token&since=$since"
                    )
                    if (result.statusCode == 401) {
                        runOnUiThread {
                            if (generation == connectionGeneration.get()) {
                                disconnect()
                                setStatus("Phiên ghép nối đã hết hạn, vui lòng ghép nối lại")
                                showToast("Phiên đã hết hạn. Vui lòng nhập mã ghép nối mới.")
                            }
                        }
                        break
                    }
                    requireSuccess(result)
                    appendMessages(JSONObject(result.body).optJSONArray("messages"))
                    runOnUiThread {
                        if (connected) setStatus("Đã kết nối với $serverName")
                    }
                    Thread.sleep(POLL_INTERVAL_MS)
                } catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    break
                } catch (error: Exception) {
                    if (!polling) break
                    runOnUiThread { setStatus("Mất kết nối, đang thử lại...") }
                    try {
                        Thread.sleep(RETRY_INTERVAL_MS)
                    } catch (_: InterruptedException) {
                        Thread.currentThread().interrupt()
                        break
                    }
                    if (error is IOException) continue
                }
            }
        }
    }

    private fun sendMessage() {
        if (!connected) {
            showToast(getString(R.string.not_connected))
            return
        }
        val text = messageInput.text.toString().trim()
        if (text.isEmpty()) return

        sendButton.isEnabled = false
        val host = baseUrl
        val token = sessionToken
        val generation = connectionGeneration.get()
        networkExecutor.execute sendTask@{
            try {
                val body = JSONObject()
                    .put("token", token)
                    .put("text", text)
                val result = request("POST", "$host/api/messages", body)
                if (result.statusCode == 401) {
                    runOnUiThread {
                        if (generation == connectionGeneration.get()) {
                            disconnect()
                            setStatus("Phiên ghép nối đã hết hạn, vui lòng ghép nối lại")
                            showToast("Phiên đã hết hạn. Vui lòng nhập mã ghép nối mới.")
                        }
                    }
                    return@sendTask
                }
                requireSuccess(result)
                if (!connected || generation != connectionGeneration.get()) return@sendTask
                val message = JSONObject(result.body).getJSONObject("message")
                acceptMessage(message)
                runOnUiThread {
                    messageInput.text.clear()
                    setStatus("Đã gửi tới $serverName")
                }
            } catch (error: Exception) {
                runOnUiThread { showToast(messageFor(error)) }
            } finally {
                runOnUiThread { updateButtons() }
            }
        }
    }

    private fun appendMessages(messages: JSONArray?) {
        if (messages == null) return
        for (index in 0 until messages.length()) {
            acceptMessage(messages.optJSONObject(index) ?: continue)
        }
    }

    private fun acceptMessage(message: JSONObject) {
        val id = message.optLong("id", -1L)
        if (id < 0L) return
        synchronized(messageLock) {
            if (id <= lastMessageId) return
            lastMessageId = id
        }
        runOnUiThread { addMessageView(message) }
    }

    private fun addMessageView(message: JSONObject) {
        emptyText.visibility = View.GONE
        val sender = message.optString("sender", "phone")
        val label = if (sender == "laptop") "Laptop" else "Điện thoại"
        val timestamp = message.optString("timestamp", "")
        val text = message.optString("text", "")

        val bubble = TextView(this).apply {
            this.text = getString(R.string.message_bubble, label, timestamp, text)
            textSize = 14f
            maxWidth = (resources.displayMetrics.widthPixels * 0.82f).toInt()
            setTextColor(Color.rgb(23, 33, 27))
            setPadding(dp(13), dp(10), dp(13), dp(10))
            background = GradientDrawable().apply {
                setColor(
                    if (sender == "laptop") Color.rgb(255, 225, 210)
                    else Color.rgb(238, 242, 237)
                )
                cornerRadius = dp(16).toFloat()
            }
        }
        val layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        ).apply {
            topMargin = dp(7)
            gravity = if (sender == "laptop") Gravity.END else Gravity.START
        }
        messagesContainer.addView(bubble, layoutParams)
        messageScroll.post { messageScroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun clearLocalMessages() {
        val count = messagesContainer.childCount - 1
        if (count > 0) messagesContainer.removeViews(1, count)
        emptyText.visibility = View.VISIBLE
    }

    private fun disconnect() {
        connectionGeneration.incrementAndGet()
        polling = false
        pollFuture?.cancel(true)
        pollFuture = null
        connected = false
        connecting = false
        sessionToken = ""
        setStatus(getString(R.string.status_idle))
        updateButtons()
    }

    private fun updateButtons() {
        connectButton.isEnabled = !connected && !connecting
        disconnectButton.isEnabled = connected || connecting
        sendButton.isEnabled = connected
        hostInput.isEnabled = !connected && !connecting
        codeInput.isEnabled = !connected && !connecting
    }

    private fun setStatus(text: String) {
        statusText.text = text
    }

    private fun normalizeBaseUrl(value: String): String {
        var normalized = value.trim()
        if (normalized.isEmpty()) return ""
        if (!normalized.startsWith("http://", ignoreCase = true) &&
            !normalized.startsWith("https://", ignoreCase = true)
        ) {
            normalized = "http://$normalized"
        }
        return normalized.trimEnd('/')
    }

    @Throws(IOException::class)
    private fun request(method: String, url: String, body: JSONObject? = null): HttpResult {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = REQUEST_TIMEOUT_MS
            readTimeout = REQUEST_TIMEOUT_MS
            useCaches = false
            doInput = true
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Connection", "close")
        }

        try {
            if (body != null) {
                val encoded = body.toString().toByteArray(StandardCharsets.UTF_8)
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.setFixedLengthStreamingMode(encoded.size)
                connection.outputStream.use { it.write(encoded) }
            }

            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            val responseBody = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }
                .orEmpty()
            return HttpResult(status, responseBody)
        } finally {
            connection.disconnect()
        }
    }

    private fun requireSuccess(result: HttpResult) {
        if (result.statusCode !in 200..299) {
            var message = "HTTP ${result.statusCode}"
            try {
                message = JSONObject(result.body).optString("error", message)
            } catch (_: Exception) {
                // Keep the HTTP status when the server did not return JSON.
            }
            throw IOException(message)
        }
    }

    private fun messageFor(error: Exception): String {
        val message = error.message.orEmpty()
        return if (message.isBlank()) "Không thể kết nối tới laptop." else message
    }

    private fun showToast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    override fun onDestroy() {
        disconnect()
        networkExecutor.shutdownNow()
        super.onDestroy()
    }

    companion object {
        private const val PREFERENCES_NAME = "lanlink_preferences"
        private const val KEY_HOST = "host"
        private const val KEY_CODE = "pair_code"
        private const val REQUEST_TIMEOUT_MS = 5_000
        private const val POLL_INTERVAL_MS = 1_000L
        private const val RETRY_INTERVAL_MS = 1_500L
    }
}
