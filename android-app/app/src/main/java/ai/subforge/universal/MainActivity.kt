package ai.subforge.universal

import ai.subforge.universal.core.Gemini
import ai.subforge.universal.core.Languages
import ai.subforge.universal.core.Media
import ai.subforge.universal.core.Prefs
import ai.subforge.universal.core.Project
import ai.subforge.universal.core.ProjectStore
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
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
    private lateinit var continueBtn: Button
    private lateinit var openLastBtn: Button
    private lateinit var progress: ProgressBar
    private lateinit var status: TextView

    private var videoUri: Uri? = null
    private val onChange = Runnable { refresh() }

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
        ProjectStore.addListener(onChange)
        refresh()
    }

    override fun onPause() {
        super.onPause()
        ProjectStore.removeListener(onChange)
    }

    /** Shows the live state of the background job. */
    private fun refresh() {
        val running = ProjectStore.running
        val p = if (Project.exists(this)) ProjectStore.get(this) else null
        startBtn.isEnabled = !running
        cancelBtn.visibility = if (running) View.VISIBLE else View.GONE
        progress.visibility = if (running) View.VISIBLE else View.GONE
        if (running) {
            val f = ProjectStore.fraction
            progress.isIndeterminate = f < 0
            if (f >= 0) progress.progress = (f * 1000).toInt()
            status.text = ProjectStore.statusText + "\n\nYou can lock the phone or use other apps. You'll get a notification when it's done."
        } else if (p != null && p.message.isNotBlank()) {
            status.text = p.message
        }
        openLastBtn.visibility = if (p != null && p.cues.isNotEmpty()) View.VISIBLE else View.GONE
        val canContinue = !running && p != null &&
            (p.state == Project.STATE_STOPPED || p.state == Project.STATE_PARTIAL ||
                p.state == Project.STATE_WORKING || p.state == Project.STATE_IMPROVING)
        continueBtn.visibility = if (canContinue) View.VISIBLE else View.GONE
        if (p != null) {
            continueBtn.text = if (p.todoCount() > 0) "Continue unfinished job (${p.videoName})"
            else "Re-check with the best model (${p.videoName})"
        }
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

        col.addView(CheckBox(this).apply {
            text = "Double-check every part with the best model (most accurate, uses about twice as many requests)"
            isChecked = prefs.doubleCheck
            setPadding(0, dp(12), 0, 0)
            setOnCheckedChangeListener { _, on -> prefs.doubleCheck = on }
        })

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
        cancelBtn = button("Cancel") { JobService.cancel(this); status.text = "Cancelling…" }.apply { visibility = View.GONE }
        col.addView(cancelBtn)
        continueBtn = button("Continue") { resumeJob() }.apply { visibility = View.GONE }
        col.addView(continueBtn)

        col.addView(space(24))
        openLastBtn = button("Open subtitles") { openEditor() }
        col.addView(openLastBtn)
        col.addView(button("Settings (Gemini API key, models)") { showSettings() })

        col.addView(TextView(this).apply {
            setPadding(0, dp(20), 0, 0)
            textSize = 13f
            text = "Your video stays on your phone. Only the sound (about 1 MB per 5 minutes) is sent to Google Gemini, " +
                "using your own free API key. Work continues in the background; a 2-hour movie takes roughly 5–20 minutes."
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
            // Keep access after restarts, so the background job and the editor can still read this video.
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (e: SecurityException) {
            // some file pickers do not offer it; access still works while the app is running
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
        val p = if (Project.exists(this)) ProjectStore.get(this) else null
        if (p != null && p.cues.isNotEmpty() && p.state != Project.STATE_DONE) {
            AlertDialog.Builder(this)
                .setMessage("Starting a new video replaces the unfinished subtitles for \"${p.videoName}\". Continue?")
                .setPositiveButton("Start new") { _, _ -> startNow(uri) }
                .setNegativeButton("Keep", null)
                .show()
        } else {
            startNow(uri)
        }
    }

    private fun startNow(uri: Uri) {
        askPermissionsOnce()
        val langIdx = langSpinner.selectedItemPosition
        prefs.languageIndex = langIdx
        val fromSpeech = modeGroup.checkedRadioButtonId == MODE_SPEECH
        ProjectStore.status("Starting…", -1.0)
        JobService.start(this, uri, Media.displayName(this, uri), langIdx, fromSpeech)
        startBtn.isEnabled = false // the service takes over a moment later and updates the screen
        status.text = "Starting…"
    }

    private fun resumeJob() {
        if (prefs.apiKey.isBlank()) return showSettings()
        askPermissionsOnce()
        ProjectStore.status("Continuing…", -1.0)
        JobService.resume(this)
        continueBtn.visibility = View.GONE
        status.text = "Continuing…"
    }

    /** Notifications (Android 13+) and "run in background without limits" so long jobs are not stopped. */
    private fun askPermissionsOnce() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
        }
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!prefs.askedBattery && !pm.isIgnoringBatteryOptimizations(packageName)) {
            prefs.askedBattery = true
            try {
                startActivity(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
                )
            } catch (e: Exception) {
                // some phones do not have this screen
            }
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
            text = "Get a free key at aistudio.google.com/apikey (sign in with Google → Create API key), then paste it here. " +
                "One key works for every model."
        })
        val keyField = EditText(this).apply {
            hint = "Gemini API key"
            setText(prefs.apiKey)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            isSingleLine = true
        }
        box.addView(keyField)
        box.addView(TextView(this).apply { text = "Best model (used first)"; setPadding(0, dp(12), 0, 0) })
        val modelField = EditText(this).apply { setText(prefs.model); isSingleLine = true }
        box.addView(modelField)
        box.addView(TextView(this).apply { text = "Backup models when it is busy (comma separated)"; setPadding(0, dp(12), 0, 0) })
        val backupField = EditText(this).apply { setText(prefs.backupModels); isSingleLine = true }
        box.addView(backupField)
        box.addView(button("Reset models to default") {
            modelField.setText(Prefs.DEFAULT_MODEL)
            backupField.setText(Prefs.DEFAULT_BACKUPS)
        })

        fun save() {
            prefs.apiKey = keyField.text.toString()
            prefs.model = modelField.text.toString()
            prefs.backupModels = backupField.text.toString()
        }

        AlertDialog.Builder(this)
            .setTitle("Settings")
            .setView(ScrollView(this).apply { addView(box) })
            .setPositiveButton("Save") { _, _ -> save(); toast("Saved") }
            .setNeutralButton("Save & test") { _, _ ->
                save()
                status.text = "Testing your key with ${prefs.model}…"
                val g = Gemini(prefs.apiKey)
                val model = prefs.model
                thread {
                    val err = g.test(model)
                    runOnUiThread {
                        status.text = if (err == null) "Key works with $model ✓"
                        else "Test failed: $err\n(If it says busy, the key is fine; Google is just overloaded right now.)"
                    }
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
        private const val REQ_NOTIFY = 11
        private const val MODE_SPEECH = 101
        private const val MODE_TRACK = 102
    }
}
