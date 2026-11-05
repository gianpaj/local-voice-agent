//! Isolated, headless Rustvani pipeline using unchanged local model adapters.
//! No sockets, no cloud defaults, no stored conversation or audio.
use async_trait::async_trait;
use base64::{engine::general_purpose::STANDARD, Engine};
use rustvani::{
    error::{PipecatError, Result},
    frames::{
        AudioRawData, ControlFrame, DataFrame, Frame, FrameDirection, FrameHandler, FrameInner,
        FrameProcessor, TranscriptionData,
    },
    system_clock, PipelineParams, PipelineTask,
};
use serde_json::{json, Value};
use std::{
    env,
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex as StdMutex,
    },
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{Mutex, Notify},
};

const REV: &str = "b134ad055c754efaa690fec72f530d5390fd6180";
const MAX_LINE: usize = 2_000_000;
fn err(message: &str) -> PipecatError {
    PipecatError::pipeline(message)
}
fn ms(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}
struct Bridge {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}
impl Bridge {
    async fn line(&mut self) -> Result<Value> {
        let mut bytes = Vec::new();
        // read_until alone has an unbounded allocation on malformed IPC.
        loop {
            let available = self
                .output
                .fill_buf()
                .await
                .map_err(|_| err("IPC read failed"))?;
            if available.is_empty() {
                return Err(err("IPC closed"));
            }
            let take = available
                .iter()
                .position(|b| *b == b'\n')
                .map(|n| n + 1)
                .unwrap_or(available.len());
            if bytes.len() + take > MAX_LINE {
                return Err(err("IPC line too large"));
            }
            let complete = available[take - 1] == b'\n';
            bytes.extend_from_slice(&available[..take]);
            self.output.consume(take);
            if complete {
                break;
            }
        }
        let value: Value = serde_json::from_slice(&bytes).map_err(|_| err("Invalid IPC JSON"))?;
        if value["event"] == "error" {
            return Err(err("Model adapter failed"));
        }
        Ok(value)
    }
    async fn send(&mut self, request: Value) -> Result<()> {
        let mut bytes = serde_json::to_vec(&request).map_err(|_| err("IPC encode failed"))?;
        if bytes.len() > MAX_LINE {
            return Err(err("IPC request too large"));
        }
        bytes.push(b'\n');
        self.input
            .write_all(&bytes)
            .await
            .map_err(|_| err("IPC write failed"))
    }
}
struct State {
    started: Instant,
    trace: StdMutex<Value>,
    bridge: Mutex<Bridge>,
    done: Notify,
}
impl State {
    fn event(&self, key: &str) {
        self.trace.lock().unwrap()[key] = json!(ms(self.started));
    }
    fn value(&self, key: &str, value: Value) {
        self.trace.lock().unwrap()[key] = value;
    }
}
struct Stt(Arc<State>);
#[async_trait]
impl FrameHandler for Stt {
    async fn on_process_frame(
        &self,
        p: &FrameProcessor,
        frame: Frame,
        d: FrameDirection,
    ) -> Result<()> {
        if let FrameInner::System(rustvani::frames::SystemFrame::InputAudioRaw(audio)) =
            &frame.inner
        {
            self.0.event("stt_handler_start_ms");
            let mut bridge = self.0.bridge.lock().await;
            bridge
                .send(json!({"op":"stt","pcm":STANDARD.encode(&audio.audio)}))
                .await?;
            let mut transcript = None;
            loop {
                let v = bridge.line().await?;
                match v["event"].as_str() {
                    Some("transcript") => transcript = v["text"].as_str().map(str::to_owned),
                    Some("done") => {
                        self.0
                            .value("stt_model_adapter_ms", v["duration_ms"].clone());
                        break;
                    }
                    _ => {}
                }
            }
            self.0.event("stt_handler_end_ms");
            drop(bridge);
            let text = transcript.ok_or_else(|| err("Missing transcript"))?;
            p.push_frame(
                Frame::transcription(
                    TranscriptionData::new(text, "public-fixture", "")
                        .with_language("en")
                        .finalized(),
                ),
                d,
            )
            .await
        } else {
            p.push_frame(frame, d).await
        }
    }
}
struct Llm(Arc<State>);
#[async_trait]
impl FrameHandler for Llm {
    async fn on_process_frame(
        &self,
        p: &FrameProcessor,
        frame: Frame,
        d: FrameDirection,
    ) -> Result<()> {
        if let FrameInner::Data(DataFrame::Transcription(data)) = &frame.inner {
            self.0.event("llm_handler_start_ms");
            p.push_frame(Frame::llm_full_response_start(), d).await?;
            let mut bridge = self.0.bridge.lock().await;
            bridge.send(json!({"op":"llm","text":data.text})).await?;
            let mut count = 0;
            loop {
                let v = bridge.line().await?;
                match v["event"].as_str() {
                    Some("token") => {
                        if count == 0 {
                            self.0.event("llm_first_token_at_ms");
                        }
                        let text = v["text"].as_str().ok_or_else(|| err("Invalid token"))?;
                        count += text.len();
                        if count > 16000 {
                            return Err(err("Answer size limit"));
                        }
                        p.push_frame(Frame::llm_text(text.to_owned()), d).await?;
                    }
                    Some("llm_first") => self.0.value(
                        "llm_model_first_answer_token_ms",
                        v["first_answer_token_ms"].clone(),
                    ),
                    Some("done") => {
                        self.0
                            .value("llm_model_adapter_ms", v["duration_ms"].clone());
                        break;
                    }
                    _ => {}
                }
            }
            self.0.event("llm_handler_end_ms");
            drop(bridge);
            p.push_frame(Frame::llm_full_response_end(), d).await
        } else {
            p.push_frame(frame, d).await
        }
    }
}
struct Tts {
    state: Arc<State>,
    text: StdMutex<String>,
}
#[async_trait]
impl FrameHandler for Tts {
    async fn on_process_frame(
        &self,
        p: &FrameProcessor,
        frame: Frame,
        d: FrameDirection,
    ) -> Result<()> {
        match &frame.inner {
            FrameInner::Data(DataFrame::LLMText(token)) => {
                let mut text = self.text.lock().unwrap();
                if text.len() + token.len() > 1200 {
                    return Err(err("Synthesis text limit"));
                }
                text.push_str(token);
                Ok(())
            }
            FrameInner::Control(ControlFrame::LLMFullResponseEnd) => {
                self.state.event("tts_handler_start_ms");
                let text = std::mem::take(&mut *self.text.lock().unwrap());
                let mut bridge = self.state.bridge.lock().await;
                bridge.send(json!({"op":"tts","text":text})).await?;
                loop {
                    let v = bridge.line().await?;
                    match v["event"].as_str() {
                        Some("pcm") => {
                            let pcm = STANDARD
                                .decode(v["pcm"].as_str().ok_or_else(|| err("Invalid audio"))?)
                                .map_err(|_| err("Invalid PCM encoding"))?;
                            p.push_frame(
                                Frame::output_audio_raw(AudioRawData::new(pcm, 24000, 1)),
                                d,
                            )
                            .await?;
                        }
                        Some("tts_metrics") => {
                            self.state
                                .value("tts_model_first_pcm_ms", v["first_pcm_ms"].clone());
                            self.state
                                .value("audio_duration_ms", v["audio_duration_ms"].clone());
                        }
                        Some("done") => {
                            self.state
                                .value("tts_model_adapter_ms", v["duration_ms"].clone());
                            break;
                        }
                        _ => {}
                    }
                }
                self.state.event("tts_handler_end_ms");
                drop(bridge);
                p.push_frame(frame, d).await
            }
            _ => p.push_frame(frame, d).await,
        }
    }
}
struct Sink(Arc<State>);
#[async_trait]
impl FrameHandler for Sink {
    async fn on_process_frame(
        &self,
        p: &FrameProcessor,
        frame: Frame,
        d: FrameDirection,
    ) -> Result<()> {
        match &frame.inner {
            FrameInner::Data(DataFrame::OutputAudioRaw(data)) => {
                let mut state = self.0.trace.lock().unwrap();
                if state["first_pcm_sink_at_ms"].is_null() {
                    state["first_pcm_sink_at_ms"] = json!(ms(self.0.started));
                }
                let samples = data
                    .audio
                    .chunks_exact(2)
                    .map(|b| i16::from_le_bytes([b[0], b[1]]) as f64 / 32768.0);
                let energy: f64 = samples.map(|v| v * v).sum();
                state["audio_energy_sum"] =
                    json!(state["audio_energy_sum"].as_f64().unwrap_or(0.0) + energy);
                state["audio_samples"] =
                    json!(state["audio_samples"].as_u64().unwrap_or(0) + data.num_frames as u64);
                Ok(())
            }
            FrameInner::Control(ControlFrame::LLMFullResponseEnd) => {
                self.0.event("sink_done_ms");
                self.0.done.notify_one();
                Ok(())
            }
            _ => p.push_frame(frame, d).await,
        }
    }
}
fn wav(path: &str) -> std::result::Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut reader = hound::WavReader::open(path)?;
    let spec = reader.spec();
    if spec.channels != 1
        || spec.sample_rate != 16000
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err("Expected 16 kHz mono signed-int16 WAV".into());
    }
    if reader.duration() > 16000 * 30 {
        return Err("Fixture exceeds 30 seconds".into());
    }
    let mut bytes = Vec::new();
    for sample in reader.samples::<i16>() {
        bytes.extend_from_slice(&sample?.to_le_bytes());
    }
    if bytes.is_empty() {
        return Err("Empty fixture".into());
    }
    Ok(bytes)
}
#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 {
        return Err("Usage: spike PYTHON BRIDGE.py PUBLIC_FIXTURE.wav".into());
    }
    let pcm = wav(&args[3])?;
    let mut child = Command::new(&args[1])
        .arg("-u")
        .arg(&args[2])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    let input = child.stdin.take().ok_or("Missing bridge stdin")?;
    let output = BufReader::new(child.stdout.take().ok_or("Missing bridge stdout")?);
    let mut bridge = Bridge {
        child,
        input,
        output,
    };
    let ready = tokio::time::timeout(Duration::from_secs(60), bridge.line()).await??;
    if ready["event"] != "ready" {
        return Err("Bridge did not initialize".into());
    }
    let state = Arc::new(State {
        started: Instant::now(),
        bridge: Mutex::new(bridge),
        done: Notify::new(),
        trace: StdMutex::new(json!({
            "schema_version":1, "experiment":"rustvani_headless", "rustvani_revision":REV,
            "model_settings":ready["settings"], "status":"running", "tts_buffering":"full_reply", "vad_included":false,
            "browser_playback_measured":false, "framework_speedup_claimed":false,
            "audio_input_duration_ms":pcm.len() as f64 / 32.0,
            "bridge_shutdown_forced":false, "first_pcm_sink_at_ms":null, "audio_samples":0, "audio_energy_sum":0.0
        })),
    });
    let failed = Arc::new(AtomicBool::new(false));
    let task = PipelineTask::new(
        vec![
            FrameProcessor::new("LocalParakeet", Box::new(Stt(state.clone())), false),
            FrameProcessor::new("LocalQwen", Box::new(Llm(state.clone())), false),
            FrameProcessor::new(
                "LocalPhonon",
                Box::new(Tts {
                    state: state.clone(),
                    text: StdMutex::new(String::new()),
                }),
                false,
            ),
            FrameProcessor::new("MetadataSink", Box::new(Sink(state.clone())), false),
        ],
        PipelineParams {
            allow_interruptions: false,
            ..Default::default()
        },
    );
    let failure_state = state.clone();
    let failure_flag = failed.clone();
    task.add_on_pipeline_error(move |_| {
        let state = failure_state.clone();
        let flag = failure_flag.clone();
        Box::pin(async move {
            flag.store(true, Ordering::SeqCst);
            state.done.notify_one();
        })
    });
    let sender = task.push_sender();
    let mut running = tokio::spawn(async move { task.run(system_clock(), None).await });
    state.event("injected_at_ms");
    sender
        .send((
            Frame::input_audio_raw(AudioRawData::new(pcm, 16000, 1)),
            FrameDirection::Downstream,
        ))
        .await?;
    let timeout = tokio::time::timeout(Duration::from_secs(90), state.done.notified())
        .await
        .is_err();
    let ok = !timeout
        && !failed.load(Ordering::SeqCst)
        && state.trace.lock().unwrap()["audio_energy_sum"]
            .as_f64()
            .unwrap_or(0.0)
            > 0.0;
    state.value(
        "status",
        json!(if ok {
            "ok"
        } else if timeout {
            "timeout"
        } else {
            "error"
        }),
    );
    // Cleanup precedes output: this short-lived experiment owns only its bridge.
    let _ = sender
        .send((Frame::cancel(), FrameDirection::Downstream))
        .await;
    match tokio::time::timeout(Duration::from_secs(4), &mut running).await {
        Ok(_) => {
            state.value("pipeline_shutdown_forced", json!(false));
        }
        Err(_) => {
            state.value("pipeline_shutdown_forced", json!(true));
            running.abort();
            let _ = running.await;
        }
    }
    let mut bridge = state.bridge.lock().await;
    if let Some(pid) = bridge.child.id() {
        // Give the Python owner a bounded chance to cancel/reap native STT.
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
    }
    if tokio::time::timeout(Duration::from_secs(3), bridge.child.wait())
        .await
        .is_err()
    {
        state.value("bridge_shutdown_forced", json!(true));
        let _ = bridge.child.kill().await;
        let _ = bridge.child.wait().await;
    }
    drop(bridge);
    let mut report = state.trace.lock().unwrap().clone();
    for (key, start, end) in [
        (
            "stt_to_llm_framework_handoff_ms",
            "stt_handler_end_ms",
            "llm_handler_start_ms",
        ),
        (
            "llm_to_tts_framework_handoff_ms",
            "llm_handler_end_ms",
            "tts_handler_start_ms",
        ),
        (
            "injected_utterance_to_first_pcm_sink_ms",
            "injected_at_ms",
            "first_pcm_sink_at_ms",
        ),
    ] {
        report[key] = match (report[start].as_f64(), report[end].as_f64()) {
            (Some(a), Some(b)) if b >= a => json!(b - a),
            _ => Value::Null,
        };
    }
    println!("{}", serde_json::to_string(&report)?);
    if !ok {
        return Err("Rustvani experiment failed; see metadata status".into());
    }
    Ok(())
}
