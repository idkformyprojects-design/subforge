# FFmpegKit calls back into Java from native code, so its classes must keep their names.
-keep class com.arthenica.ffmpegkit.** { *; }
-keep class com.arthenica.smartexception.** { *; }
-dontwarn com.arthenica.**
