"""
market_connectors/minimax.py — MiniMax M1/M2 & multimodal APIs.
TTS (text-to-speech), Music, Video, Image generation.
API docs: https://platform.minimax.io
"""

import os, httpx

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.getenv("MINIMAX_GROUP_ID", "")
MINIMAX_BASE = "https://api.minimax.chat/v1"


async def text_to_speech(text: str, voice_id: str = "male-qn-qingse",
                         speed: float = 1.0, model: str = "speech-01") -> dict:
    if not MINIMAX_API_KEY:
        return {"error": "MINIMAX_API_KEY not configured"}
    body = {"model": model, "text": text, "voice_setting": {"voice_id": voice_id, "speed": speed, "vol": 1.0},
            "audio_setting": {"sample_rate": 32000, "format": "mp3"}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{MINIMAX_BASE}/t2a_v2", json=body,
            headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"status": "ok", "audio_hex": data.get("data", {}).get("audio", ""),
                    "format": "mp3", "sample_rate": 32000}
        return {"error": resp.text}


async def generate_image(prompt: str, model: str = "image-01",
                         aspect_ratio: str = "16:9", n: int = 1) -> dict:
    if not MINIMAX_API_KEY:
        return {"error": "MINIMAX_API_KEY not configured"}
    body = {"model": model, "prompt": prompt, "aspect_ratio": aspect_ratio, "n": n,
            "prompt_optimizer": True, "group_id": MINIMAX_GROUP_ID}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{MINIMAX_BASE}/image_generation", json=body,
            headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"status": "ok", "images": data.get("data", {}).get("image_urls", [])}
        return {"error": resp.text}


async def generate_video(prompt: str, model: str = "T2V-01",
                         duration: int = 5) -> dict:
    if not MINIMAX_API_KEY:
        return {"error": "MINIMAX_API_KEY not configured"}
    body = {"model": model, "prompt": prompt, "duration": duration, "prompt_optimizer": True}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{MINIMAX_BASE}/video_generation", json=body,
            headers={"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return {"status": "ok", "task_id": data.get("task_id", ""),
                    "video_url": data.get("data", {}).get("video_url", "")}
        return {"error": resp.text}
