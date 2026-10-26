"""Lane HEAR2, decision 7 (2026-10-26): the screen's 48 -> 16 kHz conversion no longer aliases.

Both screens capture at the browser's rate (48 kHz on the person's laptop)
and send 16 kHz PCM16. ``resamplePcm16`` picked one sample in three with a
linear interpolation and no low-pass, so everything above 8 kHz -- fan and
pump noise, hiss -- folded into the voice band unattenuated. Measured on a
synthetic voice with high-frequency noise at -10 dB (node, the production
script): 10 dB signal-to-error in the voice band with the pick alone, 47
dB with a 64-tap windowed-sinc low-pass before it. A quiet room is the
same either way.

The capture and the calibration recording now go through
``createPcm16Downsampler``: the same pick after a low-pass, with the
filter's memory kept across the worklet's 20 ms blocks, so a block edge
leaves no mark. ``resamplePcm16`` keeps its name and its contract for the
callers that have it.
"""

from __future__ import annotations

import subprocess
import unittest

from tests.static_page import ROOT, VOICE_CORE
from tests.test_lane_rx2_voice_core import _host_names

SIGNALS = r"""
function voice(t){let v=0;for(let k=1;k<=36;k++){const f=110*k;if(f>4000)break;v+=Math.sin(2*Math.PI*f*t)/k;}return 0.08*(0.5*(1+Math.sin(2*Math.PI*4*t)))*v;}
function lcg(seed){let s=seed>>>0;return()=>{s=(Math.imul(s,1664525)+1013904223)>>>0;return s/4294967296*2-1;};}
function highNoise(n,gain,seed){const r=lcg(seed);let x=new Float64Array(n);for(let i=0;i<n;i++)x[i]=r();
 for(let p=0;p<4;p++){const y=new Float64Array(n);for(let i=1;i<n;i++)y[i]=(x[i]-x[i-1])/2;x=y;}
 let e=0;for(let i=0;i<n;i++)e+=x[i]*x[i];const rms=Math.sqrt(e/n);for(let i=0;i<n;i++)x[i]*=gain/rms;return x;}
const SR=48000,TR=16000,seconds=2,N=SR*seconds;
// The reference: the voice alone at 16 kHz, delayed by what the path delays (the filter's DOWNSAMPLE_TAPS/2 input samples; nothing for the plain pick).
function reference(delayInputSamples){const ref=new Float64Array(TR*seconds);for(let i=0;i<ref.length;i++)ref[i]=voice((i*SR/TR-delayInputSamples)/SR);return ref;}
let vp=0;{const r=reference(0);for(const v of r)vp+=v*v;vp/=r.length;}
function snr(out,delayInputSamples){const ref=reference(delayInputSamples);let ep=0;const n=Math.min(out.length,ref.length);for(let i=64;i<n;i++){const d=out[i]/32767-ref[i];ep+=d*d;}ep/=(n-64);return 10*Math.log10(vp/ep);}
function signal(noiseDb){const x=new Float32Array(N);const noise=noiseDb===null?null:highNoise(N,Math.sqrt(vp)*Math.pow(10,noiseDb/20),7);for(let i=0;i<N;i++)x[i]=voice(i/SR)+(noise?noise[i]:0);return x;}
function blocks(x,size){const out=[];for(let at=0;at<x.length;at+=size)out.push(x.subarray(at,Math.min(x.length,at+size)));return out;}
function joinInt16(parts){const n=parts.reduce((s,p)=>s+p.length,0),out=new Int16Array(n);let at=0;for(const p of parts){out.set(p,at);at+=p.length;}return out;}
"""


def run_voice_core(body: str) -> subprocess.CompletedProcess:
    names = _host_names()
    stubs = "".join(f"globalThis[{name!r}]=()=>null;\n" for name in names)
    harness = (
        "const assert=(ok,m)=>{if(!ok)throw new Error(m)};\n"
        "globalThis.document={addEventListener(){},getElementById:()=>null,visibilityState:'visible'};\n"
        "class WS{static OPEN=1;constructor(){this.readyState=1;this.sent=[]}send(v){this.sent.push(v)}}globalThis.WebSocket=WS;\n"
        + stubs + VOICE_CORE.read_text(encoding="utf-8") + SIGNALS + body + "\nconsole.log('ok');\n"
    )
    return subprocess.run(["node", "-"], cwd=ROOT, text=True, input=harness, capture_output=True)


class DownsamplerTests(unittest.TestCase):
    def test_high_frequency_noise_no_longer_folds_into_the_voice_band(self) -> None:
        result = run_voice_core(r"""
const down=createPcm16Downsampler(SR,TR);
const quiet=snr(down(signal(null)),DOWNSAMPLE_TAPS/2);
assert(quiet>50,`a quiet room: ${quiet.toFixed(1)} dB`);
const noisy=snr(createPcm16Downsampler(SR,TR)(signal(-10)),DOWNSAMPLE_TAPS/2);
assert(noisy>40,`noise at -10 dB: ${noisy.toFixed(1)} dB in the voice band`);
const picked=snr(resamplePcm16(signal(-10),SR,TR),0);
assert(picked<15,`the plain pick should still alias (${picked.toFixed(1)} dB), the callers moved`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_block_by_block_equals_the_whole_and_the_count_is_exact(self) -> None:
        result = run_voice_core(r"""
const x=signal(-10);
const whole=createPcm16Downsampler(SR,TR)(x);
const down=createPcm16Downsampler(SR,TR);
const pieces=blocks(x,960).map(block=>down(block));  // the worklet's 20 ms blocks at 48 kHz
const joined=joinInt16(pieces);
assert(joined.length===whole.length,`lengths ${joined.length} vs ${whole.length}`);
assert(whole.length===TR*seconds,`output count ${whole.length}`);
let worst=0;for(let i=0;i<whole.length;i++)worst=Math.max(worst,Math.abs(joined[i]-whole[i]));
assert(worst<=1,`a block edge left a mark: ${worst}`);
assert(pieces.every(p=>p.length===320),`every 20 ms block gives 320 samples: ${pieces.map(p=>p.length).slice(0,4)}`);
// 44.1 kHz: 882-sample blocks, still 320 out each, nothing lost over a second.
const d44=createPcm16Downsampler(44100,16000);let total=0;for(let b=0;b<50;b++)total+=d44(new Float32Array(882).fill(0.1)).length;
assert(total===16000,`44.1 kHz gave ${total} samples a second`);
// The same rate passes through untouched.
const same=createPcm16Downsampler(16000,16000)(new Float32Array([0,0.5,-0.5,1]));
assert(same.length===4&&same[1]===Math.round(0.5*32767)&&same[3]===32767,`passthrough ${Array.from(same)}`);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_capture_and_the_calibration_recording_use_it(self) -> None:
        source = VOICE_CORE.read_text(encoding="utf-8")
        self.assertIn("createPcm16Downsampler(micContext.sampleRate,targetRate)", source)
        self.assertIn("createPcm16Downsampler(context.sampleRate,16000)", source)
        self.assertNotIn("resamplePcm16(f,micContext.sampleRate,targetRate)", source)


if __name__ == "__main__":
    unittest.main()
