import io

from PIL import Image, ImageOps

from . import scan  # noqa: F401  (registers the HEIC opener)


def open_upright(path, max_side=None):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def jpeg_bytes(img, quality=85):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def sharpness(img):
    """Variance of a Laplacian-like edge filter on a small greyscale copy; higher is sharper."""
    from PIL import ImageFilter, ImageStat

    g = img.convert("L")
    g.thumbnail((512, 512))
    edges = g.filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]
