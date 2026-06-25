"""
Helper for serving access-controlled user uploads through Django (never from a
public ``/media/`` URL).

Security posture:
- Always sets ``X-Content-Type-Options: nosniff`` so the browser honours the
  declared Content-Type and never sniffs an uploaded file into an executable
  type (HTML/JS).
- Only *validated image types* are served inline (they cannot execute script);
  everything else (PDF, unknown) is forced to download with
  ``Content-Disposition: attachment``. Combined with ``nosniff`` this keeps a
  malicious or mislabelled file from rendering in the trusted same-origin
  context.
"""
import mimetypes

from django.http import FileResponse

# Extensions we are willing to render inline. These are signature-validated at
# upload time and cannot execute script when rendered as their declared type.
_INLINE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}


def serve_protected_file(file_field, original_name):
    """
    Return a ``FileResponse`` for an authenticated download.

    ``file_field`` is a Django ``FieldFile`` (e.g. ``record.file``);
    ``original_name`` is the human filename used for the download and to decide
    the Content-Type / disposition.
    """
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    inline = ext in _INLINE_IMAGE_EXTENSIONS

    content_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    response = FileResponse(
        file_field.open("rb"),
        as_attachment=not inline,
        filename=original_name,
        content_type=content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
