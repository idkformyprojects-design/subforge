package ai.subforge.universal.core

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

/**
 * One piece of work: a 5-minute audio clip (speech mode) or a batch of lines to translate (subtitle-track mode).
 * Keeping the audio lets the app redo or double-check a part later with the best model.
 */
class Part(
    val index: Int,
    val offset: Double,        // seconds from the start of the video (speech mode)
    val audio: String?,        // path of the saved clip (speech mode), null in track mode
    val from: Int,             // cue range [from, to) in track mode
    val to: Int,
    var status: Int = TODO,
    var model: String = "",
    var backup: Boolean = false,   // made by a backup model, should be redone with the best one
    var reviewed: Boolean = false, // double-checked by the best model
) {
    companion object {
        const val TODO = 0
        const val DONE = 1
    }
}

/** The current job: the video, the settings it was started with, its parts and the subtitle lines. */
class Project(
    val videoUri: Uri,
    val videoName: String,
    val languageIndex: Int,
    val fromSpeech: Boolean,
    var doubleCheck: Boolean,
    val cues: MutableList<Cue>,
    val parts: MutableList<Part>,
    val source: MutableList<String>, // original lines (track mode) so batches can be re-translated
    var state: String = STATE_WORKING,
    var message: String = "",
) {
    val language: Languages.Lang get() = Languages.get(languageIndex)

    fun partHasEdits(p: Part): Boolean =
        if (fromSpeech) cues.any { it.part == p.index && it.edited }
        else (p.from until p.to).any { it in cues.indices && cues[it].edited }

    fun backupCount() = parts.count { it.status == Part.DONE && it.backup }
    fun todoCount() = parts.count { it.status != Part.DONE }

    fun srtFile(context: Context): File {
        val f = File(context.cacheDir, "subtitles.srt")
        f.writeText(Srt.format(cues), Charsets.UTF_8)
        return f
    }

    fun save(context: Context) {
        val cs = JSONArray()
        for (c in cues) {
            cs.put(
                JSONObject().put("s", c.start).put("e", c.end).put("t", c.text)
                    .put("p", c.part).put("m", c.mark).put("x", c.edited)
            )
        }
        val ps = JSONArray()
        for (p in parts) {
            ps.put(
                JSONObject().put("i", p.index).put("o", p.offset).put("a", p.audio ?: "")
                    .put("f", p.from).put("t", p.to).put("st", p.status).put("m", p.model)
                    .put("b", p.backup).put("r", p.reviewed)
            )
        }
        val json = JSONObject()
            .put("video", videoUri.toString())
            .put("name", videoName)
            .put("lang", languageIndex)
            .put("speech", fromSpeech)
            .put("dc", doubleCheck)
            .put("cues", cs)
            .put("parts", ps)
            .put("src", JSONArray(source))
            .put("state", state)
            .put("msg", message)
        val f = file(context)
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(json.toString(), Charsets.UTF_8)
        if (!tmp.renameTo(f)) {
            f.writeText(json.toString(), Charsets.UTF_8)
            tmp.delete()
        }
    }

    companion object {
        const val STATE_WORKING = "working"    // first pass not finished
        const val STATE_IMPROVING = "improving" // all parts have subtitles; re-checking with the best model
        const val STATE_DONE = "done"
        const val STATE_PARTIAL = "partial"     // finished, but some parts still need the best model
        const val STATE_STOPPED = "stopped"     // cancelled, interrupted or failed; can be continued

        private fun file(context: Context) = File(context.filesDir, "project.json")

        /** Saved audio parts live here (in app storage, deleted when a new video is started). */
        fun partsDir(context: Context) = File(context.filesDir, "parts")

        fun exists(context: Context) = file(context).exists()

        fun load(context: Context): Project? = try {
            val json = JSONObject(file(context).readText(Charsets.UTF_8))
            val cs = json.getJSONArray("cues")
            val cues = ArrayList<Cue>()
            for (i in 0 until cs.length()) {
                val o = cs.getJSONObject(i)
                cues.add(
                    Cue(
                        o.getDouble("s"), o.getDouble("e"), o.getString("t"),
                        o.optInt("p", -1), o.optInt("m", Cue.MARK_NONE), o.optBoolean("x", false),
                    )
                )
            }
            val parts = ArrayList<Part>()
            val ps = json.optJSONArray("parts") ?: JSONArray()
            for (i in 0 until ps.length()) {
                val o = ps.getJSONObject(i)
                parts.add(
                    Part(
                        o.getInt("i"), o.optDouble("o", 0.0), o.optString("a").ifEmpty { null },
                        o.optInt("f", 0), o.optInt("t", 0), o.optInt("st", Part.DONE), o.optString("m"),
                        o.optBoolean("b", false), o.optBoolean("r", false),
                    )
                )
            }
            val src = ArrayList<String>()
            val sa = json.optJSONArray("src") ?: JSONArray()
            for (i in 0 until sa.length()) src.add(sa.getString(i))
            Project(
                Uri.parse(json.getString("video")), json.optString("name"), json.optInt("lang", 1),
                json.optBoolean("speech", true), json.optBoolean("dc", false),
                cues, parts, src, json.optString("state", STATE_DONE), json.optString("msg"),
            )
        } catch (e: Exception) {
            null
        }
    }
}

/**
 * The one shared copy of the project, used by the background job and the screens at the same time.
 * All changes go through [edit], so the job and the editor never overwrite each other's work.
 */
object ProjectStore {
    private var current: Project? = null
    private val listeners = CopyOnWriteArrayList<Runnable>()
    private val main = Handler(Looper.getMainLooper())

    /** Live status of the background job, for the screens. */
    @Volatile var running = false
    @Volatile var statusText = ""
    @Volatile var fraction = -1.0

    fun get(context: Context): Project? = synchronized(this) {
        current ?: Project.load(context.applicationContext).also { current = it }
    }

    fun replace(context: Context, p: Project) {
        synchronized(this) {
            current = p
            p.save(context.applicationContext)
        }
        changed()
    }

    /** Runs [block] on the project under the lock, saves it, and tells the screens. */
    fun <T> edit(context: Context, block: (Project) -> T): T? {
        val r = synchronized(this) {
            val p = get(context) ?: return null
            val out = block(p)
            p.save(context.applicationContext)
            out
        }
        changed()
        return r
    }

    fun status(text: String, f: Double) {
        statusText = text
        fraction = f
        changed()
    }

    fun addListener(l: Runnable) = listeners.add(l)
    fun removeListener(l: Runnable) = listeners.remove(l)

    private fun changed() {
        main.post { for (l in listeners) l.run() }
    }
}
