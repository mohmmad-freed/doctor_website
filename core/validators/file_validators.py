import os
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'pdf']
ALLOWED_MIME_TYPES = [
    'image/jpeg',
    'image/png',
    'image/webp',
    'image/gif',
    'application/pdf'
]
MAX_FILE_SIZE = 5

validate_file_extension = FileExtensionValidator(allowed_extensions=ALLOWED_EXTENSIONS)

# ---------- magic detection with fallback --------------------------------

def _detect_mime_via_magic(file_bytes):
    """Try python-magic's from_buffer. Returns mime string or None."""
    try:
        import magic
        return magic.from_buffer(file_bytes, mime=True)
    except (AttributeError, ImportError, TypeError):
        return None


# Byte-header signatures for safe file types
_SIGNATURES = [
    (b'\xff\xd8\xff',          'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n',    'image/png'),
    (b'%PDF',                  'application/pdf'),
    (b'GIF87a',                'image/gif'),
    (b'GIF89a',                'image/gif'),
    (b'RIFF',                  '_check_webp'),   # needs extra check
]


def _detect_mime_fallback(file_bytes):
    """Detect MIME by reading the raw file header bytes."""
    for sig, mime in _SIGNATURES:
        if file_bytes[:len(sig)] == sig:
            if mime == '_check_webp':
                # WebP: RIFF....WEBP
                if len(file_bytes) >= 12 and file_bytes[8:12] == b'WEBP':
                    return 'image/webp'
                continue
            return mime
    return 'application/octet-stream'


def validate_file_signature(file):
    """
    Validates the file type using its true content bytes.
    Uses python-magic when available, falls back to byte-header sniffing.
    """
    file_bytes = file.read(2048)
    file.seek(0)

    mime = _detect_mime_via_magic(file_bytes)
    if mime is None:
        mime = _detect_mime_fallback(file_bytes)

    if mime not in ALLOWED_MIME_TYPES:
        raise ValidationError(
            f"نوع الملف غير مدعوم: {mime}. الأنواع المسموحة: JPEG, PNG, WebP, GIF, PDF"
        )


def validate_file_size(file):
    """
    Validates that the file size is less than 5 MB.
    """
    max_size_bytes = MAX_FILE_SIZE * 1024 * 1024

    if file.size > max_size_bytes:
        raise ValidationError(
            f"حجم الملف يتجاوز الحد الأقصى المسموح ({MAX_FILE_SIZE} ميجابايت)."
        )

