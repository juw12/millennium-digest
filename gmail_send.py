"""Send one email from your Gmail account through Gmail SMTP, with an optional PDF attachment.

The Gmail app password is stored by gmail_setup.ps1, encrypted with Windows DPAPI (only this Windows
user on this PC can decrypt it). It is decrypted in memory for each send and never written in plain text.
"""
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
import os

from settings import EMAIL as GMAIL_ACCOUNT
SENDER_NAME = "Juwen Lie"
SECRET_FILE = Path(os.environ["LOCALAPPDATA"]) / "GmailJuwpegatron" / "app_password.dpapi"


def _app_password():
    if not SECRET_FILE.exists():
        raise RuntimeError("Gmail is not set up yet: run gmail_setup.ps1 first")
    script = (
        f"$s = Get-Content '{SECRET_FILE}' | ConvertTo-SecureString; "
        "[Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
    password = result.stdout.strip().replace(" ", "")
    if not password:
        raise RuntimeError("could not read the stored Gmail app password")
    return password


def send(to, subject, body, attachment=None):
    message = EmailMessage()
    message["From"] = formataddr((SENDER_NAME, GMAIL_ACCOUNT))
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain="gmail.com")
    message.set_content(body)
    if attachment:
        path = Path(attachment)
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as server:
        server.login(GMAIL_ACCOUNT, _app_password())
        server.send_message(message)
    return message["Message-ID"]


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "test":
        send(GMAIL_ACCOUNT, "Gmail setup test", "This test confirms the research outreach tool can send from your Gmail.")
        print("TEST SENT to", GMAIL_ACCOUNT)
