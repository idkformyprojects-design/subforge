package ai.subforge.universal

import ai.subforge.universal.core.Gemini
import ai.subforge.universal.core.Languages
import ai.subforge.universal.core.Media
import ai.subforge.universal.core.Pipeline
import ai.subforge.universal.core.Prefs
import ai.subforge.universal.core.Project
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

class MainActivity : Activity() {

    private lateinit var prefs: Prefs
    private lateinit var videoLabel: TextView
    private lateinit var modeGroup: RadioGroup
    private lateinit var langSpinner: Spinner
    private lateinit var startBtn: Button
    private lateinit var cancelBtn: Button
    private lateinit var openLastBtn: Button
    private lateinit var progress: ProgressBar
    private lateinit var status: TextView

    private var videoUri: Uri? = null
    private var pipeline: Pipeline? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        videoUri = savedInstanceState?.getString("video")?.let { Uri.parse(it) }
        buildUi()
        videoUri?.let { videoLabel.text = Media.displayName(this, it) }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        videoUri?.let { outState.putString("video", it.toString()) }
    }

    override fun onResume() {
        super.onResume()
        openLastBtn.visibility = if (Project.exists(this)) View.VISIBLE else View.GONE
    }

    private fun buildUi() {
        val pad = dp(16)
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        col.addView(heading("1. Choose a video"))
        col.addView(button("Choose video") { pickVideo() })
        videoLabel = TextView(this).apply { text = "No video chosen"; setPadding(0, dp(6), 0, dp(12)) }
        col.addView(videoLabel)

        col.addView(heading("2. Make subtitles from"))
        modeGroup = RadioGroup(this).apply {
            addView(RadioButton(this@MainActivity).apply { id = MODE_SPEECH; text = "Speech in the video" })
            addView(RadioButton(this@MainActivity).apply { id = MODE_TRACK; text = "Existing subtitle track in the video (translate it)" })
            check(MODE_SPEECH)
            setPadding(0, 0, 0, dp(12))
        }
        col.addView(modeGroup)

        col.addView(heading("3. Subtitle language"))
        langSpinner = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@MainActivity, android.R.layout.simple_spinner_dropdown_item, Languages.ALL.map { it.name },
            )
            setSelection(prefs.languageIndex.coerceIn(0, Languages.ALL.size - 1))
        }
        col.addView(langSpinner)

        startBtn = button("Start") { start() }
        col.addView(space(16))
        col.addView(startBtn)

        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            visibility = View.GONE
        }
        col.addView(progress)
        status = TextView(this).apply { setPadding(0, dp(8), 0, dp(8)) }
        col.addView(status)
        cancelBtn = button("Cancel") { pipeline?.cancel(); status.text = "Cancelling…" }.apply { visibility = View.GONE }
        col.addView(cancelBtn)

        col.addView(space(24))
        openLastBtn = button("Open last subtitles") { openEditor() }
        col.addView(openLastBtn)
        col.addView(button("Settings (Gemini API key)") { showSettings() })

        col.addView(TextView(this).apply {
            setPadding(0, dp(20), 0, 0)
            textSize = 13f
            text = "Your video stays on your phone. Only the sound (about 1 MB per 5 minutes) is sent to Google Gemini, " +
                "using your own free API key. Keep this screen open while it works; a 2-hour movie takes roughly 5–15 minutes."
        })

        setContentView(ScrollView(this).apply { addView(col) })
    }

    // ---------------------------------------------------------------- actions

    private fun pickVideo() {
        val i = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("video/*", "application/x-matroska", "application/octet-stream"))
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        startActivityForResult(i, REQ_PICK)
    }

    @Deprecated("Uses the platform API on purpose so the app needs no extra libraries")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_PICK || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        try {
            // Keep access after restarts, so the editor can still export this video later.
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (e: SecurityException) {
            // some file pickers do not offer it; access still works for this session
        }
        videoUri = uri
        videoLabel.text = Media.displayName(this, uri)
    }

    private fun start() {
        val uri = videoUri ?: return toast("Choose a video first")
        if (prefs.apiKey.isBlank()) {
            toast("Add your free Gemini API key first")
            showSettings()
            return
        }
        val langIdx = langSpinner.selectedItemPosition
        prefs.languageIndex = langIdx
        val lang = Languages.get(langIdx)
        val fromSpeech = modeGroup.checkedRadioButtonId == MODE_SPEECH
        val name = Media.displayName(this, uri)

        setBusy(true)
        val p = Pipeline(applicationContext, Gemini(prefs.apiKey, prefs.model)) { msg, f ->
            runOnUiThread {
                status.text = msg
                progress.progress = (f.coerceIn(0.0, 1.0) * 1000).toInt()
            }
        }
        pipeline = p
        thread(name = "subforge-job") {
            try {
                val cues = if (fromSpeech) p.fromSpeech(uri, lang) else p.fromSubtitleTrack(uri, lang)
                if (cues.isEmpty()) throw IllegalStateException("No speech was found in this video.")
                Project(uri, name, langIdx, cues.toMutableList()).save(applicationContext)
                runOnUiThread {
                    setBusy(false)
                    status.text = "Done: ${cues.size} subtitle lines."
                    openEditor()
                }
            } catch (e: InterruptedException) {
                runOnUiThread { setBusy(false); status.text = "Cancelled." }
            } catch (e: Exception) {
                val msg = if (e.message == "Cancelled") "Cancelled." else "Problem: ${e.message ?: e.toString()}"
                runOnUiThread { setBusy(false); status.text = msg }
            }
        }
    }

    private fun setBusy(busy: Boolean) {
        startBtn.isEnabled = !busy
        cancelBtn.visibility = if (busy) View.VISIBLE else View.GONE
        progress.visibility = if (busy) View.VISIBLE else View.GONE
        if (busy) {
            progress.progress = 0
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            pipeline = null
        }
    }

    private fun openEditor() {
        if (!Project.exists(this)) return toast("No subtitles yet")
        startActivity(Intent(this, EditorActivity::class.java))
    }

    private fun showSettings() {
        val pad = dp(20)
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, dp(8), pad, 0) }
        box.addView(TextView(this).apply {
            text = "Get a free key at aistudio.google.com/apikey (sign in with Google → Create API key), then paste it here."
        })
        val keyField = EditText(this).apply {
            hint = "Gemini API key"
            setText(prefs.apiKey)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            isSingleLine = true
        }
        box.addView(keyField)
        box.addView(TextView(this).apply { text = "Model"; setPadding(0, dp(12), 0, 0) })
        val modelField = EditText(this).apply { setText(prefs.model); isSingleLine = true }
        box.addView(modelField)

        AlertDialog.Builder(this)
            .setTitle("Settings")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                prefs.apiKey = keyField.text.toString()
                prefs.model = modelField.text.toString()
                toast("Saved")
            }
            .setNeutralButton("Save & test") { _, _ ->
                prefs.apiKey = keyField.text.toString()
                prefs.model = modelField.text.toString()
                status.text = "Testing your key…"
                val g = Gemini(prefs.apiKey, prefs.model)
                thread {
                    val err = g.test()
                    runOnUiThread { status.text = if (err == null) "Key works ✓" else "Key test failed: $err" }
                }
            }
            .setNegativeButton("Close", null)
            .show()
    }

    // ---------------------------------------------------------------- small helpers

    private fun heading(t: String) = TextView(this).apply {
        text = t
        textSize = 17f
        setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(8), 0, dp(4))
    }

    private fun button(t: String, onClick: () -> Unit) = Button(this).apply {
        text = t
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun space(h: Int) = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(1, dp(h))
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_LONG).show()

    companion object {
        private const val REQ_PICK = 10
        private const val MODE_SPEECH = 101
        private const val MODE_TRACK = 102
    }
}
