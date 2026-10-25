import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { runApi, voicePreview } from "../../api/runApi";
import { LoadingBox, Note } from "../../components/Pieces";
import { settings as copy } from "../../copy/ko";

// "안내 목소리" (person's decision 5): picking a voice saves it at once
// ("저장했어요") and plays it. One preview plays at a time -- a new press
// stops the one before, the same button again stops it, and a spent sound's
// address is let go (the old screen's lesson, lane IA bug ①).

export const SETTINGS_KEY = ["experimenter", "settings"] as const;

export function VoicePicker() {
  const client = useQueryClient();
  const voices = useQuery({ queryKey: ["voice", "voices"], queryFn: runApi.voices, retry: false });
  const settings = useQuery({ queryKey: SETTINGS_KEY, queryFn: runApi.settings, retry: false });
  const [playing, setPlaying] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const player = useRef<{ audio: HTMLAudioElement | null; url: string | null; token: number }>({ audio: null, url: null, token: 0 });

  function stopPreview() {
    player.current.token += 1;
    const { audio, url } = player.current;
    player.current.audio = null;
    player.current.url = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
    }
    if (url) URL.revokeObjectURL(url);
    setPlaying(null);
  }
  useEffect(() => stopPreview, []);

  async function preview(voiceId: string) {
    if (playing === voiceId) {
      stopPreview();
      return;
    }
    stopPreview();
    const token = player.current.token;
    setPlaying(voiceId);
    setNote(null);
    try {
      const blob = await voicePreview(voiceId);
      if (token !== player.current.token) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      player.current.audio = audio;
      player.current.url = url;
      audio.onended = () => {
        if (player.current.audio === audio) stopPreview();
      };
      audio.onerror = () => {
        if (player.current.audio !== audio) return;
        stopPreview();
        setNote(copy.agentVoice.previewFailed);
      };
      await audio.play();
    } catch {
      if (token === player.current.token) {
        stopPreview();
        setNote(copy.agentVoice.previewFailed);
      }
    }
  }

  async function pick(voiceId: string) {
    setNote(null);
    setSaved(null);
    try {
      const next = await runApi.saveSetting("agent_voice", voiceId);
      client.setQueryData(SETTINGS_KEY, next);
      setSaved(voiceId);
      void preview(voiceId);
    } catch {
      setNote(copy.saveFailed);
    }
  }

  if (voices.isPending || settings.isPending) return <LoadingBox />;
  if (voices.isError || !voices.data?.voices?.length) return <p className="muted">{copy.agentVoice.unavailable}</p>;
  const selected = settings.data?.agent_voice && settings.data.agent_voice !== "default" ? settings.data.agent_voice : voices.data.default_voice_id;
  return (
    <div className="voice-picker" data-testid="voice-picker">
      {voices.data.preview_text_ko ? <p className="muted">“{voices.data.preview_text_ko}”</p> : null}
      <ul className="choice-list">
        {voices.data.voices.map((voice) => (
          <li key={voice.voice_id} className="choice-row">
            <button type="button" className="choice" aria-pressed={voice.voice_id === selected} onClick={() => void pick(voice.voice_id)} data-voice={voice.voice_id}>
              {voice.voice_id === selected ? <Check aria-hidden="true" /> : <span className="choice-dot" aria-hidden="true" />}
              <span className="choice-label">{voice.label_ko}</span>
              {voice.voice_id === voices.data.default_voice_id ? <span className="muted"> · {copy.agentVoice.defaultVoice}</span> : null}
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => void preview(voice.voice_id)} aria-label={`${voice.label_ko} ${copy.agentVoice.preview}`}>
              <Volume2 aria-hidden="true" />
              <span>{playing === voice.voice_id ? copy.agentVoice.previewing : copy.agentVoice.preview}</span>
            </button>
            {saved === voice.voice_id ? (
              <span className="saved" role="status" data-testid="voice-saved">
                {copy.saved}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {note ? <Note>{note}</Note> : null}
    </div>
  );
}
