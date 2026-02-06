import os
import django
import sys
from django.core.cache import cache

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clinic_website.settings")
django.setup()


def list_otps():
    print("Checking for active Mock OTPs in Redis...")
    # This relies on the implementation details of the specific cache backend
    # But since we know the key format 'otp:code:{phone}', we can try to verify widely.

    # Since standard Django cache doesn't easily support "keys" (wildcard),
    # we might need to access the raw client if possible, OR just ask the user for the phone.
    # However, for the user '0598765432' or the NEW phone, we can guess.

    # Actually, let's try to access the raw redis client if we can.
    # The default django-redis or redis-cache usually exposes the client.

    try:
        # Try to get the raw redis client
        # For 'django.core.cache.backends.redis.RedisCache' (Django 4.0+)
        # It uses redis-py internally.

        # Method 1: direct redis connection using settings
        import redis
        from django.conf import settings

        redis_url = settings.CACHES["default"]["LOCATION"]
        r = redis.from_url(redis_url)

        # Look for our keys
        # Note: Django's RedisCache often adds a prefix. Let's look for *otp:code:*
        keys = r.keys("*otp:code:*")

        if not keys:
            print("No active OTPs found in Redis.")
            return

        print(f"Found {len(keys)} active OTP(s):")
        print("-" * 30)
        for k in keys:
            key_str = k.decode("utf-8")
            # If django adds a prefix like ':1:', we handle it.
            # The value is the OTP.
            val = r.get(key_str)
            if val:
                # Value might be pickle-serialized or just bytes depending on backend config.
                # Django's default RedisCache usually pickles unless configured otherwise?
                # Actually newer Django RedisCache might not pickle integers/strings by default if using the native client,
                # but cache.set normally pickles.

                # Let's try to use django cache get with the key_str if possible,
                # but we need the exact key logic.

                # Simpler: just try to decode val as utf-8 first, if garbage, it's pickled.
                try:
                    otp_val = val.decode("utf-8")
                except:
                    otp_val = "<pickled/binary data>"

                print(f"Key: {key_str} -> Value: {otp_val}")

                # Let's also try via Django cache to be safe
                # Strip prefix if possible?
                # Known prefix is usually ':1:' for version 1.
                # key is like ':1:otp:code:0599...'

                clean_key = key_str
                if ":1:otp:code:" in key_str:
                    clean_key = key_str.split(":1:", 1)[1]
                elif "otp:code:" in key_str:
                    clean_key = key_str  # assumes no prefix or handled

                # print(f"  (Trying cache.get('{clean_key}')...)")
                # cached_val = cache.get(clean_key)
                # print(f"  -> Django Cache returns: {cached_val}")

    except Exception as e:
        print(f"Error inspecting Redis directly: {e}")
        # Fallback: Just try the known demo user phone and some common ones
        phones = ["0598765432"]
        for p in phones:
            val = cache.get(f"otp:code:{p}")
            if val:
                print(f"Phone {p}: {val}")


if __name__ == "__main__":
    list_otps()
