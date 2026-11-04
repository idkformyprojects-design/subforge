# SubForge (v1.2.0)

Android app that makes subtitles for any video, in any language, using your own **free** Google Gemini key.

## What works in this version

1. **Choose a video** (MP4, MKV, MOV, WebM… through Android's file picker).
2. **Make subtitles from**
   - **Speech in the video**: the phone pulls out the sound (16 kHz mono MP3, ~1.2 MB per 5 minutes) and sends it to Gemini in 5-minute parts. Gemini writes timed subtitles, translated into the language you pick (or kept in the original language).
   - **Existing subtitle track** (SRT/ASS/VTT/mov_text inside an MKV/MP4): copied out and translated.
3. **Edit** every line (text, start/end time), add or delete lines, shift all timings.
4. **Save** a `.srt` file, or **export the video**:
   - MP4 + subtitle track (fast, no quality loss)
   - MKV + subtitle track (fast, works with any audio format)
   - Burn subtitles into the picture (slow, but shows on every player, WhatsApp, etc.)

The video never leaves the phone. Only the sound goes to Gemini.

### New in v1.2.0
- **Runs in the background** with a progress notification and a Cancel button. Lock the phone or use other apps.
- **Never gives up because Google is busy.** The best model is tried first; if it stays busy, a backup model
  (Settings → backup models) makes that part so the job keeps going.
- **Automatic re-check.** Parts made by a backup model are redone by the best model as soon as it is free
  (it keeps trying for up to 45 minutes; "Re-check now" in the editor tries again later).
- **Double-check (optional switch).** The best model listens to every part again next to the finished subtitles
  and fixes misheard words, names, missing lines, timing and translation. Uses about twice the requests.
- **Notifications:** "Subtitles ready" when you can start editing, and "All done ✓" when re-checks finish.
- **Continue after interruptions.** Every finished part is saved; "Continue" picks up where it stopped.
- **Your edits are safe.** Background re-checks never overwrite a part you edited by hand.
- Editor marks: ✓ corrected by the best model · ⚠ made by a backup model · ✎ edited by you.

## Not in this version (yet)

- Reading subtitles that are burned into the picture (OCR)
- Picture-based DVD/Blu-ray subtitle tracks

## Setup on the phone

1. Get a free key: <https://aistudio.google.com/apikey> → **Create API key**.
2. Open SubForge → **Settings** → paste the key → **Save & test**.
3. Default models: best `gemini-flash-latest`, backups `gemini-flash-latest, gemini-flash-lite-latest`.
   "-latest" names follow Google's newest model, so they do not get retired. One key works for all models.
4. When asked, allow notifications and "run in background without restrictions" so long jobs are not stopped.

## Build the APK

### Easiest: GitHub (free, nothing to install)
1. Create a GitHub repository and upload this whole folder (the `.github` folder must be at the top level).
2. Open the **Actions** tab → **Build APK** → **Run workflow**.
3. After ~5 minutes, download **SubForge-APK** from the finished run and install it on your phone.

Without signing secrets the APK is signed with a debug key. That's fine for your own phone. To publish
updates that install over each other, add the 4 secrets listed in `.github/workflows/build-apk.yml`.

### On a computer
```bash
cd android-app
./gradlew :app:assembleRelease      # needs JDK 17 and Android SDK 34 (ANDROID_HOME set)
# APK: app/build/outputs/apk/release/
```

## Project layout

```
android-app/app/src/main/java/ai/subforge/universal/
  MainActivity.kt      choose video, mode, language, settings; runs the job
  EditorActivity.kt    edit lines, save .srt, export video
  core/Media.kt        all FFmpeg work (audio clips, subtitle track, mux, burn-in)
  core/Gemini.kt       Gemini calls (transcribe audio clip, translate lines), retries + free-tier pacing
  JobService.kt        background service: notifications, cancel, wake lock
  core/Worker.kt       the job: parts, best/backup models, re-check, double-check, continue
  core/Project.kt      the saved project (parts + lines) and the shared ProjectStore
  core/Cue.kt          subtitle line + SRT read/write
  core/Prefs.kt        settings + language list
logic-tests/           runs the job logic against a fake Gemini server (bash logic-tests/run.sh)
backend/               optional Python server from the first plan; the app does NOT need it
```

Only one library: `dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.7` (LGPL). No Compose, Media3 or Retrofit,
which keeps the build small and fast.
