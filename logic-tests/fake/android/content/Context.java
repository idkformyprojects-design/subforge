package android.content;
import java.io.File; import java.util.HashMap;
public class Context {
  public static final int MODE_PRIVATE = 0;
  private final File root; public Context(File root) { this.root = root; new File(root,"files").mkdirs(); new File(root,"cache").mkdirs(); }
  public Context getApplicationContext() { return this; }
  public File getFilesDir() { return new File(root, "files"); }
  public File getCacheDir() { return new File(root, "cache"); }
  final HashMap<String,Object> m = new HashMap<>();
  public SharedPreferences getSharedPreferences(String n, int mode) {
    return new SharedPreferences() {
      public String getString(String k, String d) { Object v = m.get(k); return v == null ? d : (String) v; }
      public int getInt(String k, int d) { Object v = m.get(k); return v == null ? d : (Integer) v; }
      public boolean getBoolean(String k, boolean d) { Object v = m.get(k); return v == null ? d : (Boolean) v; }
      public Editor edit() { return new Editor() {
        public Editor putString(String k, String v) { m.put(k, v); return this; }
        public Editor putInt(String k, int v) { m.put(k, v); return this; }
        public Editor putBoolean(String k, boolean v) { m.put(k, v); return this; }
        public void apply() {} }; }
    };
  }
}
