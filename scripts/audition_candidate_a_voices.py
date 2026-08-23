"""Audition candidate voices for lab instructor / professor delivery."""

import asyncio
import os
import time
from pathlib import Path
import httpx

SAMPLE_TEXT_KO = (
    "현재 3단계입니다. "
    "밴드를 Solution A 500 마이크로리터로 세척한 뒤, "
    "37도에서 15분 동안 800 rpm으로 부드럽게 교반해 주세요. "
    "타이머가 필요하면 말씀해 주세요."
)

CANDIDATES = ["lux", "rex", "luna", "leo"]


def load_env():
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


async def audition_voice(client: httpx.AsyncClient, voice: str, output_dir: Path):
    api_key = os.environ.get("XAI_API_KEY", "")
    base_url = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    url = f"{base_url}/tts"
    payload = {
        "text": SAMPLE_TEXT_KO,
        "voice_id": voice,
        "language": "ko",
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 24000},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.monotonic()
    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=15.0)
        elapsed = time.monotonic() - t0
        if resp.status_code == 200 and resp.content:
            out_file = output_dir / f"audition_{voice}.wav"
            out_file.write_bytes(resp.content)
            print(
                f"Voice [{voice:6s}]: SUCCESS in {elapsed:5.2f}s | "
                f"Bytes: {len(resp.content):6d} | File: {out_file.name}"
            )
            return True, elapsed, len(resp.content)
        else:
            print(f"Voice [{voice:6s}]: HTTP {resp.status_code} ({resp.text[:100]})")
            return False, elapsed, 0
    except Exception as exc:
        elapsed = time.monotonic() - t0
        print(f"Voice [{voice:6s}]: FAILED ({type(exc).__name__}: {exc})")
        return False, elapsed, 0


async def main():
    load_env()
    output_dir = Path("scratch/voice_audition")
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Auditioning Korean professor-style voices with sample text:\n'{SAMPLE_TEXT_KO}'\n")
    async with httpx.AsyncClient() as client:
        results = []
        for v in CANDIDATES:
            success, el, size = await audition_voice(client, v, output_dir)
            results.append((v, success, el, size))
    print("\nAudition Summary:")
    for v, success, el, size in results:
        status = "OK" if success else "FAIL"
        print(f"  {v:6s} -> {status:4s} (time: {el:.2f}s, size: {size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
