"""
Celery Async Tasks — Motor Insurance Claims System
Handles all email notifications asynchronously.
"""

from celery import shared_task
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_claim_notification_email(self, recipient_email, subject, notification_type, context):
    """
    Sends an email notification about a claim event.
    Retries up to 3 times with 60-second delays on failure.
    """
    try:
        # Try to render HTML template; fall back to plain text
        try:
            html_content = render_to_string(
                f'emails/{notification_type.lower()}.html', context
            )
        except Exception:
            html_content = None

        plain_body = context.get('body', subject)

        if html_content:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient_email],
            )
            msg.attach_alternative(html_content, 'text/html')
            msg.send()
        else:
            send_mail(
                subject=subject,
                message=plain_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient_email],
                fail_silently=False,
            )

        # Update notification delivery status in DB
        _update_notification_status(context.get('notification_id'), sent=True)
        logger.info(f'Email sent to {recipient_email} | Subject: {subject}')

    except Exception as exc:
        logger.error(f'Email failed to {recipient_email}: {exc}')
        _update_notification_status(context.get('notification_id'), sent=False, error=str(exc))
        raise self.retry(exc=exc)


@shared_task
def send_bulk_notifications(notification_ids):
    """Process a batch of pending notifications."""
    from .models import Notification  # local import to avoid circular deps
    notifications = Notification.objects.filter(
        id__in=notification_ids,
        delivery_status='PENDING'
    ).select_related('recipient', 'claim')

    for notif in notifications:
        send_claim_notification_email.delay(
            recipient_email=notif.recipient.email,
            subject=notif.subject,
            notification_type=notif.notification_type,
            context={'body': notif.body, 'notification_id': str(notif.id)},
        )


def _update_notification_status(notification_id, sent=True, error=''):
    """Helper to update notification delivery status."""
    if not notification_id:
        return
    try:
        from .models import Notification
        notif = Notification.objects.get(id=notification_id)
        if sent:
            notif.mark_sent()
        else:
            notif.mark_failed(error)
    except Exception:
        pass
