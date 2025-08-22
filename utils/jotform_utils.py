from PIL import Image, ImageDraw, ImageFont
import io, base64

def generate_signature(first, last):
    img = Image.new("RGB", (650, 114), color="white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.text((10, 40), f"{first} {last}", fill="black", font=font)
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")
