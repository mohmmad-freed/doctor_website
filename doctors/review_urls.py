"""Doctor-review action endpoints, mounted at /reviews/ (middleware-safe for
patients). Display of reviews lives in the browse app; these are the writes."""
from django.urls import path

from . import review_views

app_name = "reviews"

urlpatterns = [
    path("doctor/<int:doctor_id>/submit/", review_views.submit_review, name="submit"),
    path("<int:review_id>/report/", review_views.report_review, name="report"),
    path("<int:review_id>/hide/", review_views.hide_review, name="hide"),
    path("<int:review_id>/unhide/", review_views.unhide_review, name="unhide"),
]
