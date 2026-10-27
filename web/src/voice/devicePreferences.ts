export const DEVICE_KEYS = { language: 'voiney-lab.input-language', gain: 'voiney-lab.agc-mode', microphone: 'voiney-lab.mic-device' } as const;
export function deviceValue(key: string, options: readonly string[], fallback: string): string {
 try { const value = localStorage.getItem(key); return value && options.includes(value) ? value : fallback; } catch { return fallback; }
}
export function inputLanguage(): string { return deviceValue(DEVICE_KEYS.language, ['ko', 'auto', 'en'], 'ko'); }
export function storedMicrophone(): { deviceId: string; label: string } | null {
 try { const value = JSON.parse(localStorage.getItem(DEVICE_KEYS.microphone) || 'null'); return value && typeof value.deviceId === 'string' && typeof value.label === 'string' ? value : null; } catch { return null; }
}
