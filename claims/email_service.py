import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def send_email(recipient_email, subject, body):
    """
    Send an email via Gmail SMTP to the client's email address.
    """
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],  # ← goes to client's email
            html_message=body.replace('\n', '<br>'),
            fail_silently=False,
        )
        logger.info("Email sent to %s | Subject: %s", recipient_email, subject)
        return True

    except Exception as e:
        logger.error("Email FAILED to %s | Subject: %s | Error: %s", recipient_email, subject, str(e))
        return False