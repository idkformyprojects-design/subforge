package ai.subforge.universal.core

import android.content.Context

/** Small settings store: Gemini key, model and the last chosen language. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("subforge", Context.MODE_PRIVATE)

    var apiKey: String
        get() = sp.getString("api_key", "") ?: ""
        set(v) { sp.edit().putString("api_key", v.trim()).apply() }

    /** Model names change over time, so the user can edit this in Settings. */
    var model: String
        get() = (sp.getString("model", DEFAULT_MODEL) ?: DEFAULT_MODEL).ifBlank { DEFAULT_MODEL }
        set(v) { sp.edit().putString("model", v.trim()).apply() }

    /** Models to use when the main one is busy, comma separated, best first. */
    var backupModels: String
        get() = sp.getString("backup_models", DEFAULT_BACKUPS) ?: DEFAULT_BACKUPS
        set(v) { sp.edit().putString("backup_models", v.trim()).apply() }

    fun backupList(): List<String> =
        backupModels.split(',', ' ', '\n').map { it.trim() }.filter { it.isNotEmpty() && it != model }.distinct()

    /** Second listen by the best model to correct mistakes. About twice the requests. */
    var doubleCheck: Boolean
        get() = sp.getBoolean("double_check", false)
        set(v) { sp.edit().putBoolean("double_check", v).apply() }

    var askedBattery: Boolean
        get() = sp.getBoolean("asked_battery", false)
        set(v) { sp.edit().putBoolean("asked_battery", v).apply() }

    var languageIndex: Int
        get() = sp.getInt("lang_idx", 1)
        set(v) { sp.edit().putInt("lang_idx", v).apply() }

    companion object {
        /** "-latest" names always point to Google's current model, so they do not get retired. */
        const val DEFAULT_MODEL = "gemini-flash-latest"
        const val DEFAULT_BACKUPS = "gemini-flash-latest, gemini-flash-lite-latest"
    }
}

/** Target languages shown in the picker. "Original" means transcribe without translating. */
object Languages {
    class Lang(val name: String, val iso639_2: String)

    val ALL: List<Lang> = listOf(
        Lang("Original language (no translation)", "und"),
        Lang("English", "eng"), Lang("Bengali", "ben"), Lang("Hindi", "hin"), Lang("Urdu", "urd"),
        Lang("Arabic", "ara"), Lang("Assamese", "asm"), Lang("Gujarati", "guj"), Lang("Kannada", "kan"),
        Lang("Malayalam", "mal"), Lang("Marathi", "mar"), Lang("Nepali", "nep"), Lang("Odia", "ori"),
        Lang("Punjabi", "pan"), Lang("Tamil", "tam"), Lang("Telugu", "tel"), Lang("Sinhala", "sin"),
        Lang("Persian", "per"), Lang("Turkish", "tur"), Lang("Hebrew", "heb"),
        Lang("Chinese (Simplified)", "chi"), Lang("Chinese (Traditional)", "chi"), Lang("Japanese", "jpn"),
        Lang("Korean", "kor"), Lang("Thai", "tha"), Lang("Vietnamese", "vie"), Lang("Indonesian", "ind"),
        Lang("Malay", "may"), Lang("Filipino", "fil"), Lang("Burmese", "bur"),
        Lang("Spanish", "spa"), Lang("Portuguese", "por"), Lang("French", "fre"), Lang("German", "ger"),
        Lang("Italian", "ita"), Lang("Dutch", "dut"), Lang("Polish", "pol"), Lang("Russian", "rus"),
        Lang("Ukrainian", "ukr"), Lang("Greek", "gre"), Lang("Swedish", "swe"), Lang("Romanian", "rum"),
        Lang("Swahili", "swa"), Lang("Amharic", "amh"), Lang("Hausa", "hau"), Lang("Yoruba", "yor"),
    )

    fun get(index: Int): Lang = ALL[index.coerceIn(0, ALL.size - 1)]
    fun isOriginal(lang: Lang): Boolean = lang.iso639_2 == "und"
}
