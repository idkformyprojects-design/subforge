package ai.subforge.universal.core

import android.content.Context
import android.net.Uri
import java.io.File

/**
 * Does the whole job on a background thread:
 *  1. prepare: cut the audio into 5-minute parts (or read the subtitle track)
 *  2. first pass: every part gets subtitles, from the best model if it answers, otherwise a backup model
 *  3. improve: parts made by a backup model are redone with the best model, and (if switched on)
 *     every part is double-checked by the best model. Busy? Wait and try again for a while.
 * Progress is saved after every part, so an interrupted job can continue where it stopped.
 */
class Worker(
    private val context: Context,
    private val mainModel: String,
    private val backups: List<String>,
    apiKey: String,
    private val events: Events,
) {
    interface Events {
        fun progress(text: String, fraction: Double)
        /** Every part has subtitles; the user can start editing. [stillImproving] = re-checks are coming. */
        fun firstPassDone(lines: Int, stillImproving: Boolean)
    }

    private val gemini = Gemini(apiKey)
    @Volatile private var cancelled = false
    private var mainBusy = false     // after the best model is busy once, do not wait long for it on every part
    private var mainMissing = false  // the best model's name does not exist for this key

    fun cancel() {
        cancelled = true
        gemini.cancelled = true
        Media.cancelAll()
    }

    private fun check() {
        if (cancelled) throw GeminiCancelledException()
    }

    // ------------------------------------------------------------------ 1. prepare

    fun prepare(video: Uri, name: String, langIdx: Int, fromSpeech: Boolean, doubleCheck: Boolean): Project {
        val dir = Project.partsDir(context)
        dir.deleteRecursively()
        val lang = Languages.get(langIdx)
        return if (fromSpeech) {
            val duration = Media.durationSec(context, video)
            events.progress("Extracting audio…", 0.0)
            val clips = Media.extractAudioClips(context, video, dir, CLIP_SEC) { t ->
                if (duration > 0) events.progress("Extracting audio… ${pct(t / duration)}", 0.05 * (t / duration))
            }
            check()
            val parts = clips.mapIndexed { i, f -> Part(i, i * CLIP_SEC.toDouble(), f.absolutePath, 0, 0) }
            Project(video, name, langIdx, true, doubleCheck, ArrayList(), parts.toMutableList(), ArrayList())
        } else {
            events.progress("Reading the subtitle track…", 0.02)
            val raw = Media.extractSubtitleTrack(context, video, File(context.cacheDir, "original.srt"))
                ?: throw GeminiException(
                    "No text subtitle track was found in this video. (Picture-based DVD/Blu-ray subtitles and " +
                        "subtitles burned into the picture are not supported yet.) Choose \"Speech in the video\" instead."
                )
            val cues = Srt.parse(raw).toMutableList()
            if (cues.isEmpty()) throw GeminiException("The subtitle track in this video is empty.")
            val parts = ArrayList<Part>()
            var from = 0
            while (from < cues.size) {
                val to = minOf(from + BATCH, cues.size)
                val p = Part(parts.size, 0.0, null, from, to)
                if (Languages.isOriginal(lang)) { p.status = Part.DONE; p.reviewed = true }
                for (k in from until to) cues[k].part = p.index
                parts.add(p)
                from = to
            }
            Project(video, name, langIdx, false, false, cues, parts, cues.map { it.text }.toMutableList())
        }
    }

    // ------------------------------------------------------------------ 2 + 3. run

    /** Runs (or continues) the job for the project in [ProjectStore]. Returns the final state. */
    fun run(): String {
        val p = ProjectStore.get(context) ?: throw GeminiException("Nothing to do: no project found.")
        ProjectStore.edit(context) { it.state = if (it.todoCount() > 0) Project.STATE_WORKING else Project.STATE_IMPROVING }

        // ---- first pass
        val total = p.parts.size
        for (part in p.parts.toList()) {
            if (part.status == Part.DONE) continue
            check()
            val done = p.parts.count { it.status == Part.DONE }
            events.progress(
                (if (p.fromSpeech) "Writing subtitles" else "Translating") + ": part ${done + 1} of $total",
                0.05 + 0.75 * done / total,
            )
            doPart(part, allModels(), firstPass = true)
        }

        val firstPassComplete = p.todoCount() == 0
        if (firstPassComplete) {
            val stillImproving = pendingTasks().isNotEmpty()
            ProjectStore.edit(context) { it.state = if (stillImproving) Project.STATE_IMPROVING else Project.STATE_DONE }
            events.firstPassDone(p.cues.size, stillImproving)
        }

        // ---- improve with the best model, waiting for it when it is busy
        val deadline = System.currentTimeMillis() + improveMs
        while (true) {
            val tasks = pendingTasks()
            if (tasks.isEmpty()) break
            for ((i, t) in tasks.withIndex()) {
                check()
                events.progress(t.label + " (${i + 1} of ${tasks.size})", 0.8 + 0.2 * i / tasks.size)
                try {
                    when (t.kind) {
                        Task.FILL -> doPart(t.part, allModels(), firstPass = false)
                        Task.REDO -> doPart(t.part, listOf(mainModel), firstPass = false)
                        Task.REVIEW -> reviewPart(t.part)
                    }
                } catch (e: GeminiBusyException) {
                    // still busy; try again next round
                }
                if (mainMissing) break
            }
            if (pendingTasks().isEmpty() || mainMissing) break
            if (System.currentTimeMillis() > deadline) break
            val left = pendingTasks().size
            events.progress("The best model is busy. Trying again in 2 minutes ($left part(s) left)…", 0.8)
            gemini.sleepChecked(roundWaitMs)
        }

        // ---- final state
        val left = pendingTasks().size
        val todo = p.todoCount()
        val finalState = when {
            todo > 0 -> Project.STATE_STOPPED
            left > 0 -> Project.STATE_PARTIAL
            else -> Project.STATE_DONE
        }
        val msg = when {
            todo > 0 -> "$todo part(s) could not be done because Gemini was busy. Tap Continue later."
            mainMissing -> "Your main model \"$mainModel\" was not found, so backup models were used. Check the model name in Settings."
            left > 0 -> "$left part(s) still need checking by the best model. Tap Re-check later."
            p.doubleCheck && p.fromSpeech -> "All parts made and double-checked by the best model."
            else -> "All parts made by the best model."
        }
        ProjectStore.edit(context) { it.state = finalState; it.message = msg }
        if (!firstPassComplete && todo == 0) events.firstPassDone(p.cues.size, false)
        return finalState
    }

    // ------------------------------------------------------------------ work on one part

    private class Task(val part: Part, val kind: Int, val label: String) {
        companion object {
            const val FILL = 0   // part has no subtitles yet: any model
            const val REDO = 1   // made by a backup model: redo with the best model
            const val REVIEW = 2 // double-check with the best model
        }
    }

    private fun pendingTasks(): List<Task> {
        val p = ProjectStore.get(context) ?: return emptyList()
        val out = ArrayList<Task>()
        synchronized(ProjectStore) {
            for (part in p.parts) {
                val n = part.index + 1
                when {
                    part.status != Part.DONE -> out.add(Task(part, Task.FILL, "Writing subtitles: part $n"))
                    mainMissing || p.partHasEdits(part) -> { /* leave the user's edits alone */ }
                    part.backup -> out.add(Task(part, Task.REDO, "Re-checking part $n with the best model"))
                    p.doubleCheck && p.fromSpeech && !part.reviewed ->
                        out.add(Task(part, Task.REVIEW, "Double-checking part $n"))
                }
            }
        }
        return out
    }

    private fun allModels(): List<String> = listOf(mainModel) + backups.filter { it != mainModel }

    /**
     * Makes subtitles for one part, trying [models] in order. Returns true on success.
     * A part made by any model other than the main one is marked as "backup".
     */
    private fun doPart(part: Part, models: List<String>, firstPass: Boolean): Boolean {
        val p = ProjectStore.get(context) ?: return false
        val target = if (Languages.isOriginal(p.language)) null else p.language.name
        val improving = part.status == Part.DONE && part.backup
        for (model in models) {
            check()
            val isMain = model == mainModel
            if (isMain && mainMissing) continue
            val attempts = when {
                !isMain -> BACKUP_ATTEMPTS
                firstPass && mainBusy -> 1      // do not wait long for a busy main model on every part
                firstPass -> MAIN_ATTEMPTS
                else -> IMPROVE_ATTEMPTS
            }
            try {
                if (p.fromSpeech) {
                    val clip = File(part.audio ?: return false)
                    if (!clip.exists()) throw GeminiException("The saved audio for part ${part.index + 1} is missing. Start the video again.")
                    val raw = gemini.transcribe(model, clip, target, attempts)
                    applySpeech(part, raw, model, isMain, if (improving && isMain) Cue.MARK_FIXED else null)
                } else {
                    val lines = synchronized(ProjectStore) { p.source.subList(part.from, part.to).toList() }
                    val out = gemini.translate(model, lines, p.language.name, attempts)
                    ProjectStore.edit(context) { pr ->
                        for (k in part.from until part.to) {
                            if (k !in pr.cues.indices || pr.cues[k].edited) continue
                            pr.cues[k].text = out[k - part.from]
                            pr.cues[k].mark = when {
                                !isMain -> Cue.MARK_BACKUP
                                improving -> Cue.MARK_FIXED
                                else -> Cue.MARK_NONE
                            }
                        }
                        markDone(part, model, isMain)
                    }
                }
                if (isMain) mainBusy = false
                return true
            } catch (e: GeminiMissingModelException) {
                if (isMain) mainMissing = true
            } catch (e: GeminiBusyException) {
                if (isMain) mainBusy = true
            }
        }
        if (models.size == 1) throw GeminiBusyException("$mainModel is busy")
        return false
    }

    /** Replaces one speech part's lines. [forceMark] overrides the normal mark (used when the best model fixes a part). */
    private fun applySpeech(part: Part, raw: List<Cue>, model: String, isMain: Boolean, forceMark: Int?) {
        val mark = forceMark ?: if (isMain) Cue.MARK_NONE else Cue.MARK_BACKUP
        val shifted = shift(part, raw).onEach { it.mark = mark }
        ProjectStore.edit(context) { pr ->
            if (pr.partHasEdits(part) && part.status == Part.DONE) return@edit // user edited meanwhile: keep theirs
            pr.cues.removeAll { it.part == part.index }
            pr.cues.addAll(shifted)
            tidy(pr.cues)
            markDone(part, model, isMain)
        }
    }

    private fun reviewPart(part: Part) {
        val p = ProjectStore.get(context) ?: return
        val clip = File(part.audio ?: return)
        if (!clip.exists()) { ProjectStore.edit(context) { part.reviewed = true }; return }
        val target = if (Languages.isOriginal(p.language)) null else p.language.name
        val current = synchronized(ProjectStore) {
            p.cues.filter { it.part == part.index }.map { Cue(it.start - part.offset, it.end - part.offset, it.text) }
        }
        val reviewed = try {
            gemini.review(mainModel, clip, current, target, IMPROVE_ATTEMPTS)
        } catch (e: GeminiMissingModelException) {
            mainMissing = true
            return
        }
        val oldMarks = HashMap<String, Int>()
        synchronized(ProjectStore) { p.cues.filter { it.part == part.index }.forEach { oldMarks[it.text.trim()] = it.mark } }
        ProjectStore.edit(context) { pr ->
            if (pr.partHasEdits(part)) return@edit
            // A suspiciously empty answer is ignored rather than wiping good subtitles.
            if (reviewed.isNotEmpty() || current.isEmpty()) {
                val fresh = shift(part, reviewed).onEach { it.mark = oldMarks[it.text.trim()] ?: Cue.MARK_FIXED }
                pr.cues.removeAll { it.part == part.index }
                pr.cues.addAll(fresh)
                tidy(pr.cues)
            }
            part.reviewed = true
        }
    }

    private fun markDone(part: Part, model: String, isMain: Boolean) {
        part.status = Part.DONE
        part.model = model
        part.backup = !isMain
        part.reviewed = false
    }

    /** Clip-relative times -> video times, kept inside the part. */
    private fun shift(part: Part, raw: List<Cue>): List<Cue> = raw.map { c ->
        val start = c.start.coerceIn(0.0, CLIP_SEC + 5.0) + part.offset
        val end = c.end.coerceIn(0.0, CLIP_SEC + 10.0) + part.offset
        Cue(start, if (end > start) end else start + 2.0, c.text, part.index)
    }

    private fun pct(f: Double) = "${(f.coerceIn(0.0, 1.0) * 100).toInt()}%"

    companion object {
        /** 5-minute clips keep Gemini's timestamps accurate and each request small. */
        const val CLIP_SEC = 300
        const val BATCH = 60
        private const val MAIN_ATTEMPTS = 4      // ~2 minutes of waiting before using a backup
        private const val BACKUP_ATTEMPTS = 2
        private const val IMPROVE_ATTEMPTS = 2
        // How long to keep waiting for the best model, and the pause between tries (changeable for tests).
        @Volatile var improveMs = 45 * 60_000L
        @Volatile var roundWaitMs = 120_000L

        /** Sorts lines and trims overlaps that can appear at part borders. */
        fun tidy(cues: MutableList<Cue>) {
            cues.sortBy { it.start }
            for (i in 0 until cues.size - 1) {
                val next = cues[i + 1]
                if (cues[i].end > next.start) cues[i].end = maxOf(cues[i].start + 0.3, next.start - 0.05)
            }
        }
    }
}
