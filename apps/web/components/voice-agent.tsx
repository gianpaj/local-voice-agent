"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { BotOutputData, PipecatClient } from "@pipecat-ai/client-js";
import { BrowserMediaManager } from "./browser-media";

type Line = { id: string; role: "user" | "assistant"; text: string; status?: string };
type Health = { ready: boolean; busy: boolean; phase: string; error?: string };

export default function VoiceAgent() {
  const [health, setHealth] = useState<Health>({ ready: false, busy: false, phase: "initializing" });
  const [phase, setPhase] = useState("ready");
  const [connecting, setConnecting] = useState(false);
  const [connected, setConnected] = useState(false);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState("");
  const [playbackBlocked, setPlaybackBlocked] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [mic, setMic] = useState("");
  const [speakers, setSpeakers] = useState<MediaDeviceInfo[]>([]);
  const [speaker, setSpeaker] = useState("");
  const [level, setLevel] = useState(0);
  const client = useRef<PipecatClient | null>(null);
  const media = useRef<BrowserMediaManager | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const active = useRef(false);
  const operation = useRef(0);
  const end = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try { const response = await fetch("/api/health", { cache: "no-store" }); const data = await response.json(); if (alive) setHealth(data); }
      catch { if (alive) setHealth({ ready: false, busy: false, phase: "offline" }); }
    };
    void check(); const timer = setInterval(check, 2500);
    return () => { alive = false; clearInterval(timer); operation.current++; void client.current?.disconnect(); void media.current?.disconnect(); };
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [lines]);

  const stop = useCallback(async () => {
    operation.current++;
    active.current = false;
    const current = client.current; client.current = null;
    const manager = media.current; media.current = null;
    try { await current?.disconnect(); } finally {
      await manager?.disconnect();
      if (audio.current) { audio.current.pause(); audio.current.srcObject = null; }
      setConnected(false); setConnecting(false); setMuted(false); setPhase("ready"); setLines([]); setLevel(0);
    }
  }, []);

  const start = async () => {
    if (client.current) return;
    const ticket = ++operation.current;
    setError(""); setPlaybackBlocked(false); setLines([]); setConnecting(true); setPhase("connecting");
    try {
      if (!window.isSecureContext || !navigator.mediaDevices) throw new Error("Microphone access needs HTTPS (Tailscale Serve) or localhost.");
      const [{ PipecatClient, logger, LogLevel }, { SmallWebRTCTransport }] = await Promise.all([
        import("@pipecat-ai/client-js"), import("@pipecat-ai/small-webrtc-transport"),
      ]);
      logger.setLevel(LogLevel.WARN);
      const manager = new BrowserMediaManager(() => audio.current);
      media.current = manager;
      const transport = new SmallWebRTCTransport({ iceServers: [], mediaManager: manager });
      // The pinned transport's custom-manager hook needs explicit sender replacement.
      // Its runtime method is omitted from its published declaration; browser tests cover it.
      manager.replaceTrack = track => (transport as typeof transport & { getAudioTransceiver(): RTCRtpTransceiver }).getAudioTransceiver().sender.replaceTrack(track);
      const current = new PipecatClient({
        transport, enableMic: true, enableCam: false,
        callbacks: {
          onBotReady: () => { active.current = true; setConnected(true); setConnecting(false); setPhase("listening"); },
          // A remote close must release the same resources as the Stop button.
          // Ignore a late callback from an older client after a new Start.
          onDisconnected: () => { if (client.current === current) void stop(); },
          onError: (message) => { setError(String((message.data as { error?: string })?.error || "Voice session error. Stop and reconnect.")); setPhase("error"); },
          onDeviceError: (event) => { setError(event.message || "Microphone unavailable. Check browser permissions."); setPhase("error"); },
          onAvailableMicsUpdated: setMics,
          onAvailableSpeakersUpdated: setSpeakers,
          onMicUpdated: (device) => setMic(device.deviceId),
          onLocalAudioLevel: setLevel,
          onTrackStarted: (track, participant) => {
            // SmallWebRTC remote callbacks omit participant; local callbacks mark local=true.
            if (track.kind === "audio" && participant?.local !== true && audio.current) {
              audio.current.srcObject = new MediaStream([track]);
              void audio.current.play().catch(() => { setPlaybackBlocked(true); setError("Audio playback was blocked. Press Enable sound below."); });
            }
          },
          onUserStartedSpeaking: () => setPhase("listening"),
          onUserStoppedSpeaking: () => setPhase("transcribing"),
          onBotLlmStarted: () => setPhase("thinking"),
          onBotStartedSpeaking: () => setPhase("speaking"),
          onBotStoppedSpeaking: () => { if (active.current) setPhase("listening"); },
          onBotInterrupted: () => { setPhase(previous => ["speaking", "thinking"].includes(previous) ? "interrupted" : "listening"); setLines(old => old.map(line => line.role === "assistant" && line.status !== "completed" ? { ...line, status: "interrupted" } : line)); },
          onServerMessage: (data) => { if (data?.phase) setPhase(data.phase); },
          onUserInput: (data) => { if (data.final && data.text.trim()) setLines(old => [...old, { id: crypto.randomUUID(), role: "user" as const, text: data.text }].slice(-60)); },
          onBotOutput: (data: BotOutputData) => {
            if (!data.text || data.will_be_spoken === false) return;
            const id = `assistant-${data.segment_id ?? "current"}`;
            setLines(old => {
              const found = old.findIndex(line => line.id === id);
              const line: Line = { id, role: "assistant", text: data.text, status: data.spoken_status || "new" };
              return (found < 0 ? [...old, line] : old.map((item, index) => index === found ? line : item)).slice(-60);
            });
          },
        },
      });
      client.current = current;
      await current.initDevices();
      if (ticket !== operation.current) { await current.disconnect(); await manager.disconnect(); return; }
      setMics(await current.getAllMics()); setSpeakers(await current.getAllSpeakers());
      // Request browser AEC/noise reduction on the actual captured track when supported.
      const localTrack = current.tracks().local.audio;
      if (localTrack) await localTrack.applyConstraints({ echoCancellation: true, noiseSuppression: true, autoGainControl: true }).catch(() => {});
      await current.connect({ webrtcRequestParams: { endpoint: "/api/offer" } });
      if (ticket !== operation.current) { await current.disconnect(); await manager.disconnect(); }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      await stop(); setError(message); setPhase("error");
    } finally { if (ticket === operation.current) setConnecting(false); }
  };

  const displayPhase = !health.ready && !connected ? health.phase : phase;
  return <main>
    <header><a className="brand" href="/">◉ <span>local<span className="brand-light">voice</span></span></a><div className="privacy"><span className="dot" />Private by design</div></header>
    <section className="intro"><span className="eyebrow">YOUR COMPUTER. YOUR CONVERSATION.</span><h1>A little less typing.<br /><span>A lot more talking.</span></h1><p>An English voice companion, powered by models on your own computer. No cloud inference. Just a conversation.</p></section>
    <div className="workspace">
      <section className="session-card" aria-label="Voice controls">
        <div className="card-top"><span className="eyebrow">VOICE SESSION</span><span className={`state ${connected ? "live" : ""}`}><i />{displayPhase}</span></div>
        <div className={`orb-wrap ${phase === "speaking" ? "speaking" : ""} ${connected ? "active" : ""}`} style={{ "--level": Math.min(1, level) } as React.CSSProperties}><div className="orb"><span /><span /><span /><span /><span /></div></div>
        <h2>{connected ? muted ? "Take your time." : phase === "speaking" ? "Here’s a thought." : "I’m listening." : health.ready ? "Ready when you are." : "Getting things ready…"}</h2>
        <p className="session-hint">{connected ? "Speak naturally. You can interrupt a reply at any time." : "Your browser’s microphone and speaker are used."}</p>
        <div className="controls">{connected || connecting ? <><button className="button secondary" disabled={connecting} onClick={() => { const next = !muted; client.current?.enableMic(!next); setMuted(next); }}>{muted ? "Unmute mic" : "Mute mic"}</button><button className="button stop" onClick={() => void stop()}>Stop session</button></> : <button className="button primary" onClick={() => void start()} disabled={!health.ready || health.busy}>↗ {health.busy ? "Agent is in use" : "Start talking"}</button>}</div>
        {(error || health.error) && <div className="error" role="alert">{error || health.error}{playbackBlocked && <button onClick={() => void audio.current?.play().then(() => { setError(""); setPlaybackBlocked(false); }).catch(() => {})}>Enable sound</button>}</div>}
        <div className="devices"><label>Microphone<select value={mic} disabled={!connected} onChange={e => { const id = e.target.value; void media.current?.updateMic(id).then(() => setMic(id)).catch(cause => setError(String(cause))); }}><option value="">System default</option>{mics.map(device => <option key={device.deviceId} value={device.deviceId}>{device.label || "Microphone"}</option>)}</select></label><label>Speaker<select value={speaker} disabled={!connected} onChange={e => { const id = e.target.value; void media.current?.updateSpeaker(id).then(() => setSpeaker(id)).catch(cause => setError(String(cause))); }}><option value="">System default</option>{speakers.map(device => <option key={device.deviceId} value={device.deviceId}>{device.label || "Speaker"}</option>)}</select></label></div>
        <div className="model-strip"><span>Parakeet <small>ears</small></span><span>Qwen <small>thoughts</small></span><span>Phonon <small>voice</small></span></div>
      </section>
      <section className="transcript-card" aria-label="Conversation"><div className="card-top"><span className="eyebrow">THE CONVERSATION</span><span className="session-only">Session only</span></div><div className="transcript" aria-live="polite">{lines.length ? lines.map(line => <article key={line.id} className={`message ${line.role}`}><div className="speaker">{line.role === "user" ? "YOU" : "LOCAL VOICE"}{line.status === "interrupted" && <span> · interrupted</span>}</div><p>{line.text}</p></article>) : <div className="empty"><span>“</span><h3>Good conversations<br />start with hello.</h3><p>Your words and replies appear here.<br />Nothing is recorded or saved.</p></div>}<div ref={end} /></div><div className="transcript-footer">English · Pause between thoughts · Stop clears the conversation</div></section>
    </div><footer>One browser. Three local models. All yours.<span>LOCAL VOICE / 01</span></footer><audio ref={audio} autoPlay playsInline />
  </main>;
}
