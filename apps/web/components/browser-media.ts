import type { PipecatClientOptions, Tracks } from "@pipecat-ai/client-js";

/** Microphone/device management using browser APIs only: no Daily iframe/CDN. */
export class BrowserMediaManager {
  private options: PipecatClientOptions | undefined;
  private stream: MediaStream | undefined;
  private micEnabled = true;
  private mic: MediaDeviceInfo | Record<string, never> = {};
  private speaker: MediaDeviceInfo | Record<string, never> = {};
  private context: AudioContext | undefined;
  private meter: ReturnType<typeof setInterval> | undefined;
  replaceTrack: ((track: MediaStreamTrack) => Promise<void>) | undefined;
  readonly supportsScreenShare = false;
  readonly mediaSupport = { mic: true, cam: false, screenShare: false };
  constructor(private playback: () => HTMLAudioElement | null) {}
  setClientOptions(options: PipecatClientOptions) { this.options = options; this.micEnabled = options.enableMic ?? true; }
  setUserAudioCallback() {} // SmallWebRTC sends the native track, not PCM callbacks.
  private changed = () => { void this.refreshDevices(); };
  private async refreshDevices() {
    const devices = await navigator.mediaDevices.enumerateDevices();
    this.options?.callbacks?.onAvailableMicsUpdated?.(devices.filter(d => d.kind === "audioinput"));
    this.options?.callbacks?.onAvailableSpeakersUpdated?.(devices.filter(d => d.kind === "audiooutput"));
    const mic = devices.find(d => d.deviceId === this.stream?.getAudioTracks()[0]?.getSettings().deviceId);
    this.mic = mic || {};
    if (mic) this.options?.callbacks?.onMicUpdated?.(mic);
  }
  async initialize() {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    navigator.mediaDevices.addEventListener("devicechange", this.changed);
    this.stream.getAudioTracks()[0].enabled = this.micEnabled;
    await this.refreshDevices();
    await this.startMeter();
    this.options?.callbacks?.onTrackStarted?.(this.stream.getAudioTracks()[0], { id: "local", name: "", local: true });
  }
  private async startMeter() {
    clearInterval(this.meter);
    await this.context?.close();
    if (!this.stream) return;
    this.context = new AudioContext();
    const analyzer = this.context.createAnalyser(); analyzer.fftSize = 256;
    this.context.createMediaStreamSource(this.stream).connect(analyzer);
    const data = new Uint8Array(analyzer.fftSize);
    this.meter = setInterval(() => {
      analyzer.getByteTimeDomainData(data);
      const rms = Math.sqrt(data.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / data.length);
      this.options?.callbacks?.onLocalAudioLevel?.(this.micEnabled ? Math.min(1, rms * 4) : 0);
    }, 80);
  }
  async connect() { await this.initialize(); }
  async disconnect() {
    navigator.mediaDevices.removeEventListener("devicechange", this.changed);
    clearInterval(this.meter);
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
    const context = this.context; this.context = undefined;
    if (context && context.state !== "closed") await context.close();
  }
  async getAllMics() { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput"); }
  async getAllCams() { return []; }
  async getAllSpeakers() { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audiooutput"); }
  async updateMic(id: string) {
    const next = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: id ? { exact: id } : undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    const track = next.getAudioTracks()[0]; track.enabled = this.micEnabled;
    try { await this.replaceTrack?.(track); }
    catch (error) { next.getTracks().forEach(t => t.stop()); throw error; }
    this.stream?.getTracks().forEach(t => t.stop()); this.stream = next;
    await this.refreshDevices(); await this.startMeter();
  }
  updateCam() {}
  async updateSpeaker(id: string) {
    const element = this.playback();
    if (!element || !("setSinkId" in element)) throw new Error("This browser cannot select a speaker; use its system default.");
    await (element as HTMLAudioElement & { setSinkId: (id: string) => Promise<void> }).setSinkId(id);
    this.speaker = (await this.getAllSpeakers()).find(d => d.deviceId === id) || {};
  }
  get selectedMic() { return this.mic; }
  get selectedCam() { return {}; }
  get selectedSpeaker() { return this.speaker; }
  enableMic(enabled: boolean) { this.micEnabled = enabled; this.stream?.getAudioTracks().forEach(t => { t.enabled = enabled; }); }
  enableCam() {}
  async enableScreenShare() { throw new Error("This voice-only app does not capture screens."); }
  get isCamEnabled() { return false; }
  get isMicEnabled() { return this.micEnabled; }
  get isSharingScreen() { return false; }
  tracks(): Tracks { return { local: { audio: this.stream?.getAudioTracks()[0] }, bot: {} }; }
}
