# Notes for whoever works on this next

Read this before changing anything.

## State (v1.2.0)
- The app was rewritten with plain Android Views + FFmpegKit only. All Kotlin compiles against the
  real android-34 API and the real FFmpegKit classes (extracted from the earlier APK). SRT logic is unit-tested.
  All FFmpeg commands were tested on a sample video with desktop FFmpeg, including Bengali/Arabic burn-in.
- v1.1.0 was installed and run on the owner's phone: picking video, audio extraction and Gemini calls work.
- v1.2.0 adds the background service, backup models, automatic re-check and double-check.
  The job logic is tested with logic-tests/ (10 scenarios against a fake Gemini server). Run it after every change.
- Signing: the owner's release key is NOT in the repo. Until the 4 GitHub secrets are added, CI APKs are
  debug-signed and must be re-signed with the owner's key before they install over the current app.

## Why the old version was thrown away
- The old manifest launched `.MainActivity`, but the class lived in `.ui` and was missing from the APK,
  so the app crashed on launch. Most buttons were empty `onClick = {}`.
- ~60 build attempts were spent downgrading versions (compileSdk 35→34→33, Media3 1.11.1→1.0.0) and
  removing features to get a green build.

## Rules to avoid the same loop
1. Do not add Compose, Media3, Retrofit or other libraries unless a feature truly needs it.
2. If the SDK platform is missing, install it. Do not lower compileSdk.
3. Keep `org.gradle.jvmargs=-Xmx1g`. 256m crashes the daemon.
4. A green build is not "done". Done = it opens on a phone and the feature works end to end.
5. Never commit keystores or passwords (see `.gitignore`).

## Next features, in order
1. Burned-in subtitle OCR: crop the bottom strip at ~2 fps with FFmpeg, send strips to Gemini vision.
2. Video preview with subtitles in the editor.
