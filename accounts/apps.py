from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        # Register deploy-time security system checks.
        from accounts import checks  # noqa: F401
