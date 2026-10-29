import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

IMAGE_MODEL = "gpt-image-2.5-flare"


def generate_product_image(
    *,
    prompt: str,
    size: str,
    quality: str,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Image generation is not configured")

    client = OpenAI(api_key=api_key)
    response = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size=size,
        quality=quality,
        n=1,
    )
    image = (response.data or [None])[0]
    if image is None:
        raise RuntimeError("The image provider returned no image")

    image_url = getattr(image, "url", None)
    image_base64 = getattr(image, "b64_json", None)
    if not image_url and not image_base64:
        raise RuntimeError("The image provider returned an unusable image")

    return {
        "model": IMAGE_MODEL,
        "url": image_url,
        "b64_json": image_base64,
    }
