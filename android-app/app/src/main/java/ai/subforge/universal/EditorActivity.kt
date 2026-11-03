package ai.subforge.universal

import ai.subforge.universal.core.Cue
import ai.subforge.universal.core.Media
import ai.subforge.universal.core.Project
import ai.subforge.universal.core.Srt
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

class EditorActivity : Activity() {

    private lateinit var project: Project
    private lateinit var adapter: CueAdapter
    private lateinit var info: TextView
    private var pendingExport: Media.Export? = null
    private var exporting = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val p = Project.load(this)
        if (p == null) {
            Toast.makeText(this, "Could not open the subtitles", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        project = p
        title = p.videoName.ifBlank { "Subtitles" }

        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(dp(8), dp(8), dp(8), 0)
        }
        bar.addView(barButton("Save .srt") { saveSrt() })
        bar.addView(barButton("Export video") { chooseExport() })
        bar.addView(barButton("Shift time") { shiftAll() })
        root.addView(bar)

        info = TextView(this).apply { setPadding(dp(12), dp(6), dp(12), dp(6)); textSize = 13f }
        root.addView(info)

        adapter = CueAdapter()
        val list = ListView(this).apply {
            adapter = this@EditorActivity.adapter
            setOnItemClickListener { _, _, pos, _ -> editCue(pos) }
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f)
        }
        root.addView(list)
        setContentView(root)
        refresh()
    }

    private fun refresh() {
        adapter.notifyDataSetChanged()
        info.text = "${project.cues.size} lines · ${project.language.name} · tap a line to edit it"
    }

    private fun persist() {
        project.save(this)
        refresh()
    }

    // ---------------------------------------------------------------- editing

    private fun editCue(pos: Int) {
        val c = project.cues[pos]
        val pad = dp(20)
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, dp(8), pad, 0) }
        val startF = timeField(c.start)
        val endF = timeField(c.end)
        val textF = EditText(this).apply {
            setText(c.text)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_FLAG_CAP_SENTENCES
            minLines = 2
        }
        box.addView(label("Start")); box.addView(startF)
        box.addView(label("End")); box.addView(endF)
        box.addView(label("Text")); box.addView(textF)

        AlertDialog.Builder(this)
            .setTitle("Line ${pos + 1}")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val s = Srt.parseTime(startF.text.toString())
                val e = Srt.parseTime(endF.text.toString())
                if (s == null || e == null || e <= s) {
                    toast("Times must look like 00:01:02,500 and end after start")
                    return@setPositiveButton
                }
                c.start = s; c.end = e; c.text = textF.text.toString().trim()
                project.cues.sortBy { it.start }
                persist()
            }
            .setNeutralButton("Delete") { _, _ -> project.cues.removeAt(pos); persist() }
            .setNegativeButton("Add line after") { _, _ ->
                val nextStart = project.cues.getOrNull(pos + 1)?.start ?: (c.end + 3.0)
                val s = c.end + 0.1
                val e = if (nextStart - 0.1 > s + 0.5) nextStart - 0.1 else s + 2.0
                project.cues.add(pos + 1, Cue(s, e, "…"))
                persist()
                editCue(pos + 1)
            }
            .show()
    }

    /** Moves every line earlier or later, for when all subtitles are out of sync by the same amount. */
    private fun shiftAll() {
        val f = EditText(this).apply {
            hint = "e.g. 1.5 or -0.8"
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL or InputType.TYPE_NUMBER_FLAG_SIGNED
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(8), dp(20), 0)
            addView(TextView(this@EditorActivity).apply { text = "Seconds to move all lines (minus = earlier):" })
            addView(f)
        }
        AlertDialog.Builder(this)
            .setTitle("Shift all timings")
            .setView(box)
            .setPositiveButton("Shift") { _, _ ->
                val d = f.text.toString().toDoubleOrNull() ?: return@setPositiveButton
                for (c in project.cues) {
                    c.start = (c.start + d).coerceAtLeast(0.0)
                    c.end = (c.end + d).coerceAtLeast(c.start + 0.3)
                }
                persist()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ---------------------------------------------------------------- saving and exporting

    private fun baseName(): String = project.videoName.substringBeforeLast('.').ifBlank { "subtitles" }

    private fun saveSrt() {
        val i = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "application/x-subrip"
            putExtra(Intent.EXTRA_TITLE, "${baseName()}.${project.language.iso639_2}.srt")
        }
        startActivityForResult(i, REQ_SRT)
    }

    private fun chooseExport() {
        if (exporting) return toast("An export is already running")
        val kinds = Media.Export.values()
        AlertDialog.Builder(this)
            .setTitle("Export video")
            .setItems(kinds.map { it.label }.toTypedArray()) { _, which ->
                val k = kinds[which]
                pendingExport = k
                val i = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = k.mime
                    putExtra(Intent.EXTRA_TITLE, "${baseName()}.subtitled.${k.ext}")
                }
                startActivityForResult(i, REQ_VIDEO)
            }
            .show()
    }

    @Deprecated("Uses the platform API on purpose so the app needs no extra libraries")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data
        if (resultCode != RESULT_OK || uri == null) return
        when (requestCode) {
            REQ_SRT -> writeSrt(uri)
            REQ_VIDEO -> pendingExport?.let { exportVideo(uri, it) }
        }
    }

    private fun writeSrt(uri: Uri) {
        try {
            contentResolver.openOutputStream(uri, "w")!!.use { it.write(Srt.format(project.cues).toByteArray(Charsets.UTF_8)) }
            toast("Subtitle file saved")
        } catch (e: Exception) {
            toast("Could not save: ${e.message}")
        }
    }

    private fun exportVideo(out: Uri, kind: Media.Export) {
        exporting = true
        val srt = project.srtFile(this)
        val duration = Media.durationSec(this, project.videoUri)

        val bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            isIndeterminate = duration <= 0
        }
        val msg = TextView(this).apply { text = "Starting…"; setPadding(0, dp(8), 0, 0) }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(12), dp(20), 0)
            addView(bar); addView(msg)
        }
        val dialog = AlertDialog.Builder(this)
            .setTitle(if (kind == Media.Export.BURN_IN) "Burning subtitles into the video" else "Adding subtitle track")
            .setView(box)
            .setCancelable(false)
            .setNegativeButton("Cancel") { _, _ -> Media.cancelAll() }
            .show()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val lang = project.language.iso639_2
        thread(name = "subforge-export") {
            val res = Media.export(applicationContext, project.videoUri, srt, out, kind, lang) { t ->
                if (duration > 0) runOnUiThread {
                    val f = (t / duration).coerceIn(0.0, 1.0)
                    bar.progress = (f * 1000).toInt()
                    msg.text = "${(f * 100).toInt()}%"
                }
            }
            runOnUiThread {
                exporting = false
                window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                if (dialog.isShowing) dialog.dismiss()
                when {
                    res.ok -> toast("Video saved ✓")
                    res.cancelled -> toast("Export cancelled")
                    else -> AlertDialog.Builder(this)
                        .setTitle("Export failed")
                        .setMessage(
                            (if (kind == Media.Export.MP4_TRACK) "Tip: try the MKV option; some audio formats cannot go into MP4.\n\n" else "") +
                                res.errorTail()
                        )
                        .setPositiveButton("OK", null)
                        .show()
                }
            }
        }
    }

    // ---------------------------------------------------------------- list rows

    private inner class CueAdapter : BaseAdapter() {
        override fun getCount() = project.cues.size
        override fun getItem(position: Int): Any = project.cues[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val row = (convertView as? LinearLayout) ?: LinearLayout(this@EditorActivity).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(8), dp(12), dp(8))
                addView(TextView(this@EditorActivity).apply { textSize = 12f; setTypeface(Typeface.MONOSPACE) })
                addView(TextView(this@EditorActivity).apply { textSize = 16f })
            }
            val c = project.cues[position]
            (row.getChildAt(0) as TextView).text = "${position + 1}   ${Srt.ts(c.start)} → ${Srt.ts(c.end)}"
            (row.getChildAt(1) as TextView).text = c.text
            return row
        }
    }

    // ---------------------------------------------------------------- helpers

    private fun timeField(sec: Double) = EditText(this).apply {
        setText(Srt.ts(sec))
        isSingleLine = true
        inputType = InputType.TYPE_CLASS_TEXT
    }

    private fun label(t: String) = TextView(this).apply { text = t; setPadding(0, dp(8), 0, 0) }

    private fun barButton(t: String, onClick: () -> Unit) = Button(this).apply {
        text = t
        isAllCaps = false
        layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        setOnClickListener { onClick() }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    companion object {
        private const val REQ_SRT = 20
        private const val REQ_VIDEO = 21
    }
}
