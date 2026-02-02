from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import LoginSerializer


class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = LoginSerializer


class LogoutAPIView(APIView):
    """
    API View to handle user logout by blacklisting the refresh token.
    If the blacklist app is not installed, it returns success (client-side logout).
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh_token")
            if refresh_token:
                from rest_framework_simplejwt.tokens import RefreshToken

                token = RefreshToken(refresh_token)
                token.blacklist()
        except Exception:
            # - TokenError: Token is invalid/expired (already logged out) -> Pass
            # - ImportError: simplejwt.token_blacklist app not installed -> Pass
            # - AttributeError: blacklist() method missing or fails -> Pass
            # Goal is idempotency: if it fails, we assume successful "logout" state
            pass

        return Response(
            {"detail": "Successfully logged out."}, status=status.HTTP_200_OK
        )
