from datetime import datetime, date

from appointments.models import AppointmentAttachment, AppointmentAnswer
from doctors.models import DoctorIntakeFormTemplate, DoctorIntakeQuestion, DoctorIntakeRule

def get_active_intake_template(doctor_id, appointment_type_id=None):
    """
    Find the active intake form template for a doctor.

    Lookup order (per README Section 6.3):
      1. Template specific to this appointment_type
      2. Template for all types (appointment_type=NULL)
      3. None

    Returns: (DoctorIntakeFormTemplate, list[DoctorIntakeQuestion]) or (None, [])
    """
    # 1. Try type-specific template
    if appointment_type_id:
        try:
            template = DoctorIntakeFormTemplate.objects.prefetch_related("questions").get(
                doctor_id=doctor_id,
                appointment_type_id=appointment_type_id,
                is_active=True,
            )
            return template, list(template.ordered_questions)
        except DoctorIntakeFormTemplate.DoesNotExist:
            pass

    # 2. Try generic template (appointment_type=NULL)
    try:
        template = DoctorIntakeFormTemplate.objects.prefetch_related("questions").get(
            doctor_id=doctor_id,
            appointment_type__isnull=True,
            is_active=True,
        )
        return template, list(template.ordered_questions)
    except DoctorIntakeFormTemplate.DoesNotExist:
        pass

    return None, []


def get_rules_for_template(template):
    """
    Load all conditional display rules for a template.
    Returns a list of dicts ready for JSON serialization (for client-side JS).
    """
    if template is None:
        return []
    rules = DoctorIntakeRule.objects.filter(
        source_question__template=template,
    ).select_related("source_question", "target_question")

    return [
        {
            "source_question_id": r.source_question_id,
            "expected_value": r.expected_value,
            "operator": r.operator,
            "target_question_id": r.target_question_id,
            "action": r.action,
        }
        for r in rules
    ]


def evaluate_rules_server_side(questions, answers_dict, rules):
    """
    Re-evaluate conditional rules server-side to determine which questions
    are actually visible. Returns a set of visible question IDs.
    """
    visible = set()
    # Start: all questions visible, except those targeted by SHOW rules
    # (they start hidden until their condition is met)
    show_targets = set()
    hide_targets = set()

    for rule in rules:
        if rule.action == DoctorIntakeRule.Action.SHOW:
            show_targets.add(rule.target_question_id)
        elif rule.action == DoctorIntakeRule.Action.HIDE:
            hide_targets.add(rule.target_question_id)

    for q in questions:
        if q.id in show_targets:
            # Hidden by default; will be shown if condition met
            pass
        else:
            visible.add(q.id)

    # Evaluate each rule
    for rule in rules:
        source_answer = answers_dict.get(str(rule.source_question_id), "")
        match = False

        if rule.operator == DoctorIntakeRule.Operator.EQUALS:
            match = source_answer == rule.expected_value
        elif rule.operator == DoctorIntakeRule.Operator.NOT_EQUALS:
            match = source_answer != rule.expected_value
        elif rule.operator == DoctorIntakeRule.Operator.CONTAINS:
            match = rule.expected_value in source_answer
        elif rule.operator == DoctorIntakeRule.Operator.IN:
            if isinstance(source_answer, list):
                match = rule.expected_value in source_answer
            else:
                match = rule.expected_value in source_answer

        if match:
            if rule.action == DoctorIntakeRule.Action.SHOW:
                visible.add(rule.target_question_id)
            elif rule.action == DoctorIntakeRule.Action.HIDE:
                visible.discard(rule.target_question_id)

    return visible


def collect_and_validate_intake(post_data, files, questions, rules):
    """
    Collect answers from POST, validate required fields (respecting conditional rules),
    and validate file uploads (type + size).

    Two file-related types:
      FILE         → simple multi-file upload (name="intake_{q_id}")
      DATED_FILES  → date-grouped uploads, 7 groups × 5 files each
                     (name="intake_dfile_{q_id}_g{i}", date="intake_dfile_date_{q_id}_g{i}")

    Returns: (answers_dict, file_data, errors)
      - answers_dict: {str(question_id): answer_text}
      - file_data:
          For FILE:        {q_id_str: [UploadedFile, ...]}
          For DATED_FILES: {q_id_str: [(date_str, [UploadedFile, ...]), ...]}
      - errors: list of error strings
    """
    MAX_GROUPS = AppointmentAttachment.MAX_FILE_GROUPS
    MAX_PER_GROUP = AppointmentAttachment.MAX_FILES_PER_GROUP
    MAX_TOTAL_BYTES = AppointmentAttachment.MAX_TOTAL_UPLOAD_MB * 1024 * 1024

    answers = {}
    file_data = {}

    for q in questions:
        key = f"intake_{q.id}"
        if q.field_type == DoctorIntakeQuestion.FieldType.MULTISELECT:
            value = post_data.getlist(key)
            if value:
                answers[str(q.id)] = "، ".join(value)

        elif q.field_type == DoctorIntakeQuestion.FieldType.FILE:
            # Simple multi-file upload
            uploaded_list = files.getlist(key)
            uploaded_list = [f for f in uploaded_list if f and f.size > 0]
            if uploaded_list:
                file_data[str(q.id)] = uploaded_list

        elif q.field_type == DoctorIntakeQuestion.FieldType.DATED_FILES:
            # Date-grouped files: up to 7 groups × 5 files
            group_count_str = post_data.get(f"intake_dfile_count_{q.id}", "0")
            try:
                group_count = min(int(group_count_str), MAX_GROUPS)
            except (ValueError, TypeError):
                group_count = 0

            groups = []
            for gi in range(group_count):
                group_date = post_data.get(f"intake_dfile_date_{q.id}_g{gi}", "").strip()
                group_files = files.getlist(f"intake_dfile_{q.id}_g{gi}")
                group_files = [f for f in group_files if f and f.size > 0]
                if group_files:
                    groups.append((group_date, group_files[:MAX_PER_GROUP]))

            if groups:
                file_data[str(q.id)] = groups

        elif q.field_type == DoctorIntakeQuestion.FieldType.CHECKBOX:
            value = post_data.get(key, "")
            answers[str(q.id)] = value
        else:
            value = post_data.get(key, "").strip()
            if value:
                answers[str(q.id)] = value

    # Evaluate rules to determine visible questions
    db_rules = DoctorIntakeRule.objects.filter(
        source_question__template=questions[0].template if questions else None,
    ) if questions else DoctorIntakeRule.objects.none()
    visible_ids = evaluate_rules_server_side(questions, answers, db_rules)

    # Validate required fields + file constraints (only for visible questions)
    errors = []
    for q in questions:
        if q.id not in visible_ids:
            continue

        if q.field_type == DoctorIntakeQuestion.FieldType.FILE:
            uploaded_list = file_data.get(str(q.id), [])

            if q.is_required and not uploaded_list:
                errors.append(f'الحقل "{q.display_text}" مطلوب.')
                continue

            for uploaded in uploaded_list:
                if q.max_file_size_mb:
                    max_bytes = q.max_file_size_mb * 1024 * 1024
                    if uploaded.size > max_bytes:
                        errors.append(
                            f'الملف "{uploaded.name}" في "{q.display_text}" يتجاوز الحد الأقصى '
                            f'({q.max_file_size_mb} ميغابايت). '
                            f'حجم الملف: {uploaded.size / (1024 * 1024):.1f} ميغابايت.'
                        )
                if q.allowed_extensions:
                    file_ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
                    allowed = [ext.lower().lstrip(".") for ext in q.allowed_extensions]
                    if file_ext not in allowed:
                        errors.append(
                            f'صيغة الملف "{uploaded.name}" في "{q.display_text}" غير مسموحة. '
                            f'الصيغ المسموحة: {", ".join(allowed)}.'
                        )

        elif q.field_type == DoctorIntakeQuestion.FieldType.DATED_FILES:
            groups = file_data.get(str(q.id), [])
            all_files = [f for _, gf in groups for f in gf]

            if q.is_required and not all_files:
                errors.append(f'الحقل "{q.display_text}" مطلوب.')
                continue

            if len(groups) > MAX_GROUPS:
                errors.append(
                    f'الحد الأقصى لمجموعات الملفات في "{q.display_text}" هو {MAX_GROUPS}.'
                )

            for gi, (group_date, group_files) in enumerate(groups):
                if not group_date:
                    errors.append(
                        f'يرجى تحديد تاريخ للمجموعة {gi + 1} في "{q.display_text}".'
                    )
                else:
                    # Future date check
                    try:
                        from datetime import datetime as _dt
                        parsed = _dt.strptime(group_date, "%Y-%m-%d").date()
                        if parsed > date.today():
                            errors.append(
                                f'تاريخ المجموعة {gi + 1} في "{q.display_text}" '
                                f'لا يمكن أن يكون في المستقبل.'
                            )
                    except ValueError:
                        errors.append(
                            f'تاريخ المجموعة {gi + 1} في "{q.display_text}" غير صالح.'
                        )
                if len(group_files) > MAX_PER_GROUP:
                    errors.append(
                        f'الحد الأقصى للملفات في كل مجموعة هو {MAX_PER_GROUP} '
                        f'في "{q.display_text}".'
                    )

            for uploaded in all_files:
                if q.max_file_size_mb:
                    max_bytes = q.max_file_size_mb * 1024 * 1024
                    if uploaded.size > max_bytes:
                        errors.append(
                            f'الملف "{uploaded.name}" في "{q.display_text}" يتجاوز الحد الأقصى '
                            f'({q.max_file_size_mb} ميغابايت). '
                            f'حجم الملف: {uploaded.size / (1024 * 1024):.1f} ميغابايت.'
                        )
                if q.allowed_extensions:
                    file_ext = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
                    allowed = [ext.lower().lstrip(".") for ext in q.allowed_extensions]
                    if file_ext not in allowed:
                        errors.append(
                            f'صيغة الملف "{uploaded.name}" في "{q.display_text}" غير مسموحة. '
                            f'الصيغ المسموحة: {", ".join(allowed)}.'
                        )
        else:
            if q.is_required and not answers.get(str(q.id)):
                errors.append(f'الحقل "{q.display_text}" مطلوب.')

    # ── Global total file size check (FILE + DATED_FILES combined) ──
    total_bytes = 0
    for q_id_str, data in file_data.items():
        q = next((q for q in questions if str(q.id) == q_id_str), None)
        if not q:
            continue
        if q.field_type == DoctorIntakeQuestion.FieldType.DATED_FILES:
            for _, gf in data:
                total_bytes += sum(f.size for f in gf)
        else:
            total_bytes += sum(f.size for f in data)

    if total_bytes > MAX_TOTAL_BYTES:
        total_mb = total_bytes / (1024 * 1024)
        errors.append(
            f'الحجم الإجمالي لجميع الملفات المرفقة ({total_mb:.1f} ميغابايت) '
            f'يتجاوز الحد الأقصى المسموح ({AppointmentAttachment.MAX_TOTAL_UPLOAD_MB} ميغابايت).'
        )

    return answers, file_data, errors


def save_intake_answers(appointment, questions, answers_dict, file_data, uploaded_by):
    """
    Create AppointmentAnswer records for each answered question,
    and AppointmentAttachment records for file uploads.

    file_data format differs by type:
      FILE:        {q_id_str: [UploadedFile, ...]}
      DATED_FILES: {q_id_str: [(date_str, [UploadedFile, ...]), ...]}
    """
    from datetime import datetime as dt_cls

    answer_objects = []
    for q in questions:
        text = answers_dict.get(str(q.id), "")
        if text or str(q.id) in file_data:
            answer_objects.append(
                AppointmentAnswer(
                    appointment=appointment,
                    question=q,
                    answer_text=text,
                )
            )
    if answer_objects:
        AppointmentAnswer.objects.bulk_create(answer_objects)

    # Save file attachments
    for q_id_str, data in file_data.items():
        q = next((q for q in questions if str(q.id) == q_id_str), None)
        if not q:
            continue

        if q.field_type == DoctorIntakeQuestion.FieldType.DATED_FILES:
            # data = [(date_str, [files]), ...]
            for group_date_str, group_files in data:
                group_date = None
                if group_date_str:
                    try:
                        group_date = dt_cls.strptime(group_date_str, "%Y-%m-%d").date()
                    except ValueError:
                        pass
                for uploaded_file in group_files:
                    AppointmentAttachment.objects.create(
                        appointment=appointment,
                        question=q,
                        file=uploaded_file,
                        original_name=uploaded_file.name,
                        file_size=uploaded_file.size,
                        mime_type=getattr(uploaded_file, "content_type", ""),
                        file_group_date=group_date,
                        uploaded_by=uploaded_by,
                    )
        else:
            # FILE: data = [UploadedFile, ...]
            for uploaded_file in data:
                AppointmentAttachment.objects.create(
                    appointment=appointment,
                    question=q,
                    file=uploaded_file,
                    original_name=uploaded_file.name,
                    file_size=uploaded_file.size,
                    mime_type=getattr(uploaded_file, "content_type", ""),
                    uploaded_by=uploaded_by,
                )
