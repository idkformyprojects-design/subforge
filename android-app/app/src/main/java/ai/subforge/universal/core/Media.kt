package ai.subforge.universal.core

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.OpenableColumns
import com.arthenica.ffmpegkit.FFmpegKit
import com.arthenica.ffmpegkit.FFmpegKitConfig
import com.arthenica.ffmpegkit.FFmpegSession
import com.arthenica.ffmpegkit.ReturnCode
import java.io.File
import java.util.concurrent.CountDownLatch

/** All on-phone video/audio work, done with FFmpegKit. Every function blocks; call from a worker thread. */
object Media {

    class Result(val ok: Boolean, val cancelled: Boolean, val log: String) {
        /** Last few log lines, which is where FFmpeg puts the real error. */
        fun errorTail(): String = log.trim().lines().takeLast(6).joinToString("\n")
    }

    private var fontsReady = false

    fun displayName(context: Context, uri: Uri): String {
        try {
            context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    val name = c.getString(0)
                    if (!name.isNullOrBlank()) return name
                }
            }
        } catch (e: Exception) {
            // fall through
        }
        return uri.lastPathSegment ?: "video"
    }

    /** Video length in seconds, or 0 if Android cannot tell. */
    fun durationSec(context: Context, uri: Uri): Double {
        val r = MediaMetadataRetriever()
        return try {
            r.setDataSource(context, uri)
            (r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L) / 1000.0
        } catch (e: Exception) {
            0.0
        } finally {
            try { r.release() } catch (e: Exception) { }
        }
    }

    /**
     * Runs one FFmpeg command and waits for it. Arguments are passed as an array,
     * so file names with spaces or quotes are safe.
     * [onTimeSec] receives the current position in the output, for progress bars.
     */
    fun run(args: List<String>, onTimeSec: ((Double) -> Unit)? = null): Result {
        val latch = CountDownLatch(1)
        var finished: FFmpegSession? = null
        FFmpegKit.executeWithArgumentsAsync(
            args.toTypedArray(),
            { session -> finished = session; latch.countDown() },
            null,
            { stats -> onTimeSec?.invoke(stats.time / 1000.0) },
        )
        latch.await()
        val s = finished ?: return Result(false, false, "FFmpeg did not report a result")
        val rc = s.returnCode
        val log = s.allLogsAsString ?: ""
        return Result(ReturnCode.isSuccess(rc), ReturnCode.isCancel(rc), log)
    }

    fun cancelAll() = FFmpegKit.cancel()

    /**
     * Pulls the first audio track out of the video as 16 kHz mono MP3 clips of [clipSec] seconds
     * (about 1.2 MB per 5 minutes). Returns the clips in order.
     */
    fun extractAudioClips(
        context: Context, video: Uri, outDir: File, clipSec: Int, onTimeSec: (Double) -> Unit,
    ): List<File> {
        outDir.deleteRecursively()
        outDir.mkdirs()
        val input = FFmpegKitConfig.getSafParameterForRead(context, video)
        val res = run(
            listOf(
                "-y", "-i", input, "-vn", "-sn", "-map", "0:a:0",
                "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k",
                "-f", "segment", "-segment_time", clipSec.toString(), "-reset_timestamps", "1",
                File(outDir, "clip_%04d.mp3").absolutePath,
            ),
            onTimeSec,
        )
        if (res.cancelled) throw InterruptedException("Cancelled")
        if (!res.ok) {
            throw IllegalStateException("Could not read the audio from this video.\n" + res.errorTail())
        }
        val clips = outDir.listFiles { f -> f.name.endsWith(".mp3") && f.length() > 0 }?.sortedBy { it.name }
        if (clips.isNullOrEmpty()) throw IllegalStateException("This video has no audio track.")
        return clips
    }

    /**
     * Copies the first text subtitle track (SRT/ASS/VTT/mov_text) out of the video.
     * Returns null when there is none. Picture-based tracks (DVD/Blu-ray) are not supported.
     */
    fun extractSubtitleTrack(context: Context, video: Uri, outSrt: File): String? {
        outSrt.delete()
        val input = FFmpegKitConfig.getSafParameterForRead(context, video)
        val res = run(listOf("-y", "-i", input, "-map", "0:s:0", "-c:s", "srt", outSrt.absolutePath))
        if (res.cancelled) throw InterruptedException("Cancelled")
        if (!res.ok || !outSrt.exists() || outSrt.length() == 0L) return null
        return outSrt.readText(Charsets.UTF_8)
    }

    enum class Export(val label: String, val mime: String, val ext: String) {
        MP4_TRACK("MP4 + subtitle track (fast, no quality loss)", "video/mp4", "mp4"),
        MKV_TRACK("MKV + subtitle track (fast, works with any audio)", "video/x-matroska", "mkv"),
        BURN_IN("Burn subtitles into the picture (slow, shows on every player)", "video/mp4", "mp4"),
    }

    /**
     * Writes a new video with the subtitles. Tries to write straight to the chosen file;
     * if that fails, writes to app storage first and then copies it over.
     */
    fun export(
        context: Context, video: Uri, srt: File, out: Uri, kind: Export, languageCode: String,
        onTimeSec: (Double) -> Unit,
    ): Result {
        if (kind == Export.BURN_IN && !fontsReady) {
            // Lets the subtitle renderer use the phone's own fonts (covers Bengali, Hindi, Arabic, CJK...).
            try {
                FFmpegKitConfig.setFontDirectory(context, "/system/fonts", null)
                fontsReady = true
            } catch (e: Exception) {
                // continue; FFmpeg will fall back to its default font
            }
        }
        val direct = run(exportArgs(context, video, srt, FFmpegKitConfig.getSafParameterForWrite(context, out), kind, languageCode), onTimeSec)
        if (direct.ok || direct.cancelled) return direct

        val tmpDir = context.getExternalFilesDir(null) ?: context.filesDir
        val tmp = File(tmpDir, "export_tmp." + kind.ext)
        tmp.delete()
        val viaFile = run(exportArgs(context, video, srt, tmp.absolutePath, kind, languageCode), onTimeSec)
        if (!viaFile.ok) {
            tmp.delete()
            return viaFile
        }
        return try {
            context.contentResolver.openOutputStream(out, "w")!!.use { os ->
                tmp.inputStream().use { it.copyTo(os, 1 shl 20) }
            }
            viaFile
        } catch (e: Exception) {
            Result(false, false, "Could not save the video: ${e.message}")
        } finally {
            tmp.delete()
        }
    }

    private fun exportArgs(
        context: Context, video: Uri, srt: File, output: String, kind: Export, lang: String,
    ): List<String> {
        val input = FFmpegKitConfig.getSafParameterForRead(context, video)
        return when (kind) {
            Export.MP4_TRACK, Export.MKV_TRACK -> {
                val mp4 = kind == Export.MP4_TRACK
                listOf(
                    "-y", "-i", input, "-i", srt.absolutePath,
                    "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
                    "-c:v", "copy", "-c:a", "copy", "-c:s", if (mp4) "mov_text" else "srt",
                    "-metadata:s:s:0", "language=$lang", "-disposition:s:0", "default",
                    "-f", if (mp4) "mp4" else "matroska", output,
                )
            }
            Export.BURN_IN -> {
                val style = "FontName=Noto Sans,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000," +
                    "BorderStyle=1,Outline=2,Shadow=0,MarginV=28"
                listOf(
                    "-y", "-i", input,
                    "-vf", "subtitles=filename='${srt.absolutePath}':charenc=UTF-8:force_style='$style'",
                    "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                    "-f", "mp4", output,
                )
            }
        }
    }
}
