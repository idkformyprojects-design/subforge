package android.content;
public interface SharedPreferences {
  String getString(String k, String d); int getInt(String k, int d); boolean getBoolean(String k, boolean d);
  Editor edit();
  interface Editor { Editor putString(String k, String v); Editor putInt(String k, int v); Editor putBoolean(String k, boolean v); void apply(); }
}
