package ai.subforge.universal.core

import java.util.Locale

/**
 * One subtitle line. Times are in seconds from the start of the video.
 * [part] = which 5-minute part (or translation batch) it came from.
 * [mark] = MARK_NONE, MARK_BACKUP (made by a backup model) or MARK_FIXED (corrected by the best model).
 * [edited] = the user changed it by hand, so background re-checks must not overwrite it.
 */
data class Cue(
    var start: Double,
    var end: Double,
    var text: String,
    var part: Int = -1,
    var mark: Int = MARK_NONE,
    var edited: Boolean = false,
) {
    companion object {
        const val MARK_NONE = 0
        const val MARK_BACKUP = 1
        const val MARK_FIXED = 2
    }
}

/** Read and write SubRip (.srt) subtitle files. */
object Srt {

    /** 3723.5 -> "01:02:03,500" */
    fun ts(seconds: Double): String {
        val totalMs = Math.round(seconds.coerceAtLeast(0.0) * 1000.0)
        val h = totalMs / 3_600_000
        val m = (totalMs / 60_000) % 60
        val s = (totalMs / 1000) % 60
        val ms = totalMs % 1000
        return String.format(Locale.US, "%02d:%02d:%02d,%03d", h, m, s, ms)
    }

    /**
     * Parses "01:02:03,500", "01:02:03.5", "02:03.500", "123.5" or "123" into seconds.
     * Returns null if the text is not a time.
     */
    fun parseTime(raw: String): Double? {
        val t = raw.trim().replace(',', '.')
        if (t.isEmpty()) return null
        val parts = t.split(':')
        return try {
            when (parts.size) {
                1 -> parts[0].toDouble()
                2 -> parts[0].toInt() * 60.0 + parts[1].toDouble()
                3 -> parts[0].toInt() * 3600.0 + parts[1].toInt() * 60.0 + parts[2].toDouble()
                else -> null
            }
        } catch (e: NumberFormatException) {
            null
        }
    }

    fun format(cues: List<Cue>): String {
        val sb = StringBuilder()
        var n = 1
        for (c in cues.sortedBy { it.start }) {
            val text = c.text.trim()
            if (text.isEmpty()) continue
            val end = if (c.end > c.start) c.end else c.start + 2.0
            sb.append(n++).append('\n')
            sb.append(ts(c.start)).append(" --> ").append(ts(end)).append('\n')
            sb.append(text).append("\n\n")
        }
        return sb.toString()
    }

    fun parse(content: String): List<Cue> {
        val out = ArrayList<Cue>()
        val normalized = content.replace("﻿", "").replace("\r\n", "\n").replace('\r', '\n')
        val blocks = normalized.split(Regex("\n\\s*\n"))
        for (block in blocks) {
            val lines = block.split('\n').map { it.trimEnd() }.filter { it.isNotEmpty() }
            val timeIdx = lines.indexOfFirst { it.contains("-->") }
            if (timeIdx < 0) continue
            val sides = lines[timeIdx].split("-->")
            if (sides.size != 2) continue
            val start = parseTime(sides[0]) ?: continue
            // the end side may carry position tags after the time, e.g. "00:00:05,000 X1:..."
            val end = parseTime(sides[1].trim().split(' ')[0]) ?: continue
            val text = lines.drop(timeIdx + 1).joinToString("\n")
                .replace(Regex("<[^>]+>"), "")       // <i>, <font ...>
                .replace(Regex("\\{\\\\[^}]*\\}"), "") // {\an8} style ASS overrides
                .trim()
            if (text.isNotEmpty()) out.add(Cue(start, end, text))
        }
        return out
    }
}
