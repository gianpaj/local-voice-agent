"""SmallWebRTC -> Parakeet -> local Qwen -> Phonon -> SmallWebRTC."""
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TranscriptionFrame, LLMContextFrame
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker, PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from .llm import BoundedOllama
from .stt import ParakeetSTT
from .tts import PhononTTS
from .telemetry import CascadeObserver


def build_worker(connection, config, engine, telemetry=None, stt_engine=None):
    transport = SmallWebRTCTransport(connection, TransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        audio_in_sample_rate=16000, audio_out_sample_rate=24000,
    ))
    context = LLMContext([])
    aggregators = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(sample_rate=16000, params=VADParams(stop_secs=config.vad_stop_seconds)),
        empty_user_turn=None,
    ))
    if telemetry:
        user_aggregator = aggregators.user()
        aggregation_turn = None

        @user_aggregator.event_handler("on_before_process_frame")
        async def bind_transcript(_processor, frame):
            nonlocal aggregation_turn
            if isinstance(frame, TranscriptionFrame):
                aggregation_turn = getattr(frame, "local_voice_turn", None)

        @user_aggregator.event_handler("on_before_push_frame")
        async def bind_context(_processor, frame):
            if isinstance(frame, LLMContextFrame):
                frame.local_voice_turn = aggregation_turn

        # These processor hooks are synchronous/awaited in pinned Pipecat1.15,
        # unlike WorkerObserver's deferred proxy notifications.
        @transport.output().event_handler("on_before_process_frame")
        async def bind_output_audio(_processor, frame):
            telemetry.note_output_frame(frame)

        @transport.output().event_handler("on_before_push_frame")
        async def stamp_server_speech(_processor, frame):
            telemetry.stamp_server_event(frame)

    worker = None

    async def status(phase):
        if worker:
            await worker.rtvi.send_server_message({"phase": phase})

    worker = PipelineWorker(Pipeline([
        transport.input(), ParakeetSTT(config, status, telemetry, stt_engine), aggregators.user(),
        BoundedOllama(config, telemetry), PhononTTS(engine, telemetry), transport.output(), aggregators.assistant(),
    ]), params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000, enable_metrics=True, enable_usage_metrics=True),
        enable_rtvi=True, idle_timeout_secs=300, cancel_timeout_secs=8,
        observers=[CascadeObserver(telemetry)] if telemetry else [],
        enable_tracing=telemetry is not None, enable_turn_tracking=True,
        conversation_id=telemetry.id if telemetry else None,
        additional_span_attributes={"local.session_id": telemetry.id} if telemetry else None)

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _connection):
        engine.interrupt()
        await worker.cancel(reason="browser disconnected")

    return worker


async def run_worker(worker):
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()
