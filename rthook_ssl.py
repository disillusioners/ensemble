"""Runtime hook: point Python's _ssl at the bundled certifi CA bundle.

When frozen by PyInstaller, the _ssl C extension has the build-time
Python's compiled-in default verify paths. On macOS/Linux boxes that
do not have those exact system OpenSSL cert files, SSL verification
fails with CERTIFICATE_VERIFY_FAILED ("self-signed certificate in
certificate chain" = no trusted CA loaded).

This hook runs before run_app.py and sets SSL_CERT_FILE / SSL_CERT_DIR
to the certifi cacert.pem that PyInstaller already bundles (httpx and
slack_sdk both depend on certifi, so it is collected automatically).
"""
import os
import sys

if getattr(sys, "frozen", False):
    try:
        import certifi

        ca_bundle = certifi.where()
        if ca_bundle and os.path.isfile(ca_bundle):
            os.environ["SSL_CERT_FILE"] = ca_bundle
            os.environ["SSL_CERT_DIR"] = os.path.dirname(ca_bundle)
    except Exception:
        pass
