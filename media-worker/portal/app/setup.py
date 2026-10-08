"""One-off setup on the server: asks the admin password and prints the .env lines (password hash, session secret,
2FA secret) plus a QR code for Google Authenticator. Nothing is written or sent anywhere.
Usage:  docker compose run --rm portal python -m app.setup"""
import getpass
import secrets
import sys
import urllib.parse

from .auth import hash_password, new_totp_secret


def main() -> None:
    pw = getpass.getpass("Nuova password del portale (min 12 caratteri): ")
    if len(pw) < 12:
        sys.exit("Password troppo corta.")
    if getpass.getpass("Ripeti la password: ") != pw:
        sys.exit("Le password non coincidono.")
    totp = new_totp_secret()
    uri = "otpauth://totp/" + urllib.parse.quote("US VIRAL:admin") + "?" + urllib.parse.urlencode(
        {"secret": totp, "issuer": "US VIRAL", "digits": 6, "period": 30})
    print("\n1) Inquadra questo QR con Google Authenticator (o inserisci a mano la chiave sotto):\n")
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(uri)
        q.print_ascii(invert=True)
    except Exception:
        pass
    print(f"   Chiave manuale: {totp}\n")
    print("2) Aggiungi (o sostituisci) queste righe nel file .env, poi:  docker compose up -d portal\n")
    print(f"PORTAL_PASSWORD_HASH={hash_password(pw)}")
    print(f"PORTAL_SECRET={secrets.token_hex(32)}")
    print(f"PORTAL_TOTP_SECRET={totp}\n")


if __name__ == "__main__":
    main()
