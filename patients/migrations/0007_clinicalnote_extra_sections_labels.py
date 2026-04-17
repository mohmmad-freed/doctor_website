from django.db import migrations, models


# Fixed display labels for sections that are stored in extra_sections under
# a known string key (not a numeric element PK).  These match the runtime
# constant _EXTRA_SECTION_DISPLAY_LABELS in doctors/views.py.
_STATIC_EXTRA_LABELS = {
    "vitals":             "Vitals",
    "body_diagram_notes": "Body Diagram",
    "dental_notes":       "Dental Chart",
}


def backfill_extra_sections_labels(apps, schema_editor):
    """
    Back-fill extra_sections_labels for notes that were saved before this
    migration was introduced.

    Resolution priority for each key in extra_sections:
    1. Static known keys (vitals / body_diagram_notes / dental_notes)
       → use the constant label from _STATIC_EXTRA_LABELS.
    2. Numeric keys (CUSTOM element PKs)
       → look up the still-existing ClinicalNoteTemplateElement.custom_label.
       If the element has already been deleted, the key is omitted from the
       snapshot (the live-lookup fallback will render "Custom Section" for it,
       same as before this migration).

    This is best-effort: only elements that still exist can be back-filled.
    Future saves will always write a complete snapshot regardless.
    """
    ClinicalNote = apps.get_model("patients", "ClinicalNote")
    ClinicalNoteTemplateElement = apps.get_model(
        "doctors", "ClinicalNoteTemplateElement"
    )

    notes = list(ClinicalNote.objects.exclude(extra_sections={}))
    if not notes:
        return

    # Collect every numeric key referenced across all notes
    custom_elem_ids = set()
    for note in notes:
        for key in note.extra_sections:
            if key in _STATIC_EXTRA_LABELS:
                continue
            try:
                custom_elem_ids.add(int(key))
            except (ValueError, TypeError):
                pass

    elem_label_map = {}
    if custom_elem_ids:
        elem_label_map = dict(
            ClinicalNoteTemplateElement.objects.filter(
                id__in=custom_elem_ids
            ).values_list("id", "custom_label")
        )

    to_update = []
    for note in notes:
        labels = {}
        for key in note.extra_sections:
            if key in _STATIC_EXTRA_LABELS:
                labels[key] = _STATIC_EXTRA_LABELS[key]
            else:
                try:
                    eid = int(key)
                    label = elem_label_map.get(eid)
                    if label is not None:
                        # Element still exists — capture its current label.
                        # If label is "" (empty custom_label), skip so the
                        # live fallback can handle it.
                        labels[key] = label
                except (ValueError, TypeError):
                    pass  # unknown key format — skip

        if labels:
            note.extra_sections_labels = labels
            to_update.append(note)

    if to_update:
        ClinicalNote.objects.bulk_update(to_update, ["extra_sections_labels"])


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0006_clinicalnote_extra_sections"),
    ]

    operations = [
        migrations.AddField(
            model_name="clinicalnote",
            name="extra_sections_labels",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Snapshot of section display labels written at note-save time. "
                    "Keys mirror extra_sections keys. This is the source of truth "
                    "for rendering historical notes — future template edits cannot "
                    "retroactively rename saved note sections."
                ),
            ),
        ),
        migrations.RunPython(
            backfill_extra_sections_labels,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
