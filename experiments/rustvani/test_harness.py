"""Pipeline IPC/metadata/fault checks; fake inference does not measure quality."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import wave

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path(os.environ.get("RUSTVANI_BINARY", ROOT / ".local/rustvani-target/release/local-voice-rustvani-spike"))
STUB = '''
import base64,json,struct,sys
print(json.dumps({"event":"ready"}),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    if req['op']=='stt':
        print(json.dumps({"event":"transcript","text":"PRIVATE_TRANSCRIPT_SENTINEL"}),flush=True)
    if req['op']=='llm':
        print(json.dumps({"event":"token","text":"PRIVATE_REPLY_SENTINEL"}),flush=True)
        print(json.dumps({"event":"llm_first","first_answer_token_ms":1.0}),flush=True)
    if req['op']=='tts':
        pcm=struct.pack('<256h',*[1000]*256)
        print(json.dumps({"event":"pcm","pcm":base64.b64encode(pcm).decode()}),flush=True)
        print(json.dumps({"event":"tts_metrics","first_pcm_ms":1.0,"audio_duration_ms":256/24}),flush=True)
    print(json.dumps({"event":"done","duration_ms":1.0}),flush=True)
'''

@unittest.skipUnless(BINARY.is_file(), "Build the Rust experiment first")
class HarnessTest(unittest.TestCase):
    def run_case(self, stub=STUB, rate=16000):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            (path/'bridge.py').write_text(textwrap.dedent(stub))
            with wave.open(str(path/'fixture.wav'),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(rate)
                audio.writeframes(struct.pack('<512h',*[100]*512))
            return subprocess.run([str(BINARY),sys.executable,str(path/'bridge.py'),str(path/'fixture.wav')],capture_output=True,text=True,timeout=12)

    def test_real_framework_routes_frames_without_persisting_text(self):
        result=self.run_case()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('PRIVATE_',result.stdout+result.stderr)
        value=json.loads(result.stdout)
        self.assertEqual(value['status'],'ok')
        self.assertEqual(value['audio_samples'],256)
        self.assertGreater(value['audio_energy_sum'],0)
        self.assertGreaterEqual(value['stt_to_llm_framework_handoff_ms'],0)
        self.assertGreaterEqual(value['llm_to_tts_framework_handoff_ms'],0)
        self.assertFalse(value['browser_playback_measured'])

    def test_adapter_error_is_not_success_or_zero_latency(self):
        result=self.run_case('''
            import json,sys
            print(json.dumps({"event":"ready"}),flush=True)
            sys.stdin.readline()
            print(json.dumps({"event":"error","error_type":"SyntheticFailure"}),flush=True)
        ''')
        self.assertNotEqual(result.returncode,0)
        value=json.loads(result.stdout)
        self.assertEqual(value['status'],'error')
        self.assertIsNone(value['injected_utterance_to_first_pcm_sink_ms'])

    def test_rejects_wrong_audio_format_before_inference(self):
        result=self.run_case(rate=48000)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('16 kHz mono signed-int16',result.stderr)

    def test_bounded_ipc_rejects_oversized_line(self):
        result=self.run_case("print('X'*2000100,flush=True)")
        self.assertNotEqual(result.returncode,0)
        self.assertIn('IPC line too large',result.stderr)

if __name__=='__main__':
    unittest.main()
