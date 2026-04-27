import os
import base64
import anthropic
from flask import Flask, request, jsonify, render_template
from supabase import create_client, Client
from datetime import datetime
import uuid

app = Flask(__name__)

# --- Config (set these as environment variables on Render) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BUCKET_NAME = "images"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def describe_image(image_bytes: bytes) -> str:
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
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
    image_bytes = request.data
    if not image_bytes:
        return jsonify({"error": "No image data received"}), 400

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
        result = (
            supabase.table("images")
            .select("*")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
    else:
        result = (
            supabase.table("images")
            .select("*")
            .ilike("description", f"%{query}%")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

    return jsonify(result.data)


if __name__ == "__main__":
    app.run(debug=True)
