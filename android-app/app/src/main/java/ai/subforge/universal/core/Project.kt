package ai.subforge.universal.core

import android.content.Context
import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** The current job: which video, which language, and the subtitle lines. Saved so nothing is lost. */
class Project(
    val videoUri: Uri,
    val videoName: String,
    val languageIndex: Int,
    val cues: MutableList<Cue>,
) {
    val language: Languages.Lang get() = Languages.get(languageIndex)

    fun save(context: Context) {
        val arr = JSONArray()
        for (c in cues) arr.put(JSONObject().put("s", c.start).put("e", c.end).put("t", c.text))
        val json = JSONObject()
            .put("video", videoUri.toString())
            .put("name", videoName)
            .put("lang", languageIndex)
            .put("cues", arr)
        val f = file(context)
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(json.toString(), Charsets.UTF_8)
        tmp.renameTo(f)
    }

    fun srtFile(context: Context): File {
        val f = File(context.cacheDir, "subtitles.srt")
        f.writeText(Srt.format(cues), Charsets.UTF_8)
        return f
    }

    companion object {
        private fun file(context: Context) = File(context.filesDir, "project.json")

        fun exists(context: Context) = file(context).exists()

        fun load(context: Context): Project? = try {
            val json = JSONObject(file(context).readText(Charsets.UTF_8))
            val arr = json.getJSONArray("cues")
            val cues = ArrayList<Cue>()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                cues.add(Cue(o.getDouble("s"), o.getDouble("e"), o.getString("t")))
            }
            Project(Uri.parse(json.getString("video")), json.optString("name"), json.optInt("lang", 1), cues)
        } catch (e: Exception) {
            null
        }
    }
}

/**
 * The whole job, start to finish, on a worker thread:
 *  speech mode:    video -> audio clips (FFmpeg) -> Gemini per clip -> one timeline
 *  subtitle mode:  video -> existing subtitle track (FFmpeg) -> Gemini translation
 */
class Pipeline(
    private val context: Context,
    private val gemini: Gemini,
    private val onProgress: (message: String, fraction: Double) -> Unit,
) {
    @Volatile private var cancelled = false

    fun cancel() {
        cancelled = true
        gemini.cancelled = true
        Media.cancelAll()
    }

    private fun check() {
        if (cancelled) throw InterruptedException("Cancelled")
    }

    fun fromSpeech(video: Uri, lang: Languages.Lang): List<Cue> {
        val duration = Media.durationSec(context, video)
        onProgress("Extracting audio…", 0.0)
        val clips = Media.extractAudioClips(context, video, File(context.cacheDir, "clips"), CLIP_SEC) { t ->
            if (duration > 0) onProgress("Extracting audio… ${pct(t / duration)}", 0.10 * (t / duration))
        }
        check()
        val target = if (Languages.isOriginal(lang)) null else lang.name
        val all = ArrayList<Cue>()
        for ((i, clip) in clips.withIndex()) {
            check()
            onProgress("Listening and writing subtitles: part ${i + 1} of ${clips.size}", 0.10 + 0.90 * i / clips.size)
            val offset = i * CLIP_SEC.toDouble()
            for (c in gemini.transcribe(clip, target)) {
                val start = (c.start.coerceIn(0.0, CLIP_SEC + 5.0)) + offset
                val end = (c.end.coerceIn(0.0, CLIP_SEC + 10.0)) + offset
                all.add(Cue(start, if (end > start) end else start + 2.0, c.text))
            }
            clip.delete()
        }
        return tidy(all)
    }

    fun fromSubtitleTrack(video: Uri, lang: Languages.Lang): List<Cue> {
        onProgress("Reading the subtitle track…", 0.02)
        val raw = Media.extractSubtitleTrack(context, video, File(context.cacheDir, "original.srt"))
            ?: throw IllegalStateException(
                "No text subtitle track was found in this video.\n" +
                    "(Picture-based DVD/Blu-ray subtitles and subtitles burned into the picture are not supported yet.) " +
                    "Choose \"Speech in the video\" instead."
            )
        val cues = Srt.parse(raw).toMutableList()
        if (cues.isEmpty()) throw IllegalStateException("The subtitle track in this video is empty.")
        if (Languages.isOriginal(lang)) return cues

        val batches = cues.chunked(BATCH)
        for ((b, batch) in batches.withIndex()) {
            check()
            onProgress("Translating: part ${b + 1} of ${batches.size}", 0.05 + 0.95 * b / batches.size)
            val translated = gemini.translate(batch.map { it.text }, lang.name)
            batch.forEachIndexed { i, c -> c.text = translated[i] }
        }
        return cues
    }

    /** Sorts lines and trims overlaps that can appear at clip borders. */
    private fun tidy(cues: MutableList<Cue>): List<Cue> {
        cues.sortBy { it.start }
        for (i in 0 until cues.size - 1) {
            val next = cues[i + 1]
            if (cues[i].end > next.start) cues[i].end = maxOf(cues[i].start + 0.3, next.start - 0.05)
        }
        return cues
    }

    private fun pct(f: Double) = "${(f.coerceIn(0.0, 1.0) * 100).toInt()}%"

    companion object {
        /** 5-minute clips keep Gemini's timestamps accurate and each request small. */
        const val CLIP_SEC = 300
        const val BATCH = 60
    }
}
