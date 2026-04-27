import os
import base64
import struct
import anthropic
from flask import Flask, request, jsonify, render_template
from supabase import create_client, Client
from datetime import datetime
from PIL import Image
import io
import uuid

app = Flask(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BUCKET_NAME = "images"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def describe_image(image_bytes: bytes) -> str:
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    message = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Describe this image in 2-4 sentences. "
                            "Be specific about objects, people, colors, and setting. "
                            "Write naturally as if describing it to someone who cannot see it."
                        ),
                    },
                ],
            }
        ],
    )
    return message.content[0].text


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    raw_bytes = request.data
    if not raw_bytes:
        return jsonify({"error": "No image data received"}), 400

    width = int(request.headers.get("X-Image-Width", 160))
    height = int(request.headers.get("X-Image-Height", 120))

    # Convert raw RGB565 to RGB888
    rgb888 = bytearray(width * height * 3)
    for i in range(width * height):
        pixel = struct.unpack_from(">H", raw_bytes, i * 2)[0]
        r = (pixel >> 11) & 0x1F
        g = (pixel >> 5) & 0x3F
        b = pixel & 0x1F
        rgb888[i*3]   = (r << 3) | (r >> 2)
        rgb888[i*3+1] = (g << 2) | (g >> 4)
        rgb888[i*3+2] = (b << 3) | (b >> 2)
    img = Image.frombytes("RGB", (width, height), bytes(rgb888))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    image_bytes = buf.getvalue()

    image_id = str(uuid.uuid4())
    filename = f"{image_id}.jpg"
    timestamp = datetime.utcnow().isoformat()

    supabase.storage.from_(BUCKET_NAME).upload(
        path=filename,
        file=image_bytes,
        file_options={"content-type": "image/jpeg"},
    )

    public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(filename)

    try:
        description = describe_image(image_bytes)
    except Exception as e:
        description = f"Description unavailable: {str(e)}"

    supabase.table("images").insert(
        {
            "id": image_id,
            "filename": filename,
            "url": public_url,
            "description": description,
            "created_at": timestamp,
        }
    ).execute()

    return jsonify(
        {"id": image_id, "url": public_url, "description": description}
    ), 200


@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify([])  # Return empty if no search query

    result = (
        supabase.table("images")
        .select("*")
        .ilike("description", f"%{query}%")
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return jsonify(result.data)


@app.route("/claim/<image_id>", methods=["DELETE"])
def claim(image_id):
    # Get the image record first
    result = supabase.table("images").select("*").eq("id", image_id).execute()
    if not result.data:
        return jsonify({"error": "Image not found"}), 404

    filename = result.data[0]["filename"]

    # Delete from storage
    supabase.storage.from_(BUCKET_NAME).remove([filename])

    # Delete from database
    supabase.table("images").delete().eq("id", image_id).execute()

    return jsonify({"success": True}), 200


if __name__ == "__main__":
    app.run(debug=True)
