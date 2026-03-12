import os
base_dir = r"d:\Clinc website\doctor_website"
appointments_views = os.path.join(base_dir, "appointments", "views.py")
patients_views = os.path.join(base_dir, "patients", "views.py")

with open(appointments_views, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if '# ─── Intake Form Helpers' in line:
        start_idx = i
    elif '# ─── Main Views' in line:
        end_idx = i

if start_idx != -1 and end_idx != -1:
    new_lines = lines[:start_idx]
    new_lines.append('from appointments.services.intake_service import (\n')
    new_lines.append('    get_active_intake_template,\n')
    new_lines.append('    get_rules_for_template,\n')
    new_lines.append('    collect_and_validate_intake,\n')
    new_lines.append('    save_intake_answers,\n')
    new_lines.append(')\n\n\n')
    new_lines.extend(lines[end_idx:])
    
    with open(appointments_views, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print("Fixed appointments.views")

with open(patients_views, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('from appointments.views import', 'from appointments.services.intake_service import')

with open(patients_views, 'w', encoding='utf-8') as f:
    f.write(content)
print("Fixed patients.views")
