from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    name = 'compliance'

    def ready(self):
        import compliance.signals
