package com.voiney.lab.session;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * VoineySessionStore: the app's login token in the phone's secure store
 * (lane APP1, decision 3). Called by static/native-bridge.js (createAppSession).
 *
 * The token is encrypted with an AES-256-GCM key that lives in the Android
 * Keystore and never leaves it; the app's private preferences hold only the
 * ciphertext and its IV (and android:allowBackup="false" keeps them off cloud
 * backups). A token that no longer decrypts (the key was lost, e.g. after a
 * device reset of the lock screen) is cleared, which only means logging in
 * again. Nothing here logs the token.
 */
@CapacitorPlugin(name = "VoineySessionStore")
public class SessionStorePlugin extends Plugin {

    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String KEY_ALIAS = "voiney_app_session_key";
    private static final String FILE = "voiney_app_session";
    private static final String ENTRY = "session";
    private static final int TAG_BITS = 128;

    /** {token} -> {} */
    @PluginMethod
    public void set(PluginCall call) {
        String token = call.getString("token");
        if (token == null || token.isEmpty() || token.length() > 200) {
            call.reject("token is required", "invalid_token");
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] sealed = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
            String stored =
                Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":" + Base64.encodeToString(sealed, Base64.NO_WRAP);
            preferences().edit().putString(ENTRY, stored).commit();
            call.resolve();
        } catch (Exception error) {
            call.reject("the secure store refused", "secure_store_unavailable");
        }
    }

    /** -> {token: string | null} */
    @PluginMethod
    public void get(PluginCall call) {
        JSObject result = new JSObject();
        String stored = preferences().getString(ENTRY, null);
        String token = null;
        if (stored != null) {
            try {
                String[] parts = stored.split(":", 2);
                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(
                    Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(TAG_BITS, Base64.decode(parts[0], Base64.NO_WRAP)));
                token = new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
            } catch (Exception unreadable) {
                preferences().edit().remove(ENTRY).commit();
            }
        }
        result.put("token", token);
        call.resolve(result);
    }

    @PluginMethod
    public void clear(PluginCall call) {
        preferences().edit().remove(ENTRY).commit();
        call.resolve();
    }

    private SharedPreferences preferences() {
        return getContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    private static SecretKey key() throws Exception {
        KeyStore keyStore = KeyStore.getInstance(KEYSTORE);
        keyStore.load(null);
        KeyStore.Entry entry = keyStore.getEntry(KEY_ALIAS, null);
        if (entry instanceof KeyStore.SecretKeyEntry) return ((KeyStore.SecretKeyEntry) entry).getSecretKey();
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        generator.init(
            new KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return generator.generateKey();
    }
}
