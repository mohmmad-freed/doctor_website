from django.test import TestCase, Client
from django.urls import reverse
from django.core.exceptions import ValidationError

from accounts.models import CustomUser
from clinics.models import Clinic, ClinicStaff
from doctors.models import ClinicalNoteTemplate, ClinicalNoteTemplateElement, DoctorClinicalNoteSettings
from doctors.clinical_note_template_service import create_clinical_note_template, update_clinical_note_template
from doctors.views import (
    _collect_extra_sections,
    _collect_extra_sections_labels,
    _annotate_notes_with_labeled_extras,
    _get_active_note_sections,
    _extract_note_field,
)
from patients.models import ClinicalNote, ClinicPatient

class ClinicalNoteTemplateServiceTests(TestCase):
    def setUp(self):
        self.doctor = CustomUser.objects.create_user(
            phone="0591231231",
            password="testpass123",
            name="Dr. Template",
            role="DOCTOR",
            roles=["DOCTOR"]
        )
        self.clinic = Clinic.objects.create(name="Service Clinic", is_active=True, main_doctor=self.doctor)
        ClinicStaff.objects.create(user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True)

    def test_create_template_success(self):
        section_types = ["SUBJECTIVE", "FREE_TEXT", "FREE_TEXT", "VITALS"]
        section_labels = ["", "General Note", "Specific Note", ""]

        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="My Assessment Template",
            description="Testing template",
            section_types=section_types,
            section_labels=section_labels
        )

        self.assertEqual(tpl.name, "My Assessment Template")
        self.assertEqual(tpl.elements.count(), 4)

        # Checking ordering and values
        elements = list(tpl.elements.order_by('order'))
        self.assertEqual(elements[0].element_type, "SUBJECTIVE")
        self.assertEqual(elements[1].element_type, "FREE_TEXT")
        self.assertEqual(elements[1].custom_label, "General Note")
        self.assertEqual(elements[2].element_type, "FREE_TEXT")
        self.assertEqual(elements[2].custom_label, "Specific Note")

    def test_validation_missing_name(self):
        with self.assertRaises(ValidationError) as ctx:
            create_clinical_note_template(
                doctor=self.doctor,
                name="  ",
                description="Desc",
                section_types=["SUBJECTIVE"],
                section_labels=[""]
            )
        self.assertIn("Template name is required", str(ctx.exception))

    def test_validation_empty_sections(self):
        with self.assertRaises(ValidationError) as ctx:
            create_clinical_note_template(
                doctor=self.doctor,
                name="Valid Name",
                description="Desc",
                section_types=[],
                section_labels=[]
            )
        self.assertIn("At least one section", str(ctx.exception))

    def test_validation_invalid_type(self):
        with self.assertRaises(ValidationError) as ctx:
            create_clinical_note_template(
                doctor=self.doctor,
                name="Valid Name",
                description="Desc",
                section_types=["SUBJECTIVE", "INVALID_HACK"],
                section_labels=["", "Hack"]
            )
        self.assertIn("Invalid section type", str(ctx.exception))

    def test_update_template_reorders_and_modifies(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Initial Template",
            description="",
            section_types=["SUBJECTIVE", "OBJECTIVE"],
            section_labels=["", ""]
        )

        # Reverse order and add a custom generic section
        updated_tpl = update_clinical_note_template(
            template_id=tpl.id,
            doctor=self.doctor,
            name="Updated Template",
            description="New description",
            section_types=["OBJECTIVE", "SUBJECTIVE", "CUSTOM"],
            section_labels=["", "", "Doctor Diary"]
        )

        self.assertEqual(updated_tpl.name, "Updated Template")
        elements = list(updated_tpl.elements.order_by('order'))
        self.assertEqual(len(elements), 3)
        self.assertEqual(elements[0].element_type, "OBJECTIVE")
        self.assertEqual(elements[1].element_type, "SUBJECTIVE")
        self.assertEqual(elements[2].element_type, "CUSTOM")
        self.assertEqual(elements[2].custom_label, "Doctor Diary")

    def test_order_field_is_sequential_zero_based(self):
        """Saved order values must be 0, 1, 2, … matching the submitted list index."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Order Integrity",
            description="",
            section_types=["VITALS", "ASSESSMENT", "PLAN", "FREE_TEXT"],
            section_labels=["", "", "", "Notes"],
        )
        elements = list(tpl.elements.order_by("order"))
        for idx, elem in enumerate(elements):
            self.assertEqual(elem.order, idx, f"Element at position {idx} has order={elem.order}")

    def test_repeated_section_types_preserve_order(self):
        """Multiple sections of the same type must each retain their position."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Repeated Types",
            description="",
            section_types=["FREE_TEXT", "FREE_TEXT", "FREE_TEXT"],
            section_labels=["Alpha", "Beta", "Gamma"],
        )
        elements = list(tpl.elements.order_by("order"))
        self.assertEqual(len(elements), 3)
        self.assertEqual(elements[0].custom_label, "Alpha")
        self.assertEqual(elements[1].custom_label, "Beta")
        self.assertEqual(elements[2].custom_label, "Gamma")

    def test_drag_reorder_persists_via_update(self):
        """
        Simulates the effect of drag-and-drop reordering followed by form submit.

        The frontend sends section_type/section_label lists in the final DOM order.
        The service must save that exact order to the database.
        Reloading via order_by('order') must reproduce the same sequence.
        """
        # Create template with initial order A→B→C→D
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Drag Target",
            description="",
            section_types=["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN"],
            section_labels=["", "", "", ""],
        )

        # Simulate doctor dragging PLAN to position 0, giving order D→A→B→C
        updated = update_clinical_note_template(
            template_id=tpl.id,
            doctor=self.doctor,
            name="Drag Target",
            description="",
            section_types=["PLAN", "SUBJECTIVE", "OBJECTIVE", "ASSESSMENT"],
            section_labels=["", "", "", ""],
        )

        elements = list(updated.elements.order_by("order"))
        self.assertEqual(elements[0].element_type, "PLAN")
        self.assertEqual(elements[1].element_type, "SUBJECTIVE")
        self.assertEqual(elements[2].element_type, "OBJECTIVE")
        self.assertEqual(elements[3].element_type, "ASSESSMENT")
        # Verify order integers are correct
        self.assertEqual([e.order for e in elements], [0, 1, 2, 3])

    def test_drag_reorder_with_custom_sections(self):
        """Custom (CUSTOM type) sections carry their label through reorder."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Custom Drag",
            description="",
            section_types=["CUSTOM", "SUBJECTIVE", "CUSTOM"],
            section_labels=["First Custom", "", "Second Custom"],
        )

        # Drag: move SUBJECTIVE to top → [SUBJECTIVE, CUSTOM(First), CUSTOM(Second)]
        updated = update_clinical_note_template(
            template_id=tpl.id,
            doctor=self.doctor,
            name="Custom Drag",
            description="",
            section_types=["SUBJECTIVE", "CUSTOM", "CUSTOM"],
            section_labels=["", "First Custom", "Second Custom"],
        )

        elements = list(updated.elements.order_by("order"))
        self.assertEqual(elements[0].element_type, "SUBJECTIVE")
        self.assertEqual(elements[0].custom_label, "")
        self.assertEqual(elements[1].element_type, "CUSTOM")
        self.assertEqual(elements[1].custom_label, "First Custom")
        self.assertEqual(elements[2].element_type, "CUSTOM")
        self.assertEqual(elements[2].custom_label, "Second Custom")


class ClinicalNoteTemplateViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.doctor = CustomUser.objects.create_user(
            phone="0591231232",
            password="testpass123",
            name="Dr. View Template",
            role="DOCTOR",
            roles=["DOCTOR"]
        )
        self.clinic = Clinic.objects.create(name="View Clinic", is_active=True, main_doctor=self.doctor)
        ClinicStaff.objects.create(user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True)

        self.client.login(phone="0591231232", password="testpass123")

        self.tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Legacy Setup",
            description="",
            section_types=["SUBJECTIVE", "CUSTOM"],
            section_labels=["", "Old Custom Data"]
        )

    def test_edit_view_loads_legacy_custom_correctly(self):
        """Legacy elements should load natively into the unified builder."""
        url = reverse("doctors:clinical_note_template_edit", args=[self.tpl.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Verify both contexts and HTML contains correct structures
        content = response.content.decode('utf-8')

        # It should contain the legacy custom label rendered within the input
        self.assertIn('value="Old Custom Data"', content)

        # It should have exactly four rows instantiated via Django templates before JS acts:
        # 2 live-rendered rows (SUBJECTIVE + CUSTOM) + 2 <template> blocks
        self.assertEqual(content.count('class="section-row flex'), 4)

    def test_drag_handle_present_in_rendered_rows(self):
        """Every rendered row (live and template blocks) must contain a drag handle."""
        url = reverse("doctors:clinical_note_template_edit", args=[self.tpl.id])
        response = self.client.get(url)
        content = response.content.decode('utf-8')

        # fa-grip-vertical appears once per row (live) and once per <template> block
        self.assertEqual(content.count('fa-grip-vertical'), 4)

        # Up/Down chevron buttons must NOT appear
        self.assertNotIn('fa-chevron-up', content)
        self.assertNotIn('fa-chevron-down', content)

    def test_create_via_post(self):
        url = reverse("doctors:clinical_note_template_create")
        data = {
            "name": "Integration Test Template",
            "description": "Integration Test Desc",
            "section_type": ["SUBJECTIVE", "FREE_TEXT"],
            "section_label": ["", "Named Section"]
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302) # Redirect to templates list

        tpl = ClinicalNoteTemplate.objects.get(name="Integration Test Template")
        self.assertEqual(tpl.elements.count(), 2)

    def test_post_order_matches_submission_order(self):
        """
        The backend must save elements in exactly the order they appear in the
        POST payload — reflecting whatever order the drag-and-drop UI produced.
        """
        url = reverse("doctors:clinical_note_template_create")
        # Submit in a deliberate non-alphabetical order
        data = {
            "name": "Order Via POST",
            "description": "",
            "section_type": ["PLAN", "VITALS", "SUBJECTIVE"],
            "section_label": ["", "", ""],
        }
        self.client.post(url, data)
        tpl = ClinicalNoteTemplate.objects.get(name="Order Via POST")
        elements = list(tpl.elements.order_by("order"))
        self.assertEqual(elements[0].element_type, "PLAN")
        self.assertEqual(elements[1].element_type, "VITALS")
        self.assertEqual(elements[2].element_type, "SUBJECTIVE")

    def test_edit_post_reorder_persists_on_reload(self):
        """
        After submitting a reordered section list via POST (as drag-and-drop does),
        reloading the edit page must show elements in the same saved order.
        """
        url = reverse("doctors:clinical_note_template_edit", args=[self.tpl.id])
        # Original order: SUBJECTIVE(0), CUSTOM(1).
        # Simulate dragging CUSTOM to the top.
        data = {
            "name": "Legacy Setup",
            "description": "",
            "section_type": ["CUSTOM", "SUBJECTIVE"],
            "section_label": ["Old Custom Data", ""],
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)

        # Verify DB order
        elements = list(
            ClinicalNoteTemplate.objects.get(pk=self.tpl.id).elements.order_by("order")
        )
        self.assertEqual(elements[0].element_type, "CUSTOM")
        self.assertEqual(elements[0].custom_label, "Old Custom Data")
        self.assertEqual(elements[1].element_type, "SUBJECTIVE")

        # Verify reload order matches by checking rendered HTML order
        reload = self.client.get(url)
        content = reload.content.decode("utf-8")
        custom_pos    = content.find('value="Old Custom Data"')
        subjective_pos = content.find('value="SUBJECTIVE"')
        self.assertGreater(subjective_pos, custom_pos,
            "After drag reorder, CUSTOM must appear before SUBJECTIVE in the HTML")


# ─────────────────────────────────────────────────────────────────────────────
# Bug-fix tests: Custom sections appearing in the clinical note editor
# ─────────────────────────────────────────────────────────────────────────────

class CollectExtraSectionsTests(TestCase):
    """Unit tests for the _collect_extra_sections POST-data parser."""

    def _post(self, data):
        """Wrap a dict in a QueryDict-like object (Django's test client gives us
        a plain dict for unit tests, but the real function calls .get() and .items()
        which work on plain dicts too)."""
        return data

    def test_vitals_captured(self):
        extra = _collect_extra_sections({"vitals": "BP 120/80, HR 72"})
        self.assertEqual(extra["vitals"], "BP 120/80, HR 72")

    def test_body_diagram_captured(self):
        extra = _collect_extra_sections({"body_diagram_notes": "Pain in right knee"})
        self.assertEqual(extra["body_diagram_notes"], "Pain in right knee")

    def test_dental_captured(self):
        extra = _collect_extra_sections({"dental_notes": "Cavity on tooth 14"})
        self.assertEqual(extra["dental_notes"], "Cavity on tooth 14")

    def test_custom_section_key_stripped_of_prefix(self):
        extra = _collect_extra_sections({"custom_section_42": "Doctor's diary entry"})
        self.assertIn("42", extra)
        self.assertEqual(extra["42"], "Doctor's diary entry")

    def test_empty_values_not_stored(self):
        extra = _collect_extra_sections({
            "vitals": "   ",
            "body_diagram_notes": "",
            "custom_section_7": "",
        })
        self.assertEqual(extra, {})

    def test_soap_fields_ignored(self):
        """Standard SOAP fields are handled by their own model columns, not extra_sections."""
        extra = _collect_extra_sections({
            "subjective": "I feel tired",
            "objective": "Looks fine",
            "assessment": "Fatigue",
            "plan": "Rest",
            "free_text": "Notes",
        })
        self.assertEqual(extra, {})

    def test_multiple_custom_sections_all_captured(self):
        extra = _collect_extra_sections({
            "custom_section_1": "Entry for elem 1",
            "custom_section_2": "Entry for elem 2",
            "vitals": "T 37.0",
        })
        self.assertEqual(extra["1"], "Entry for elem 1")
        self.assertEqual(extra["2"], "Entry for elem 2")
        self.assertEqual(extra["vitals"], "T 37.0")
        self.assertEqual(len(extra), 3)


class ExtractNoteFieldTests(TestCase):
    """Unit tests for _extract_note_field — reads correct field per element type."""

    def _make_note(self, **kwargs):
        """Construct an unsaved ClinicalNote-like object with the given attributes."""
        note = ClinicalNote.__new__(ClinicalNote)
        note.subjective  = kwargs.get("subjective", "")
        note.objective   = kwargs.get("objective", "")
        note.assessment  = kwargs.get("assessment", "")
        note.plan        = kwargs.get("plan", "")
        note.free_text   = kwargs.get("free_text", "")
        note.extra_sections = kwargs.get("extra_sections", {})
        return note

    def test_standard_soap_fields(self):
        note = self._make_note(subjective="S value", objective="O value",
                               assessment="A value", plan="P value")
        self.assertEqual(_extract_note_field(note, "SUBJECTIVE"), "S value")
        self.assertEqual(_extract_note_field(note, "OBJECTIVE"), "O value")
        self.assertEqual(_extract_note_field(note, "ASSESSMENT"), "A value")
        self.assertEqual(_extract_note_field(note, "PLAN"), "P value")

    def test_free_text_field(self):
        note = self._make_note(free_text="General notes")
        self.assertEqual(_extract_note_field(note, "FREE_TEXT"), "General notes")

    def test_vitals_from_extra_sections(self):
        note = self._make_note(extra_sections={"vitals": "BP 130/85"})
        self.assertEqual(_extract_note_field(note, "VITALS"), "BP 130/85")

    def test_body_diagram_from_extra_sections(self):
        note = self._make_note(extra_sections={"body_diagram_notes": "Left shoulder pain"})
        self.assertEqual(_extract_note_field(note, "BODY_DIAGRAM"), "Left shoulder pain")

    def test_dental_from_extra_sections(self):
        note = self._make_note(extra_sections={"dental_notes": "Wisdom tooth 48"})
        self.assertEqual(_extract_note_field(note, "DENTAL"), "Wisdom tooth 48")

    def test_none_note_returns_empty_string(self):
        self.assertEqual(_extract_note_field(None, "SUBJECTIVE"), "")
        self.assertEqual(_extract_note_field(None, "VITALS"), "")

    def test_missing_extra_section_key_returns_empty(self):
        note = self._make_note(extra_sections={})
        self.assertEqual(_extract_note_field(note, "VITALS"), "")
        self.assertEqual(_extract_note_field(note, "BODY_DIAGRAM"), "")


class GetActiveNoteSectionsTests(TestCase):
    """Unit tests for _get_active_note_sections — template-to-form descriptor list."""

    def setUp(self):
        self.doctor = CustomUser.objects.create_user(
            phone="0591231299",
            password="testpass123",
            name="Dr. Sections",
            role="DOCTOR",
            roles=["DOCTOR"],
        )
        self.clinic = Clinic.objects.create(
            name="Sections Clinic", is_active=True, main_doctor=self.doctor
        )
        ClinicStaff.objects.create(
            user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True
        )

    def _activate_template(self, tpl):
        DoctorClinicalNoteSettings.objects.update_or_create(
            doctor=self.doctor, defaults={"active_template": tpl}
        )

    def test_custom_section_appears_in_correct_position(self):
        """A CUSTOM element defined at order=1 must appear at index 1, not appended last."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Custom Mid",
            description="",
            section_types=["SUBJECTIVE", "CUSTOM", "PLAN"],
            section_labels=["", "My Custom Label", ""],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        self.assertEqual(len(sections), 3)
        self.assertEqual(sections[0]["type"], "SUBJECTIVE")
        self.assertEqual(sections[1]["type"], "CUSTOM")
        self.assertEqual(sections[1]["label"], "My Custom Label")
        self.assertEqual(sections[2]["type"], "PLAN")

    def test_custom_section_name_uses_element_id(self):
        """The textarea name for CUSTOM sections must be custom_section_<elem_id>."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Custom Name Test",
            description="",
            section_types=["CUSTOM"],
            section_labels=["Patient Notes"],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        elem = tpl.elements.first()
        self.assertEqual(sections[0]["name"], f"custom_section_{elem.id}")
        self.assertEqual(sections[0]["elem_id"], elem.id)

    def test_vitals_section_name_is_vitals(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Vitals Template",
            description="",
            section_types=["VITALS", "SUBJECTIVE"],
            section_labels=["", ""],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        self.assertEqual(sections[0]["type"], "VITALS")
        self.assertEqual(sections[0]["name"], "vitals")

    def test_body_diagram_section_name(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Body Diagram Template",
            description="",
            section_types=["BODY_DIAGRAM"],
            section_labels=[""],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        self.assertEqual(sections[0]["name"], "body_diagram_notes")

    def test_dental_section_name(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Dental Template",
            description="",
            section_types=["DENTAL"],
            section_labels=[""],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        self.assertEqual(sections[0]["name"], "dental_notes")

    def test_edit_prefill_soap_values(self):
        """When note is provided, SOAP section values are pre-filled from the note."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Prefill Template",
            description="",
            section_types=["SUBJECTIVE", "OBJECTIVE"],
            section_labels=["", ""],
        )
        self._activate_template(tpl)

        # Build a minimal note object without saving to DB
        note = ClinicalNote.__new__(ClinicalNote)
        note.subjective     = "Patient complains of pain"
        note.objective      = "Normal exam"
        note.assessment     = ""
        note.plan           = ""
        note.free_text      = ""
        note.extra_sections = {}

        sections = _get_active_note_sections(self.doctor, note=note)
        self.assertEqual(sections[0]["value"], "Patient complains of pain")
        self.assertEqual(sections[1]["value"], "Normal exam")

    def test_edit_prefill_custom_section_value(self):
        """Custom section values are pre-filled from note.extra_sections[str(elem_id)]."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Custom Prefill",
            description="",
            section_types=["CUSTOM"],
            section_labels=["Doctor Notes"],
        )
        self._activate_template(tpl)

        elem = tpl.elements.first()

        note = ClinicalNote.__new__(ClinicalNote)
        note.subjective     = ""
        note.objective      = ""
        note.assessment     = ""
        note.plan           = ""
        note.free_text      = ""
        note.extra_sections = {str(elem.id): "Saved custom content"}

        sections = _get_active_note_sections(self.doctor, note=note)
        self.assertEqual(sections[0]["value"], "Saved custom content")

    def test_edit_prefill_vitals_from_extra_sections(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Vitals Prefill",
            description="",
            section_types=["VITALS"],
            section_labels=[""],
        )
        self._activate_template(tpl)

        note = ClinicalNote.__new__(ClinicalNote)
        note.subjective = note.objective = note.assessment = note.plan = note.free_text = ""
        note.extra_sections = {"vitals": "BP 120/80"}

        sections = _get_active_note_sections(self.doctor, note=note)
        self.assertEqual(sections[0]["value"], "BP 120/80")

    def test_new_note_values_are_empty(self):
        """Without a note, all value fields must be empty strings."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Empty Values",
            description="",
            section_types=["SUBJECTIVE", "CUSTOM", "VITALS"],
            section_labels=["", "Label", ""],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        for section in sections:
            self.assertEqual(section["value"], "",
                f"Section {section['type']} should have empty value for new note")

    def test_ordering_matches_template_order(self):
        """Sections returned must follow the template element order, not alphabetical."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Order Check",
            description="",
            section_types=["PLAN", "VITALS", "SUBJECTIVE", "CUSTOM"],
            section_labels=["", "", "", "Extra"],
        )
        self._activate_template(tpl)

        sections = _get_active_note_sections(self.doctor)
        self.assertEqual(sections[0]["type"], "PLAN")
        self.assertEqual(sections[1]["type"], "VITALS")
        self.assertEqual(sections[2]["type"], "SUBJECTIVE")
        self.assertEqual(sections[3]["type"], "CUSTOM")


class NoteEditorIntegrationTests(TestCase):
    """Integration tests: custom section POST → DB → edit pre-fill → display."""

    def setUp(self):
        self.client = Client()
        self.doctor = CustomUser.objects.create_user(
            phone="0591231298",
            password="testpass123",
            name="Dr. Integration",
            role="DOCTOR",
            roles=["DOCTOR"],
        )
        self.patient = CustomUser.objects.create_user(
            phone="0591231297",
            password="testpass123",
            name="Integration Patient",
            role="PATIENT",
            roles=["PATIENT"],
        )
        self.clinic = Clinic.objects.create(
            name="Integration Clinic", is_active=True, main_doctor=self.doctor
        )
        ClinicStaff.objects.create(
            user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True
        )
        ClinicPatient.objects.create(
            patient=self.patient, clinic=self.clinic
        )

        self.client.login(phone="0591231298", password="testpass123")

        # Create a custom template with SUBJECTIVE + CUSTOM + VITALS
        self.tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Integration Template",
            description="",
            section_types=["SUBJECTIVE", "CUSTOM", "VITALS"],
            section_labels=["", "Doctor Diary", ""],
        )
        DoctorClinicalNoteSettings.objects.create(
            doctor=self.doctor, active_template=self.tpl
        )
        self.custom_elem = self.tpl.elements.filter(
            element_type=ClinicalNoteTemplateElement.ElementType.CUSTOM
        ).first()

    def _add_url(self):
        return reverse("doctors:ws_note_add", args=[self.patient.pk])

    def _edit_url(self, note_id):
        return reverse("doctors:ws_note_edit", args=[self.patient.pk, note_id])

    def _delete_url(self, note_id):
        return reverse("doctors:ws_note_delete", args=[self.patient.pk, note_id])

    def test_add_note_custom_section_is_saved(self):
        """POSTing a value for a custom section must persist it in extra_sections."""
        resp = self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            "subjective": "Chief complaint",
            f"custom_section_{self.custom_elem.id}": "My diary entry",
            "vitals": "",
        })
        self.assertEqual(resp.status_code, 200)
        note = ClinicalNote.objects.filter(patient=self.patient).latest("created_at")
        self.assertEqual(note.subjective, "Chief complaint")
        self.assertIn(str(self.custom_elem.id), note.extra_sections)
        self.assertEqual(note.extra_sections[str(self.custom_elem.id)], "My diary entry")

    def test_add_note_vitals_saved_in_extra_sections(self):
        resp = self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            "vitals": "BP 115/75, HR 68",
        })
        self.assertEqual(resp.status_code, 200)
        note = ClinicalNote.objects.filter(patient=self.patient).latest("created_at")
        self.assertIn("vitals", note.extra_sections)
        self.assertEqual(note.extra_sections["vitals"], "BP 115/75, HR 68")

    def test_add_note_response_contains_custom_section_textarea(self):
        """The ws_notes partial returned after POST must include the custom section textarea."""
        resp = self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            "subjective": "Test",
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn(f"custom_section_{self.custom_elem.id}", content)

    def test_add_note_response_contains_vitals_textarea(self):
        resp = self.client.post(self._add_url(), {"clinic_id": self.clinic.pk})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn('name="vitals"', content)

    def test_edit_note_prefills_custom_section_value(self):
        """GET on ws_note_edit must render the custom section textarea pre-filled."""
        note = ClinicalNote.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            extra_sections={str(self.custom_elem.id): "Pre-filled diary"},
        )
        resp = self.client.get(self._edit_url(note.pk))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Pre-filled diary", content)

    def test_edit_note_prefills_vitals(self):
        note = ClinicalNote.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            extra_sections={"vitals": "T 36.8°C"},
        )
        resp = self.client.get(self._edit_url(note.pk))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("T 36.8°C", content)

    def test_edit_post_updates_custom_section(self):
        note = ClinicalNote.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            extra_sections={str(self.custom_elem.id): "Old content"},
        )
        resp = self.client.post(self._edit_url(note.pk), {
            f"custom_section_{self.custom_elem.id}": "Updated content",
        })
        self.assertEqual(resp.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(
            note.extra_sections[str(self.custom_elem.id)], "Updated content"
        )

    def test_delete_note_response_still_contains_active_note_sections(self):
        """After deletion the returned partial must still include the template sections form."""
        note = ClinicalNote.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
        )
        resp = self.client.post(self._delete_url(note.pk))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        # The custom section textarea must still be rendered in the empty add-form
        self.assertIn(f"custom_section_{self.custom_elem.id}", content)

    def test_saved_note_body_renders_labeled_extras(self):
        """
        A note with extra_sections content must render those sections
        in the note display with their human-readable label.
        """
        note = ClinicalNote.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            extra_sections={
                str(self.custom_elem.id): "Annual review notes",
                "vitals": "BP 118/76",
            },
        )
        # Reload note list via GET on the patient workspace notes tab
        url = reverse("doctors:patient_workspace", args=[self.patient.pk]) + "?tab=notes"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        # Both values must appear in the note body display
        self.assertIn("Annual review notes", content)
        self.assertIn("BP 118/76", content)
        # The custom label "Doctor Diary" should be displayed as the section heading
        self.assertIn("Doctor Diary", content)


# ─────────────────────────────────────────────────────────────────────────────
# Historical label preservation tests (extra_sections_labels snapshot)
# ─────────────────────────────────────────────────────────────────────────────

class CollectExtraSectionsLabelsTests(TestCase):
    """Unit tests for _collect_extra_sections_labels — snapshot builder."""

    def setUp(self):
        self.doctor = CustomUser.objects.create_user(
            phone="0591231280",
            password="testpass123",
            name="Dr. Snapshot",
            role="DOCTOR",
            roles=["DOCTOR"],
        )
        self.clinic = Clinic.objects.create(
            name="Snapshot Clinic", is_active=True, main_doctor=self.doctor
        )
        ClinicStaff.objects.create(
            user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True
        )
        DoctorClinicalNoteSettings.objects.create(
            doctor=self.doctor, active_template=None
        )

    def _activate(self, tpl):
        DoctorClinicalNoteSettings.objects.filter(doctor=self.doctor).update(
            active_template=tpl
        )

    def test_custom_section_label_captured(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Label Capture",
            description="",
            section_types=["CUSTOM"],
            section_labels=["hello"],
        )
        self._activate(tpl)
        sections = _get_active_note_sections(self.doctor)
        snap = _collect_extra_sections_labels(sections)
        elem = tpl.elements.first()
        self.assertIn(str(elem.id), snap)
        self.assertEqual(snap[str(elem.id)], "hello")

    def test_vitals_label_captured(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Vitals Label",
            description="",
            section_types=["VITALS"],
            section_labels=[""],
        )
        self._activate(tpl)
        sections = _get_active_note_sections(self.doctor)
        snap = _collect_extra_sections_labels(sections)
        self.assertIn("vitals", snap)

    def test_soap_sections_not_in_snapshot(self):
        """SOAP sections have dedicated model columns — they must not appear in the labels snapshot."""
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="SOAP Only",
            description="",
            section_types=["SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN", "FREE_TEXT"],
            section_labels=["", "", "", "", ""],
        )
        self._activate(tpl)
        sections = _get_active_note_sections(self.doctor)
        snap = _collect_extra_sections_labels(sections)
        self.assertEqual(snap, {})

    def test_multiple_custom_sections_all_captured(self):
        tpl = create_clinical_note_template(
            doctor=self.doctor,
            name="Multi Custom",
            description="",
            section_types=["CUSTOM", "CUSTOM", "VITALS"],
            section_labels=["First Label", "Second Label", ""],
        )
        self._activate(tpl)
        sections = _get_active_note_sections(self.doctor)
        snap = _collect_extra_sections_labels(sections)
        elems = list(tpl.elements.filter(element_type="CUSTOM").order_by("order"))
        self.assertEqual(snap[str(elems[0].id)], "First Label")
        self.assertEqual(snap[str(elems[1].id)], "Second Label")
        self.assertIn("vitals", snap)


class AnnotateNotesWithLabeledExtrasTests(TestCase):
    """Unit tests for _annotate_notes_with_labeled_extras — snapshot-first resolution."""

    def setUp(self):
        self.doctor = CustomUser.objects.create_user(
            phone="0591231279",
            password="testpass123",
            name="Dr. Annotate",
            role="DOCTOR",
            roles=["DOCTOR"],
        )
        self.patient = CustomUser.objects.create_user(
            phone="0591231278",
            password="testpass123",
            name="Annotate Patient",
            role="PATIENT",
            roles=["PATIENT"],
        )
        self.clinic = Clinic.objects.create(
            name="Annotate Clinic", is_active=True, main_doctor=self.doctor
        )
        ClinicStaff.objects.create(
            user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True
        )
        ClinicPatient.objects.create(patient=self.patient, clinic=self.clinic)

    def _make_note(self, extra_sections=None, extra_sections_labels=None):
        return ClinicalNote.objects.create(
            patient=self.patient,
            clinic=self.clinic,
            doctor=self.doctor,
            extra_sections=extra_sections or {},
            extra_sections_labels=extra_sections_labels or {},
        )

    def test_snapshot_label_used_when_element_exists(self):
        """Snapshot wins even when the live element exists with a different label."""
        tpl = create_clinical_note_template(
            doctor=self.doctor, name="T", description="",
            section_types=["CUSTOM"], section_labels=["original label"],
        )
        elem = tpl.elements.first()
        note = self._make_note(
            extra_sections={str(elem.id): "content"},
            extra_sections_labels={str(elem.id): "original label"},
        )
        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(note.labeled_extras[0]["label"], "original label")

    def test_snapshot_survives_element_deletion(self):
        """
        THE KEY REGRESSION TEST.
        Create a template with a custom section labeled "hello", save a note,
        delete the template element, then annotate — the note must still show "hello".
        """
        tpl = create_clinical_note_template(
            doctor=self.doctor, name="Hello Tpl", description="",
            section_types=["CUSTOM"], section_labels=["hello"],
        )
        elem = tpl.elements.first()
        elem_id = elem.id

        # Save the note WITH a snapshot (as ws_note_add now does)
        note = self._make_note(
            extra_sections={str(elem_id): "my content"},
            extra_sections_labels={str(elem_id): "hello"},
        )

        # Simulate doctor deleting the section from the template
        elem.delete()

        # Annotate — must resolve "hello" from snapshot, not fall back to "Custom Section"
        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(len(note.labeled_extras), 1)
        self.assertEqual(note.labeled_extras[0]["label"], "hello")
        self.assertEqual(note.labeled_extras[0]["value"], "my content")

    def test_no_snapshot_and_element_deleted_falls_back_to_generic(self):
        """
        Pre-migration notes with no snapshot and a deleted element fall back to
        'Custom Section' — this is the old (unchanged) behavior for notes that
        existed before the snapshot was introduced AND whose element was deleted
        before the backfill migration ran.
        """
        tpl = create_clinical_note_template(
            doctor=self.doctor, name="Old Tpl", description="",
            section_types=["CUSTOM"], section_labels=["was here"],
        )
        elem = tpl.elements.first()
        elem_id = elem.id

        # Old-style note: no snapshot
        note = self._make_note(extra_sections={str(elem_id): "old content"})

        # Delete the element before snapshot was possible
        elem.delete()

        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(note.labeled_extras[0]["label"], "Custom Section")

    def test_no_snapshot_but_element_still_exists_uses_live_label(self):
        """Backward compat: pre-migration note without snapshot, element still exists."""
        tpl = create_clinical_note_template(
            doctor=self.doctor, name="Live Tpl", description="",
            section_types=["CUSTOM"], section_labels=["live label"],
        )
        elem = tpl.elements.first()
        note = self._make_note(extra_sections={str(elem.id): "live content"})
        # No extra_sections_labels — simulates a pre-migration note

        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(note.labeled_extras[0]["label"], "live label")

    def test_vitals_always_resolved_correctly(self):
        note = self._make_note(
            extra_sections={"vitals": "BP 120/80"},
            extra_sections_labels={"vitals": "Vitals"},
        )
        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(note.labeled_extras[0]["label"], "Vitals")

    def test_empty_values_excluded_from_labeled_extras(self):
        note = self._make_note(
            extra_sections={"vitals": "", "dental_notes": "present"},
            extra_sections_labels={"vitals": "Vitals", "dental_notes": "Dental Chart"},
        )
        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(len(note.labeled_extras), 1)
        self.assertEqual(note.labeled_extras[0]["label"], "Dental Chart")


class HistoricalLabelPreservationIntegrationTests(TestCase):
    """
    End-to-end tests for the success criteria from the bug report.

    Success criteria:
    1. Create a template with a CUSTOM section labeled "hello"
    2. Save a clinical note using it
    3. Delete the section from the template and save the template
    4. View the old note — must still show "hello", not "Custom Section"
    5. Create a new note — reflects updated template only
    """

    def setUp(self):
        self.client = Client()
        self.doctor = CustomUser.objects.create_user(
            phone="0591231277",
            password="testpass123",
            name="Dr. History",
            role="DOCTOR",
            roles=["DOCTOR"],
        )
        self.patient = CustomUser.objects.create_user(
            phone="0591231276",
            password="testpass123",
            name="History Patient",
            role="PATIENT",
            roles=["PATIENT"],
        )
        self.clinic = Clinic.objects.create(
            name="History Clinic", is_active=True, main_doctor=self.doctor
        )
        ClinicStaff.objects.create(
            user=self.doctor, clinic=self.clinic, role="DOCTOR", is_active=True
        )
        ClinicPatient.objects.create(patient=self.patient, clinic=self.clinic)
        self.client.login(phone="0591231277", password="testpass123")

        # Template with CUSTOM section "hello"
        self.tpl = create_clinical_note_template(
            doctor=self.doctor, name="Hello Template", description="",
            section_types=["SUBJECTIVE", "CUSTOM"],
            section_labels=["", "hello"],
        )
        DoctorClinicalNoteSettings.objects.create(
            doctor=self.doctor, active_template=self.tpl
        )
        self.custom_elem = self.tpl.elements.get(
            element_type=ClinicalNoteTemplateElement.ElementType.CUSTOM
        )

    def _add_url(self):
        return reverse("doctors:ws_note_add", args=[self.patient.pk])

    def test_label_snapshot_stored_at_note_create(self):
        """ws_note_add must write extra_sections_labels when it saves."""
        resp = self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            f"custom_section_{self.custom_elem.id}": "My note content",
        })
        self.assertEqual(resp.status_code, 200)
        note = ClinicalNote.objects.filter(patient=self.patient).latest("created_at")
        self.assertIn(str(self.custom_elem.id), note.extra_sections_labels)
        self.assertEqual(note.extra_sections_labels[str(self.custom_elem.id)], "hello")

    def test_deleting_template_section_does_not_rename_old_note(self):
        """
        THE PRIMARY REGRESSION TEST — mirrors the exact bug report scenario.
        """
        # Step 1: save a note with content in the "hello" section
        self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            f"custom_section_{self.custom_elem.id}": "Important clinical finding",
        })
        note = ClinicalNote.objects.filter(patient=self.patient).latest("created_at")
        self.assertEqual(note.extra_sections_labels[str(self.custom_elem.id)], "hello")

        # Step 2: doctor deletes the custom section from the template
        elem_id = self.custom_elem.id
        self.custom_elem.delete()

        # Step 3: view the old note — must still show "hello"
        note.refresh_from_db()
        _annotate_notes_with_labeled_extras([note])
        self.assertEqual(len(note.labeled_extras), 1)
        self.assertEqual(note.labeled_extras[0]["label"], "hello",
            "After deleting the template element, the historical note label must "
            "still read 'hello', not 'Custom Section'.")

    def test_label_snapshot_updated_on_note_edit(self):
        """
        When the doctor edits a saved note, the snapshot is refreshed from the
        current template state at edit time.
        """
        # Create initial note
        self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            f"custom_section_{self.custom_elem.id}": "Original content",
        })
        note = ClinicalNote.objects.filter(patient=self.patient).latest("created_at")
        self.assertEqual(note.extra_sections_labels[str(self.custom_elem.id)], "hello")

        # Edit via the edit endpoint
        edit_url = reverse("doctors:ws_note_edit", args=[self.patient.pk, note.pk])
        self.client.post(edit_url, {
            f"custom_section_{self.custom_elem.id}": "Updated content",
        })
        note.refresh_from_db()
        # Snapshot must still contain "hello" (template not changed)
        self.assertEqual(note.extra_sections_labels[str(self.custom_elem.id)], "hello")
        self.assertEqual(note.extra_sections[str(self.custom_elem.id)], "Updated content")

    def test_new_note_after_template_section_deletion_uses_new_template(self):
        """
        After the doctor deletes the 'hello' section from the template, a new note
        form must NOT show that section.
        """
        # Delete the custom section
        self.custom_elem.delete()

        # Request the overview tab — the add-note form should render without "hello"
        url = reverse("doctors:patient_workspace", args=[self.patient.pk]) + "?tab=notes"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        # The deleted element's textarea must not appear
        self.assertNotIn(f"custom_section_{self.custom_elem.id}", content)

    def test_workspace_notes_tab_renders_historical_label(self):
        """
        The notes tab rendered HTML must show the snapshotted label 'hello'
        for the saved note's extra section, even after the element is deleted.
        """
        # Save note with snapshot
        self.client.post(self._add_url(), {
            "clinic_id": self.clinic.pk,
            f"custom_section_{self.custom_elem.id}": "Saved finding",
        })
        note = ClinicalNote.objects.filter(patient=self.patient).latest("created_at")

        # Delete the template element
        self.custom_elem.delete()

        # Load the notes tab
        url = reverse("doctors:patient_workspace", args=[self.patient.pk]) + "?tab=notes"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Saved finding", content)
        self.assertIn("hello", content)
        self.assertNotIn("Custom Section", content)
