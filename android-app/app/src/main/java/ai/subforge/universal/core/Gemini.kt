package ai.subforge.universal.core

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class GeminiException(message: String) : IOException(message)

/**
 * Talks to Google AI Studio (Gemini) directly with the user's own free API key.
 * Two jobs: audio clip -> timed subtitles, and subtitle text -> translated text.
 */
class Gemini(private val apiKey: String, private val model: String) {

    @Volatile var cancelled = false
    private var lastCallAt = 0L

    /** Transcribes one audio clip. Returned times are relative to the clip start. */
    fun transcribe(clip: File, targetLanguage: String?): List<Cue> {
        val audioB64 = Base64.encodeToString(clip.readBytes(), Base64.NO_WRAP)
        val languageRule = if (targetLanguage == null) {
            "Write every line in the language that is actually spoken. Do not translate."
        } else {
            "Translate every line into natural, fluent $targetLanguage. " +
                "If a line is already in $targetLanguage, keep it as spoken."
        }
        val prompt = """
            You are a professional film subtitler. Listen to this audio clip from a video and
            produce subtitles for all spoken dialogue.
            $languageRule
            Rules:
            - "start" and "end" are numbers of seconds from the beginning of THIS clip (e.g. 12.4).
            - Each subtitle is 1 to 7 seconds long and at most 2 short lines (about 42 characters per line).
            - Subtitles must not overlap and must follow the order of speech.
            - Ignore music, sound effects and background noise. Do not describe sounds.
            - Never invent dialogue. If there is no speech, return an empty array [].
        """.trimIndent()

        val parts = JSONArray()
            .put(JSONObject().put("inline_data", JSONObject().put("mime_type", "audio/mp3").put("data", audioB64)))
            .put(JSONObject().put("text", prompt))
        val schema = arraySchema(
            JSONObject()
                .put("start", JSONObject().put("type", "NUMBER"))
                .put("end", JSONObject().put("type", "NUMBER"))
                .put("text", JSONObject().put("type", "STRING")),
            JSONArray().put("start").put("end").put("text"),
        )
        val arr = JSONArray(cleanJson(generate(parts, schema)))
        val cues = ArrayList<Cue>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val text = o.optString("text").trim()
            val start = timeOf(o.opt("start")) ?: continue
            var end = timeOf(o.opt("end")) ?: (start + 2.0)
            if (end <= start) end = start + 2.0
            if (text.isNotEmpty()) cues.add(Cue(start, end, text))
        }
        return cues
    }

    /** Translates the text of the given lines, keeping their order and count. */
    fun translate(lines: List<String>, targetLanguage: String): List<String> {
        val input = JSONArray()
        lines.forEachIndexed { i, t -> input.put(JSONObject().put("i", i).put("t", t)) }
        val prompt = """
            You are a professional film subtitle translator. Translate the "t" field of every item
            into natural, fluent $targetLanguage, keeping the meaning, tone and names.
            Keep each translation short enough to read as a subtitle (at most 2 lines).
            Return exactly one item for every input item, with the same "i".
            Input:
            $input
        """.trimIndent()
        val parts = JSONArray().put(JSONObject().put("text", prompt))
        val schema = arraySchema(
            JSONObject()
                .put("i", JSONObject().put("type", "INTEGER"))
                .put("t", JSONObject().put("type", "STRING")),
            JSONArray().put("i").put("t"),
        )
        val arr = JSONArray(cleanJson(generate(parts, schema)))
        val result = ArrayList(lines) // anything missing stays in the original language
        for (k in 0 until arr.length()) {
            val o = arr.optJSONObject(k) ?: continue
            val i = o.optInt("i", -1)
            val t = o.optString("t").trim()
            if (i in result.indices && t.isNotEmpty()) result[i] = t
        }
        return result
    }

    /** Checks the key and model with a tiny request. Returns null if OK, else the error text. */
    fun test(): String? = try {
        generate(JSONArray().put(JSONObject().put("text", "Reply with the word OK.")), null)
        null
    } catch (e: Exception) {
        e.message ?: e.toString()
    }

    // ------------------------------------------------------------------------------------

    private fun arraySchema(properties: JSONObject, required: JSONArray): JSONObject =
        JSONObject().put("type", "ARRAY").put(
            "items",
            JSONObject().put("type", "OBJECT").put("properties", properties).put("required", required),
        )

    /** Sends one request, retrying on rate limits and server errors. Returns the model's text. */
    private fun generate(parts: JSONArray, schema: JSONObject?): String {
        val config = JSONObject().put("temperature", 0.2)
        if (schema != null) {
            config.put("responseMimeType", "application/json").put("responseSchema", schema)
        }
        val body = JSONObject()
            .put("contents", JSONArray().put(JSONObject().put("role", "user").put("parts", parts)))
            .put("generationConfig", config)
            .toString()
            .toByteArray(Charsets.UTF_8)

        val url = URL(
            "https://generativelanguage.googleapis.com/v1beta/models/" +
                URLEncoder.encode(model, "UTF-8") + ":generateContent"
        )

        var lastError = "unknown error"
        for (attempt in 1..MAX_ATTEMPTS) {
            if (cancelled) throw GeminiException("Cancelled")
            pace()
            val conn = url.openConnection() as HttpURLConnection
            try {
                conn.requestMethod = "POST"
                conn.connectTimeout = 30_000
                conn.readTimeout = 300_000
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.setRequestProperty("x-goog-api-key", apiKey)
                conn.outputStream.use { it.write(body) }
                val code = conn.responseCode
                val stream = if (code in 200..299) conn.inputStream else conn.errorStream
                val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
                if (code in 200..299) return extractText(text)

                lastError = "HTTP $code: " + errorMessage(text)
                val retryable = code == 429 || code == 500 || code == 502 || code == 503 || code == 504
                if (!retryable) throw GeminiException(lastError)
            } catch (e: GeminiException) {
                throw e
            } catch (e: IOException) {
                lastError = "Network error: ${e.message}"
            } finally {
                conn.disconnect()
            }
            if (attempt < MAX_ATTEMPTS) sleepChecked(RETRY_WAIT_MS * attempt)
        }
        throw GeminiException("Gemini failed after $MAX_ATTEMPTS tries. Last error: $lastError")
    }

    private fun extractText(responseJson: String): String {
        val root = JSONObject(responseJson)
        val candidates = root.optJSONArray("candidates")
        if (candidates == null || candidates.length() == 0) {
            val reason = root.optJSONObject("promptFeedback")?.optString("blockReason") ?: ""
            throw GeminiException("Gemini returned no answer. $reason".trim())
        }
        val cand = candidates.getJSONObject(0)
        val parts = cand.optJSONObject("content")?.optJSONArray("parts")
        val sb = StringBuilder()
        if (parts != null) {
            for (i in 0 until parts.length()) {
                val p = parts.getJSONObject(i)
                if (p.optBoolean("thought", false)) continue
                sb.append(p.optString("text"))
            }
        }
        if (sb.isBlank()) {
            throw GeminiException("Gemini returned an empty answer (finishReason=${cand.optString("finishReason")})")
        }
        return sb.toString()
    }

    private fun errorMessage(body: String): String = try {
        JSONObject(body).getJSONObject("error").optString("message", body)
    } catch (e: Exception) {
        body.take(300)
    }

    /** Keeps at least MIN_GAP_MS between calls so the free tier's per-minute limit is not hit. */
    private fun pace() {
        val wait = lastCallAt + MIN_GAP_MS - System.currentTimeMillis()
        if (wait > 0) sleepChecked(wait)
        lastCallAt = System.currentTimeMillis()
    }

    private fun sleepChecked(ms: Long) {
        var left = ms
        while (left > 0) {
            if (cancelled) throw GeminiException("Cancelled")
            val step = if (left > 500) 500L else left
            Thread.sleep(step)
            left -= step
        }
    }

    companion object {
        private const val MAX_ATTEMPTS = 5
        private const val RETRY_WAIT_MS = 15_000L
        private const val MIN_GAP_MS = 7_000L

        /** Removes ```json fences if the model added them anyway. */
        fun cleanJson(text: String): String {
            var t = text.trim()
            if (t.startsWith("```")) {
                t = t.substringAfter('\n', t).substringBeforeLast("```").trim()
            }
            val first = t.indexOf('[')
            val last = t.lastIndexOf(']')
            return if (first >= 0 && last > first) t.substring(first, last + 1) else "[]"
        }

        /** Accepts 12.5, "12.5", "00:12.500" or "00:00:12,500". */
        fun timeOf(v: Any?): Double? = when (v) {
            null -> null
            is Number -> v.toDouble()
            is String -> Srt.parseTime(v)
            else -> null
        }
    }
}
