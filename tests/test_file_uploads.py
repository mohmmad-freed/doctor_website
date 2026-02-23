import os
import django
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError

# Setup Django Environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clink.settings")
django.setup()

from core.validators.file_validators import (
    validate_file_signature,
    validate_file_size,
    validate_file_extension
)

def test_file_validation():
    print("Starting Security File Validation Tests...\n")
    
    # --- TEST 1: Valid Text File (Should FAIL extension and signature) ---
    print("[TEST 1] Uploading standard text file (.txt)")
    txt_content = b"Hello world, I am a text file."
    file1 = SimpleUploadedFile("test.txt", txt_content, content_type="text/plain")
    try:
        validate_file_extension(file1)
        validate_file_signature(file1)
        print("[FAIL] Test 1 FAILED: Text file was mistakenly ALLOWED.")
    except ValidationError as e:
        print(f"[PASS] Test 1 PASSED: Text file blocked successfully. Reason: {e.message}")


    # --- TEST 2: Valid Image File (Should PASS) ---
    print("\n[TEST 2] Uploading a valid image file (.png)")
    # A tiny valid PNG binary signature
    png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDAT\x08\x99c\xf8\x0f\x04\x00\x09\xfb\x03\xfd\xe3U\xf2\x9c\x00\x00\x00\x00IEND\xaeB`\x82"
    file2 = SimpleUploadedFile("real_image.png", png_content, content_type="image/png")
    try:
        validate_file_extension(file2)
        validate_file_signature(file2)
        print("[PASS] Test 2 PASSED: Valid PNG file was allowed.")
    except ValidationError as e:
        print(f"[FAIL] Test 2 FAILED: Valid PNG was blocked. Reason: {e.message}")


    # --- TEST 3: Spoofed File (Malicious .exe renamed to .jpg) ---
    print("\n[TEST 3] Uploading a malicious .exe spoofed as a .jpg")
    # This is an executable signature starting with MZ
    exe_content = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00malicious_code_here"
    file3 = SimpleUploadedFile("hacked_file.jpg", exe_content, content_type="image/jpeg")
    try:
        validate_file_extension(file3)
        validate_file_signature(file3)
        print("[FAIL] Test 3 FAILED: Spoofed file was ALLOWED (CRITICAL VULNERABILITY).")
    except ValidationError as e:
        print(f"[PASS] Test 3 PASSED: Spoofed file blocked successfully by python-magic. Reason: {e.message}")


    # --- TEST 4: Oversized File ---
    print("\n[TEST 4] Uploading an oversized file (6 MB)")
    # We mock the size attribute for the test
    file4 = SimpleUploadedFile("big_image.png", png_content, content_type="image/png")
    file4.size = 6 * 1024 * 1024 + 1 # 6 MB + 1 byte
    try:
        validate_file_size(file4)
        print("[FAIL] Test 4 FAILED: Oversized file was ALLOWED.")
    except ValidationError as e:
        print(f"[PASS] Test 4 PASSED: Oversized file blocked successfully. Reason: {e.message}")

    print("\nSecurity Validation Testing Complete.")

if __name__ == "__main__":
    test_file_validation()
