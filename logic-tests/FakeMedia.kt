package ai.subforge.universal.core
import android.content.Context
import android.net.Uri
import java.io.File
/** Test stand-in for the FFmpeg layer (the real one was tested separately with desktop FFmpeg). */
object Media {
    fun cancelAll() {}
    fun durationSec(context: Context, uri: Uri): Double = 900.0
    fun extractAudioClips(context: Context, video: Uri, outDir: File, clipSec: Int, onTimeSec: (Double) -> Unit): List<File> {
        outDir.mkdirs(); return (0 until 3).map { File(outDir, "clip_000$it.mp3").apply { writeBytes(ByteArray(64) { 7 }) } }
    }
    fun extractSubtitleTrack(context: Context, video: Uri, outSrt: File): String? =
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nGoodbye\n"
}
