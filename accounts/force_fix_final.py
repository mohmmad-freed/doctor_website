import os

file_path = r"f:\ملفات الc\buisniss\clink app\accounts\templates\accounts\register_patient_details.html"

with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
skip_count = 0

for i in range(len(lines)):
    if skip_count > 0:
        skip_count -= 1
        continue

    line = lines[i]

    # Fix 1: City option (Lines ~506)
    # The line is split.
    # Look for the start of the option tag
    if 'option value="{{ city.id }}"' in line and "{% if" in line:
        if i + 1 < len(lines):
            # Force fix it regardless of current state if it matches the pattern
            print(f"Fixing City Option at line {i+1}")
            indent = line[: line.find("<")]
            # Note: NO spaces in stringformat arguments: stringformat:"s"
            # But spaces allow around ==
            correct_line = (
                indent
                + '<option value="{{ city.id }}" {% if form.city.value|stringformat:"s" == city.id|stringformat:"s" %}selected{% endif %}>\n'
            )
            new_lines.append(correct_line)
            # Check if next line was the continuation
            if "%}" in lines[i + 1] and "selected" in lines[i + 1]:
                skip_count = 1
            continue

    # Fix 2: Email input (Lines ~523)
    if 'value="{{ form.email.value' in line and "{% if" in line and not "%}" in line:
        if i + 1 < len(lines):
            print(f"Merging Email Input at line {i+1}")
            indent = line[: line.find("value")]
            # We need to preserve the previous line? No, this line is the value attribute line.
            # But wait, if the input tag started on previous line, we are just fixing this line.
            # The previous line <input ... class="form-control" is fine.
            # We just need to merge this line with the next.
            combined = line.rstrip() + " " + lines[i + 1].lstrip()
            # Ensure proper spacing for the if tag if needed
            new_lines.append(combined)
            skip_count = 1
            continue

    # Fix 3: Button (Line ~529)
    if "verify-email-btn" in line and "{% if" in line and not "%}" in line:
        if i + 1 < len(lines):
            print(f"Merging Button at line {i+1}")
            indent = line[: line.find("<")]
            correct_line = (
                indent
                + '<button type="button" id="verify-email-btn" class="verify-btn-inline" {% if email_is_verified %}style="display: none;" {% endif %}>\n'
            )
            new_lines.append(correct_line)
            skip_count = 1
            continue

    new_lines.append(line)

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("Done.")
