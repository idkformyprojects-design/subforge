package ai.subforge.universal.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Something is wrong that waiting will not fix (bad key, bad request…). */
open class GeminiException(message: String) : IOException(message)

/** The model is overloaded or the per-minute/day limit is hit (HTTP 429/5xx, network). Worth trying later or another model. */
class GeminiBusyException(message: String) : GeminiException(message)

/** This model name does not exist (any more) for this key (HTTP 404). Skip it. */
class GeminiMissingModelException(message: String) : GeminiException(message)

class GeminiCancelledException : GeminiException("Cancelled")

/**
 * Talks to Google AI Studio (Gemini) directly with the user's own free API key.
 * One key works for every model; the model is chosen per call.
 */
class Gemini(private val apiKey: String) {

    @Volatile var cancelled = false
    private var lastCallAt = 0L

    // ------------------------------------------------------------------ jobs

    /** Transcribes one audio clip. Returned times are relative to the clip start. */
    fun transcribe(model: String, clip: File, targetLanguage: String?, attempts: Int): List<Cue> {
        val prompt = """
            You are a professional film subtitler. Listen to this audio clip from a video and
            produce subtitles for all spoken dialogue.
            ${languageRule(targetLanguage)}
            Rules:
            - "start" and "end" are numbers of seconds from the beginning of THIS clip (e.g. 12.4).
            - Each subtitle is 1 to 7 seconds long and at most 2 short lines (about 42 characters per line).
            - Subtitles must not overlap and must follow the order of speech.
            - Ignore music, sound effects and background noise. Do not describe sounds.
            - Never invent dialogue. If there is no speech, return an empty array [].
        """.trimIndent()
        val parts = JSONArray().put(audioPart(clip)).put(JSONObject().put("text", prompt))
        return parseCues(generate(model, parts, cueSchema(), attempts))
    }

    /**
     * Second listen: the model hears the clip again next to the current subtitles and returns a corrected list
     * (misheard words, names, missing lines, timing, translation quality).
     */
    fun review(model: String, clip: File, current: List<Cue>, targetLanguage: String?, attempts: Int): List<Cue> {
        val existing = JSONArray()
        for (c in current) {
            existing.put(JSONObject().put("start", round2(c.start)).put("end", round2(c.end)).put("text", c.text))
        }
        val prompt = """
            You are a senior subtitle editor checking another subtitler's work.
            Listen to this audio clip very carefully and compare it with the draft subtitles below.
            ${languageRule(targetLanguage)}
            Fix every mistake: misheard or wrong words, wrong names, missing dialogue, lines that were never said,
            wrong start/end times, and unnatural or inaccurate translation.
            Keep lines that are already correct exactly as they are.
            "start" and "end" are seconds from the beginning of THIS clip. Each line at most 2 short lines, 1 to 7 seconds.
            Return the complete corrected list of subtitles for the whole clip.
            Draft subtitles:
            $existing
        """.trimIndent()
        val parts = JSONArray().put(audioPart(clip)).put(JSONObject().put("text", prompt))
        return parseCues(generate(model, parts, cueSchema(), attempts))
    }

    /** Translates the text of the given lines, keeping their order and count. */
    fun translate(model: String, lines: List<String>, targetLanguage: String, attempts: Int): List<String> {
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
        val arr = JSONArray(cleanJson(generate(model, parts, schema, attempts)))
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
    fun test(model: String): String? = try {
        generate(model, JSONArray().put(JSONObject().put("text", "Reply with the word OK.")), null, 1)
        null
    } catch (e: Exception) {
        e.message ?: e.toString()
    }

    // ------------------------------------------------------------------ plumbing

    private fun languageRule(targetLanguage: String?) = if (targetLanguage == null) {
        "Write every line in the language that is actually spoken. Do not translate."
    } else {
        "Translate every line into natural, fluent $targetLanguage. If a line is already in $targetLanguage, keep it as spoken."
    }

    private fun audioPart(clip: File): JSONObject {
        val b64 = java.util.Base64.getEncoder().encodeToString(clip.readBytes())
        return JSONObject().put("inline_data", JSONObject().put("mime_type", "audio/mp3").put("data", b64))
    }

    private fun cueSchema() = arraySchema(
        JSONObject()
            .put("start", JSONObject().put("type", "NUMBER"))
            .put("end", JSONObject().put("type", "NUMBER"))
            .put("text", JSONObject().put("type", "STRING")),
        JSONArray().put("start").put("end").put("text"),
    )

    private fun arraySchema(properties: JSONObject, required: JSONArray): JSONObject =
        JSONObject().put("type", "ARRAY").put(
            "items",
            JSONObject().put("type", "OBJECT").put("properties", properties).put("required", required),
        )

    private fun parseCues(text: String): List<Cue> {
        val arr = JSONArray(cleanJson(text))
        val cues = ArrayList<Cue>()
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val t = o.optString("text").trim()
            val start = timeOf(o.opt("start")) ?: continue
            var end = timeOf(o.opt("end")) ?: (start + 2.0)
            if (end <= start) end = start + 2.0
            if (t.isNotEmpty()) cues.add(Cue(start, end, t))
        }
        return cues
    }

    /**
     * Sends one request to [model], trying up to [attempts] times with growing waits on busy errors.
     * Throws [GeminiBusyException] if it stays busy, [GeminiMissingModelException] on 404,
     * [GeminiException] for errors that waiting cannot fix.
     */
    private fun generate(model: String, parts: JSONArray, schema: JSONObject?, attempts: Int): String {
        val config = JSONObject().put("temperature", 0.2)
        if (schema != null) config.put("responseMimeType", "application/json").put("responseSchema", schema)
        val body = JSONObject()
            .put("contents", JSONArray().put(JSONObject().put("role", "user").put("parts", parts)))
            .put("generationConfig", config)
            .toString()
            .toByteArray(Charsets.UTF_8)
        val url = URL(
            "$baseUrl/v1beta/models/" +
                URLEncoder.encode(model, "UTF-8") + ":generateContent"
        )

        var lastError = "unknown error"
        val tries = attempts.coerceAtLeast(1)
        for (attempt in 1..tries) {
            checkCancelled()
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
                when (code) {
                    404 -> throw GeminiMissingModelException("$model: $lastError")
                    408, 429, 500, 502, 503, 504 -> { /* busy: retry below */ }
                    else -> throw GeminiException(lastError)
                }
            } catch (e: GeminiException) {
                throw e
            } catch (e: IOException) {
                lastError = "Network error: ${e.message}"
            } finally {
                conn.disconnect()
            }
            if (attempt < tries) sleepChecked(retryWaitMs * attempt)
        }
        throw GeminiBusyException("$model is busy ($lastError)")
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
            // Usually a cut-off or overloaded answer; treat as busy so it is retried.
            throw GeminiBusyException("Gemini returned an empty answer (finishReason=${cand.optString("finishReason")})")
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
        val wait = lastCallAt + minGapMs - System.currentTimeMillis()
        if (wait > 0) sleepChecked(wait)
        lastCallAt = System.currentTimeMillis()
    }

    private fun checkCancelled() {
        if (cancelled) throw GeminiCancelledException()
    }

    /** Sleeps in small steps so Cancel works immediately. */
    fun sleepChecked(ms: Long) {
        var left = ms
        while (left > 0) {
            checkCancelled()
            val step = if (left > 500) 500L else left
            Thread.sleep(step)
            left -= step
        }
    }

    companion object {
        // Changeable only so tests can use a local fake server and short waits.
        @Volatile var baseUrl = "https://generativelanguage.googleapis.com"
        @Volatile var retryWaitMs = 20_000L
        @Volatile var minGapMs = 7_000L

        private fun round2(v: Double) = Math.round(v * 100.0) / 100.0

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
