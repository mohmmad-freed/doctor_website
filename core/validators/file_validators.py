import magic
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

def validate_file_signature(file):
    """
    Validates the file type using its true content bytes using python-magic.
    """
    file_bytes = file.read(2048)
    mime = magic.from_buffer(file_bytes, mime=True)
    file.seek(0)
    
    if mime not in ALLOWED_MIME_TYPES:
        raise ValidationError(f"Unsupported file type: {mime}. Allowed types: JPEG, PNG, WebP, GIF, PDF")

def validate_file_size(file):
    """
    Validates that the file size is less than 5 MB.
    """
    max_size_bytes = MAX_FILE_SIZE * 1024 * 1024
    
    if file.size > max_size_bytes:
        raise ValidationError(f"File size exceeds maximum limit of {MAX_FILE_SIZE} MB")
