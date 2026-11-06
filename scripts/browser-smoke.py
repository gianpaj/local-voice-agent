"""Real Chromium + fake public microphone speech. Run while pnpm dev is up.

uv run --project apps/agent --group browser python scripts/browser-smoke.py --turns 10
Use --executable /path/to/chrome if the Playwright browser is already installed elsewhere.
This checks software audio, NOT physical/remote-device audio. --public-only-ice
removes private candidates from signaling to test a public address, even on one host.
"""
import argparse
import asyncio
import ipaddress
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
from urllib.parse import urlparse
import wave

from playwright.async_api import async_playwright

from local_voice.config import Config
from local_voice.tts import PhononEngine

QUESTIONS = [
    "What is two plus two?", "What color is a clear sky?", "Name one fruit.",
    "What is the capital of France?", "What is five minus two?", "Name one animal.",
    "What planet do we live on?", "What is three times three?", "Name one ocean.",
    "Say goodbye in one sentence.",
]
INSTRUMENT = r"""
window.__voiceTest={events:[], peers:[], streams:[], contexts:[], pcIds:[]};
const nativeGetUserMedia=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia=async (...args)=>{
  const stream=await nativeGetUserMedia(...args);window.__voiceTest.streams.push(stream);return stream;
};
const NativeAudioContext=window.AudioContext;
window.AudioContext=class extends NativeAudioContext {
  constructor(...args){super(...args);window.__voiceTest.contexts.push(this);}
};
const nativeFetch=window.fetch.bind(window);
const publicCandidate=line=>{
  if(/\btyp (srflx|relay)\b/.test(line))return true;
  const address=line.replace(/^a=/,'').split(/\s+/)[4]||'';
  // Globally routable IPv6 host candidates are also valid public routes.
  return /\btyp host\b/.test(line)&&/^[23][0-9a-f]{3}:/i.test(address);
};
const publicSdp=sdp=>sdp.split(/\r?\n/).filter(line=>!line.startsWith('a=candidate:')||publicCandidate(line)).join('\r\n');
window.fetch=async (...args)=>{
  const input=args[0],url=typeof input==='string'?input:input.url||String(input);
  const method=String(args[1]?.method||input.method||'GET').toUpperCase();
  if(window.__publicOnlyICE && url.endsWith('/api/offer') && args[1]?.body && ['POST','PATCH'].includes(method)){
    const body=JSON.parse(args[1].body);
    if(body.sdp)body.sdp=publicSdp(body.sdp);
    if(body.candidates)body.candidates=body.candidates.filter(item=>publicCandidate(item.candidate||''));
    args[1]={...args[1],body:JSON.stringify(body)};
  }
  let response=await nativeFetch(...args);
  if(url.endsWith('/api/offer') && method==='POST'){
    try{const body=await response.clone().json();if(body.pc_id)window.__voiceTest.pcIds.push(body.pc_id);
      if(window.__publicOnlyICE && body.sdp){body.sdp=publicSdp(body.sdp);
        const headers=new Headers(response.headers);headers.delete('content-length');headers.delete('content-encoding');
        response=new Response(JSON.stringify(body),{status:response.status,headers});}
    }catch{}
  }
  return response;
};
const NativePeer=window.RTCPeerConnection;
window.RTCPeerConnection=class extends NativePeer {
  constructor(...args){super(...args);window.__voiceTest.peers.push(this);}
  createDataChannel(...args){const channel=super.createDataChannel(...args);
    channel.addEventListener('message',event=>{try{const m=JSON.parse(event.data);
      window.__voiceTest.events.push({at:Date.now(),type:m.type,data:m.data});}catch{}});
    return channel;}
};
"""


async def fixture(path, turns, barge_in=False, lead_in=3):
    engine = PhononEngine(Config.from_env().phonon_model)
    await engine.load()
    pcm = bytearray(48000 * lead_in)
    try:
        phrases = ["Tell me a long story about a dragon.", "Stop, say only okay."] if barge_in else QUESTIONS[:turns]
        for index, phrase in enumerate(phrases):
            audio = bytearray()
            async for chunk in engine.stream(phrase):
                audio.extend(chunk)
            pcm.extend(audio)
            spacing = 5 if barge_in and index == 0 else 11
            pcm.extend(bytes(max(0, 48000 * spacing - len(audio))))
        pcm.extend(bytes(48000 * 5))
    finally:
        await engine.close()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(pcm)


async def main(args):
    with tempfile.TemporaryDirectory(prefix="local-voice-browser-") as directory:
        audio = Path(directory) / "public-questions.wav"
        turns = 2 if args.barge_in else args.turns
        lead_in = max(args.lead_in_seconds, 15 if args.public_only_ice else 3)
        await fixture(audio, turns, args.barge_in, lead_in)
        async with async_playwright() as playwright:
            launch = {"headless": True, "args": ["--no-sandbox", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={audio}"]}
            if args.executable:
                launch["executable_path"] = args.executable
            browser = await playwright.chromium.launch(**launch)
            try:
                page = await browser.new_page(permissions=["microphone"])
                await page.add_init_script(f"window.__publicOnlyICE={str(args.public_only_ice).lower()};\n" + INSTRUMENT)
                hosts, errors, gpu = set(), [], []
                page.on("request", lambda request: hosts.add(urlparse(request.url).netloc) if request.url.startswith("http") else None)
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(args.url)
                expected_ice = await page.evaluate("async()=>{const r=await fetch('/api/health');return (await r.json()).ice_servers||[]}")
                start = page.get_by_role("button", name="Start talking")
                await start.click(timeout=120_000)
                try:
                    await page.wait_for_function("!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Mute mic')?.disabled && !!document.querySelector('audio').srcObject")
                except Exception:
                    print(json.dumps(await page.evaluate("""async()=>({peers:await Promise.all(window.__voiceTest.peers.map(async p=>{const s=await p.getStats();return {state:p.connectionState,ice:p.iceConnectionState,candidates:[...s.values()].filter(x=>['local-candidate','remote-candidate'].includes(x.type)).map(x=>({direction:x.type,type:x.candidateType,protocol:x.protocol}))}})),errors:[...document.querySelectorAll('[role=alert]')].map(x=>x.textContent)})""")))
                    raise
                busy = await page.evaluate("""async()=>{const r=await fetch('/api/offer',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({sdp:'v=0',type:'offer'})});return r.status}""")
                assert busy == 409, f"Second offer was not rejected: {busy}"
                for _ in range(turns * 11 + lead_in):
                    await asyncio.sleep(1)
                    sample = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True)
                    gpu.append(int(sample.stdout.strip().splitlines()[0]))
                result = await page.evaluate("""async()=>{const p=window.__voiceTest.peers[0],s=await p.getStats();let inbound=[],selected=[];s.forEach(x=>{if(x.type==='inbound-rtp'&&x.kind==='audio')inbound.push(x);if(x.type==='candidate-pair'&&x.nominated&&x.state==='succeeded')selected.push({state:x.state,local:s.get(x.localCandidateId),remote:s.get(x.remoteCandidateId)})});return {events:window.__voiceTest.events,inbound,selected,ice:p.getConfiguration().iceServers}}""")
                users = await page.locator(".message.user").count()
                assistants = await page.locator(".message.assistant").count()
                assert users == turns and assistants >= turns, f"Only {users} user turns / {assistants} assistant segments"
                assert any(x.get("totalAudioEnergy", 0) > 0 for x in result["inbound"]), "No decoded non-silent assistant audio"
                assert result["selected"], "No successful nominated ICE pair"
                assert [s["urls"] if isinstance(s["urls"], list) else [s["urls"]] for s in result["ice"]] == [s["urls"] if isinstance(s["urls"], list) else [s["urls"]] for s in expected_ice], "Client ICE configuration differs from server"
                if args.public_only_ice:
                    for pair in result["selected"]:
                        remote = pair["remote"]
                        assert ipaddress.ip_address(remote.get("address") or remote.get("ip")).is_global, "Selected a non-public address"
                assert hosts == {urlparse(args.url).netloc}, f"Unexpected HTTP destination(s): {hosts}"
                assert not errors, errors
                if args.barge_in:
                    interruptions = [e for e in result["events"] if e["type"] == "bot-interrupted"]
                    assert len(interruptions) >= 2, "No second-turn interruption event"
                    after = [e["data"]["text"].lower().strip(".! ") for e in result["events"] if e["type"] == "bot-output" and e["at"] >= interruptions[1]["at"]]
                    assert after and all(text in {"ok", "okay"} for text in after), f"Old reply resumed after barge-in: {after}"
                ended, latencies = None, []
                for event in result["events"]:
                    if event["type"] == "server-message" and event.get("data", {}).get("phase") == "transcribing":
                        ended = event["at"]
                    if event["type"] == "bot-started-speaking" and ended:
                        latencies.append(round((event["at"] - ended) / 1000, 3))
                await page.get_by_role("button", name="Mute mic").click()
                assert await page.evaluate("window.__voiceTest.peers[0].getSenders().filter(s=>s.track?.kind==='audio').every(s=>!s.track.enabled)")
                await page.get_by_role("button", name="Unmute mic").click()
                replaced = False
                if await page.locator("select").first.locator("option").count() > 2:
                    old_track = await page.evaluate("window.__voiceTest.peers[0].getSenders().find(s=>s.track?.kind==='audio').track.id")
                    await page.locator("select").first.select_option(index=2)
                    await page.wait_for_function("old=>window.__voiceTest.peers[0].getSenders().find(s=>s.track?.kind==='audio').track.id!==old", arg=old_track)
                    replaced = True
                if args.disconnect_reconnect:
                    # End the actual server peer, not a simulated React/SDK callback.
                    closed = await page.evaluate("""async()=>{
                      const response=await fetch('/api/offer',{method:'DELETE',headers:{'content-type':'application/json'},
                        body:JSON.stringify({pc_id:window.__voiceTest.pcIds.at(-1)})});return response.status;
                    }""")
                    assert closed == 200, f"Server-side close failed: {closed}"
                    try:
                        await page.wait_for_function("""()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent.includes('Start talking')&&!b.disabled)
                          &&window.__voiceTest.streams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))
                          &&window.__voiceTest.contexts.every(c=>c.state==='closed')
                          &&document.querySelector('audio').srcObject===null""")
                    except Exception:
                        print(await page.evaluate("""()=>({buttons:Array.from(document.querySelectorAll('button')).map(b=>[b.textContent,b.disabled]),
                          peers:window.__voiceTest.peers.map(p=>p.connectionState),
                          tracks:window.__voiceTest.streams.map(s=>s.getTracks().map(t=>t.readyState)),
                          contexts:window.__voiceTest.contexts.map(c=>c.state),audioAttached:!!document.querySelector('audio').srcObject,
                          pcIds:window.__voiceTest.pcIds})"""))
                        raise
                    assert await page.locator(".message").count() == 0
                    await page.get_by_role("button", name="Start talking").click()
                    await page.wait_for_function("""()=>window.__voiceTest.peers.length===2
                      &&window.__voiceTest.peers[1].connectionState==='connected'
                      &&!!document.querySelector('audio').srcObject
                      &&Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Mute mic'&&!b.disabled)""")
                    assert await page.evaluate("window.__voiceTest.streams.at(-1).getAudioTracks().every(t=>t.readyState==='live')")
                await page.get_by_role("button", name="Stop session").click()
                assert await page.locator(".message").count() == 0
                assert await page.evaluate("window.__voiceTest.peers[0].connectionState") == "closed"
                summary = {"turns": users, "assistant_segments": assistants, "http_destinations": sorted(hosts), "gpu_peak_mib": max(gpu), "vad_complete_to_bot_started_seconds": latencies, "median_seconds": statistics.median(latencies), "decoded_audio_energy": result["inbound"][0].get("totalAudioEnergy"), "second_offer_status": busy, "stop_clears_and_closes": True, "microphone_sender_replaced": replaced}
                summary["ice_server_urls"] = [s["urls"] for s in expected_ice]
                if args.public_only_ice:
                    summary["public_only_ice"] = True
                    summary["public_remote_candidate"] = True
                    summary["remote_candidate_types"] = [p["remote"]["candidateType"] for p in result["selected"]]
                if args.barge_in:
                    summary["barge_in_drops_old_reply"] = True
                if args.disconnect_reconnect:
                    await page.wait_for_function("window.__voiceTest.peers.every(p=>p.connectionState==='closed') && window.__voiceTest.streams.every(s=>s.getTracks().every(t=>t.readyState==='ended')) && window.__voiceTest.contexts.every(c=>c.state==='closed')")
                    summary["remote_disconnect_releases_media_and_reconnects"] = True
                print(json.dumps(summary, indent=2))
            finally:
                # Failed ICE must release the single-session slot too.
                if 'page' in locals() and not page.is_closed():
                    try:
                        await page.evaluate("""async()=>{for(const pc_id of window.__voiceTest?.pcIds||[])await fetch('/api/offer',{method:'DELETE',headers:{'content-type':'application/json'},body:JSON.stringify({pc_id})})}""")
                    except Exception:
                        pass
                await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--executable")
    parser.add_argument("--turns", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--barge-in", action="store_true", help="Interrupt a dragon story with 'say only okay'")
    parser.add_argument("--public-only-ice", action="store_true", help="Remove private host candidates from offers/answers; require a public remote address")
    parser.add_argument("--lead-in-seconds", type=int, choices=range(3, 61), default=3, help="Initial microphone silence for slower ICE discovery")
    parser.add_argument("--disconnect-reconnect", action="store_true", help="End the server peer, verify media cleanup, then Start again without reloading")
    asyncio.run(main(parser.parse_args()))
