# Hospital-Grade File Upload Security

This document outlines the mandatory security standards for all file uploads across the system to prevent malicious files, fake extensions, MIME spoofing, and oversized uploads.

## 1. Overview
Never trust any user-uploaded file based on the client-side metadata or frontend verification. Attackers can rename `.exe` or `.js` to `.jpg` or `.pdf` and bypass simple checks.

Our system enforces three layers of server-side validation:
1. **Signature Validation (`python-magic`)**: Reads the actual binary bytes of the file to determine its true internal format.
2. **Extension Validation (`FileExtensionValidator`)**: Rejects spoofed or unsupported extensions.
3. **Size Validation**: Strictly enforces maximum file size (5 MB by default) before saving.

## 2. Allowed File Types
Uploads are restricted to these safe formats. The file must match both the extension and the true MIME type.

| Extension | True MIME Type | Allowed |
| --- | --- | --- |
| `jpg` / `jpeg` | `image/jpeg` | ✅ Yes |
| `png` | `image/png` | ✅ Yes |
| `webp` | `image/webp` | ✅ Yes |
| `gif` | `image/gif` | ✅ Yes |
| `pdf` | `application/pdf` | ✅ Yes |
| *anything else* | *any other* | ❌ Default Deny |

## 3. How to Add a New Upload Field (Crucial Pattern)
Whenever adding a `FileField` or `ImageField` to models, **YOU MUST** import and attach the three validators. 

### Implementation Example

```python
from django.db import models
from core.validators.file_validators import (
    validate_file_extension,
    validate_file_signature,
    validate_file_size
)

class PatientDocument(models.Model):
    # Always include all 3 validators
    document_file = models.FileField(
        upload_to="patient_documents/",
        validators=[
            validate_file_extension,
            validate_file_signature,
            validate_file_size
        ]
    )
```

## 4. Security Explanation
* **Malicious File Execution (RCE/XSS)**: By strictly ensuring files are actually images or PDFs at the binary level, the web server cannot be tricked into executing uploaded PHP, JS, or EXE files.
* **MIME Spoofing**: Browsers and intercepting proxies can easily forge the HTTP `Content-Type` header (`file.content_type`). `python-magic` ignores this header and checks the raw bytes.
* **Denial of Service (DoS)**: `validate_file_size` prevents attackers from filling up disk space or exhausting memory by uploading enormous multi-gigabyte files.

## 5. Deployment Notes
This security flow depends on `python-magic`. Ensure the installation requirements include `python-magic==0.4.27` or higher, as defined in `requirements.txt`.

## 6. Troubleshooting & Common Errors
Because `python-magic` interfaces with a C library (libmagic) under the hood, you might encounter missing library errors during deployment or local development.

### Error 1: `ImportError: failed to find libmagic`
This is the most common issue. The Python wrapper is installed, but the underlying system C library is missing.

**To solve on Ubuntu / Debian (e.g., Render, Heroku, AWS):**
You must install the system-level dependency. If you use a `render.yaml` or a Dockerfile, ensure the following is run before pip install:
```bash
sudo apt-get update
sudo apt-get install libmagic1
```

**To solve on macOS (Local Development):**
```bash
brew install libmagic
```

**To solve on Windows (Local Development):**
Windows users may need to install `python-magic-bin` instead:
```bash
pip install python-magic-bin
```
*(Note: Do not add `python-magic-bin` to the production `requirements.txt`, as it is only needed for Windows environments.)*

### Error 2: `ValidationError: Unsupported file type: application/octet-stream`
If the file format is not recognized or the file is corrupted, `magic` might default to `application/octet-stream` or `text/plain`.
**Solution:** Ensure the uploaded file is a valid, uncorrupted image or PDF. Sometimes CSVs or oddly formatted binaries trigger this. If this occurs on a valid file, check if the file format header was stripped.
