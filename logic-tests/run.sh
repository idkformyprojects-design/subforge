#!/usr/bin/env bash
# Runs the background-job logic (fallback, re-check, double-check, edits, continue, cancel) against a fake Gemini server.
# Needs: JDK 17, kotlinc 1.9.x on PATH, python3, git. Usage: bash logic-tests/run.sh
set -e
cd "$(dirname "$0")"; W=$(mktemp -d)
git clone -q --depth 1 https://github.com/stleary/JSON-java.git "$W/json"
javac -nowarn -d "$W/cls" $(find "$W/json/src/main/java" -name "*.java") fake/android/*/*.java
python3 -I fake_gemini.py & SRV=$!; trap "kill $SRV" EXIT; sleep 1
C=../android-app/app/src/main/java/ai/subforge/universal/core
kotlinc -nowarn -cp "$W/cls" -d "$W/out" $C/Cue.kt $C/Gemini.kt $C/Project.kt $C/Worker.kt $C/Prefs.kt FakeMedia.kt Bench.kt
java -cp "$W/out:$W/cls:$(dirname "$(readlink -f "$(command -v kotlinc)")")/../lib/kotlin-stdlib.jar" BenchKt
