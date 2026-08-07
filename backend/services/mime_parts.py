import base64
from dataclasses import dataclass
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


@dataclass(frozen=True)
class InlineImagePart:
    content_id: str
    data: bytes
    content_type: str
    filename: str


def _inline_mime_part(image: InlineImagePart) -> MIMEBase:
    major, _, subtype = str(image.content_type or "image/webp").partition("/")
    if major.lower() != "image" or not subtype:
        major, subtype = "image", "webp"
    part = MIMEBase(major, subtype)
    part.set_payload(image.data)
    encoders.encode_base64(part)
    part.add_header("Content-ID", f"<{image.content_id}>")
    part.add_header("Content-Disposition", "inline", filename=image.filename or "inline-image")
    return part


def build_alternative_body(
    body_html: str,
    body_text: str = "",
    inline_images: list[InlineImagePart] | None = None,
) -> MIMEMultipart:
    alternative = MIMEMultipart("alternative")
    if body_text:
        alternative.attach(MIMEText(body_text, "plain", "utf-8"))

    images = list(inline_images or [])
    if not images:
        alternative.attach(MIMEText(body_html or "", "html", "utf-8"))
        return alternative

    related = MIMEMultipart("related")
    related.attach(MIMEText(body_html or "", "html", "utf-8"))
    for image in images:
        related.attach(_inline_mime_part(image))
    alternative.attach(related)
    return alternative


def inline_cids_to_data_uris(body_html: str, inline_images: list[InlineImagePart] | None) -> str:
    rendered = str(body_html or "")
    for image in inline_images or []:
        encoded = base64.b64encode(image.data).decode("ascii")
        data_uri = f"data:{image.content_type};base64,{encoded}"
        rendered = rendered.replace(f"cid:{image.content_id}", data_uri)
    return rendered
